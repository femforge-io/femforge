"""The dev loop for the shell core: a supervisor that reruns the server after every save of a core module, and the page script that follows it back and reloads on a rebuilt dist."""

import json
import os
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

import femforge_workbench
from femforge.common.log import log
from femforge_workbench.process_tree import BREAK_SIGNAL, CREATION_FLAGS, NEW_GROUP, NEW_SESSION, ProcessTree
from femforge_workbench.reload import SaveWatcher, package_root, shell_files
from femforge_workbench.widgets.module import ROUTE, VERSION_FILE, assets


DEV_FLAG = "--dev"
DEV_ENV = "FEMFORGE_WORKBENCH_DEV"
"""Set in the supervised server's environment; the shell then serves the self-reload script."""

POLL_SECONDS = 0.25
"""How often the supervisor looks at its child between saves; a bare wait would hold Ctrl-C on Windows."""

PROBE_MS = 1000
PROBE_PAGE = "index.html"
"""Probed beside the page's own URL: the root answers HEAD with 403, the page itself with 200."""

LIBRARY_GRACE_MS = 2000
"""How long after load the page waits for the widget library before reloading once more."""

STOP_GRACE = 5.0
"""Seconds a child gets to exit on its own after the break, so its exit hooks flush the persisted state."""


def reload_script() -> str:
    """Probes the server and the dist each second: the page reloads when the server comes back after a restart, when version.json changes, and when the library failed to install."""
    version_url = f"/{ROUTE}/{VERSION_FILE}"

    return (
        "(function follow() {"
        " let down = false;"
        " let version = null;"
        f" const assets = {json.dumps(assets())};"
        " async function refresh() {"
        "  await Promise.all(assets.map((asset) => fetch(asset, { cache: 'reload' })));"
        "  location.reload();"
        " }"
        " setInterval(async () => {"
        "  try {"
        f"   const response = await fetch(new URL('{PROBE_PAGE}', location.href), {{ method: 'HEAD', cache: 'no-store' }});"
        "   if (!response.ok) { throw new Error(String(response.status)); }"
        "   if (down) { location.reload(); return; }"
        f"   const probe = await fetch('{version_url}', {{ cache: 'no-store' }});"
        "   if (!probe.ok) { return; }"
        "   const current = await probe.text();"
        "   if (version === null) { version = current; }"
        "   else if (current !== version) { version = current; await refresh(); }"
        "  } catch (error) { down = true; }"
        f" }}, {PROBE_MS});"
        f" setTimeout(() => {{ if (!window.Ff) {{ refresh(); }} }}, {LIBRARY_GRACE_MS});"
        "})();"
    )


def without_dev(argv: Sequence[str]) -> list[str]:
    """The arguments without the dev flag."""
    return [arg for arg in argv if arg != DEV_FLAG]


def child_command(module: str, argv: Sequence[str]) -> list[str]:
    """The server as `python -m module`, given the arguments without the dev flag."""
    return [sys.executable, "-m", module, *without_dev(argv)]


class Supervisor:
    """Runs a command as a child process and restarts it after every burst of .py saves under the roots, or of the `files` when given."""

    def __init__(self, command: Sequence[str], roots: Sequence[Path], *, files: Sequence[Path] | None = None) -> None:
        """Sets the supervisor up; `serve` runs it.

        Args:
            command: The server command the child runs.
            roots: The directories whose .py saves restart the child.
            files: The only files whose saves restart the child; None counts every .py file under the roots.
        """
        self.command = list(command)
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.watcher = SaveWatcher(roots, self.wake.set, files=files)
        self.child: subprocess.Popen[bytes] | None = None
        self.tree: ProcessTree | None = None
        self.starts = 0

    def spawn(self) -> subprocess.Popen[bytes]:
        """Starts the child in its own process group and tree, flagged as supervised."""
        self.release()
        child = subprocess.Popen(
            self.command,
            env={**os.environ, DEV_ENV: "1"},
            creationflags=NEW_GROUP | CREATION_FLAGS,
            start_new_session=NEW_SESSION,
        )
        self.tree = ProcessTree.adopt(child.pid)
        self.child = child
        self.starts += 1
        log.bind(pid=child.pid, command=self.command, starts=self.starts).info("Server started.")

        return child

    def stop_child(self, child: subprocess.Popen[bytes]) -> None:
        """Asks the child to exit, so its exit hooks flush the persisted state, and kills its tree when it does not within the grace."""
        child.send_signal(BREAK_SIGNAL)
        try:
            _ = child.wait(timeout=STOP_GRACE)
        except subprocess.TimeoutExpired:
            if self.tree is not None:
                _ = self.tree.kill()

            log.bind(pid=child.pid).warning("Server did not stop on the break; killed.")

    def release(self) -> None:
        """Closes the child's tree once the child is gone, which also ends anything the child left behind."""
        if self.tree is not None:
            self.tree.close()
            self.tree = None

    def await_save(self, child: subprocess.Popen[bytes]) -> None:
        """Returns at the next save or stop, noting once when the child exits on its own meanwhile."""
        self.wake.clear()
        exited = False
        while not self.wake.wait(POLL_SECONDS):
            if self.stopping.is_set():
                return

            if not exited and child.poll() is not None:
                exited = True
                log.bind(returncode=child.returncode).warning("Server exited; waiting for a save.")

    def serve(self) -> None:
        """Runs the child until Ctrl-C or `stop`, restarting it after each save."""
        self.watcher.start()
        try:
            while not self.stopping.is_set():
                child = self.spawn()
                self.await_save(child)
                if child.poll() is None:
                    self.stop_child(child)

                self.release()
        except KeyboardInterrupt:
            pass
        finally:
            if self.child is not None and self.child.poll() is None:
                self.stop_child(self.child)

            self.release()
            self.watcher.stop()
            log.info("Supervisor stopped.")

    def stop(self) -> None:
        """Ends `serve`, which then stops the child."""
        self.stopping.set()
        self.wake.set()


def supervise(module: str, argv: Sequence[str]) -> None:
    """Serves `python -m module argv` under a supervisor restarting it on a save of a shell core module, until Ctrl-C; every other save rebuilds in process."""
    Supervisor(child_command(module, argv), [package_root(femforge_workbench.__name__)], files=shell_files()).serve()
