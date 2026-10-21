"""The terminal panel: an xterm attached to a local shell or an Elmer container through a pseudo-terminal bridged on the server loop."""

import asyncio
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, cast

from trame.widgets import html, react

from femforge.common.log import LogLevel, log
from femforge.execution.docker.cli import CONTAINER_PREFIX
from femforge.telemetry.schemas import LogColumn
from femforge.telemetry.tap import LiveLogTap, LogRow
from femforge_workbench.app import guard
from femforge_workbench.feeds import QueueFeed
from femforge_workbench.panels import Cls, ItemField, PanelComponent, labelled, picker
from femforge_workbench.panels.log import BUTTON_SIZE, NEWLINE, mounted
from femforge_workbench.process_tree import ProcessTree
from femforge_workbench.widgets.button import Button
from femforge_workbench.widgets.icon import Icon
from femforge_workbench.widgets.term import Term
from femforge_workbench.widgets.tip import Tip


if TYPE_CHECKING:
    from winpty import PtyProcess

    from femforge_workbench.app import Workbench


READ_SIZE = 1 << 14
DEFAULT_COLS = 80
DEFAULT_ROWS = 24
DOCKER_TIMEOUT_S = 5.0
DOCKER = "docker"
CONTAINER_SHELL = "bash"
LOCAL_LABEL = "local shell"
WINDOWS_SHELLS = ("pwsh.exe", "powershell.exe")
POSIX_SHELL = "/bin/bash"
SHELL_ENV = "SHELL"
EXITED = f"{NEWLINE}[process exited]{NEWLINE}"
WINDOWS = "win32"
PICKER_WIDTH = "w-56"
RESET = "\x1b[0m"

type Spawn = Callable[[Sequence[str], int, int], "Pty"]
"""Starts a command in a pty of ``rows`` by ``cols``."""

type CommandRunner = Callable[[Sequence[str]], str]
"""Runs a command and returns its stdout."""

type Which = Callable[[str], str | None]


class Mode(StrEnum):
    """How the terminal reaches a shell."""

    PTY = "pty"
    """Our bridge over pywinpty."""

    MIRROR = "mirror"
    """No pty available: a read-only mirror of the log tap."""


class TerminalVar(StrEnum):
    """The terminal's per-panel state variables."""

    TARGET = "target"
    TARGET_ITEMS = "target_items"
    OPTIONS = "options"
    STATUS = "status"


class ResizeKey(StrEnum):
    """The keys of the terminal's resize event."""

    COLS = "cols"
    ROWS = "rows"


class LevelColor(StrEnum):
    """The ANSI colour the mirror paints each log level in; the member name is the level it paints."""

    TRACE = "90"
    DEBUG = "90"
    INFO = "37"
    SUCCESS = "32"
    WARNING = "33"
    ERROR = "31"
    CRITICAL = "35"

    @classmethod
    def of(cls, level: str) -> str:
        """The colour a log level is painted in, INFO's for an unknown level."""
        found: LevelColor | None = cls.__members__.get(level.upper())

        return found.value if found is not None else cls.INFO.value


def colored(text: str, color: str) -> str:
    """The text between an ANSI colour escape and the reset the terminal reads."""
    return f"\x1b[{color}m{text}{RESET}"


class Pty(Protocol):
    """A spawned pseudo-terminal the bridge reads, writes, resizes and ends."""

    def read(self, size: int) -> str:
        """Reads at most `size` characters of the child's output."""
        ...

    def write(self, data: str) -> int:
        """Writes `data` to the child's input and returns how much was written."""
        ...

    def setwinsize(self, rows: int, cols: int) -> None:
        """Resizes the terminal to `rows` by `cols`."""
        ...

    def isalive(self) -> bool:
        """Whether the child still runs."""
        ...

    def terminate(self) -> None:
        """Ends the child."""
        ...


@dataclass(frozen=True)
class Target:
    """One thing the terminal can attach to: a label and the command that opens it."""

    label: str
    argv: tuple[str, ...]


def local_shell(*, which: Which = shutil.which) -> tuple[str, ...]:
    """The local shell: pwsh when found, else powershell, on Windows; ``$SHELL`` or bash elsewhere."""
    if sys.platform != WINDOWS:
        return (os.environ.get(SHELL_ENV) or POSIX_SHELL,)

    for name in WINDOWS_SHELLS:
        found = which(name)
        if found is not None:
            return (found,)

    return (WINDOWS_SHELLS[-1],)


def run_command(argv: Sequence[str]) -> str:
    """Runs a command and returns its stdout; raises when it is missing, fails or hangs."""
    completed = subprocess.run(list(argv), capture_output=True, text=True, timeout=DOCKER_TIMEOUT_S, check=True)

    return completed.stdout


def attach_targets(run: CommandRunner = run_command, *, which: Which = shutil.which) -> list[Target]:
    """The local shell, then a ``docker exec`` into every Elmer container; docker missing or not running leaves only the shell."""
    targets = [Target(LOCAL_LABEL, local_shell(which=which))]
    docker = which(DOCKER)
    if docker is None:
        return targets

    try:
        listing = run([docker, "ps", "--filter", f"name={CONTAINER_PREFIX}", "--format", "{{.Names}}"])
    except (OSError, subprocess.SubprocessError) as error:
        log.bind(error=str(error)).info("Docker containers unavailable.")
        return targets

    for name in listing.split():
        if not name.startswith(CONTAINER_PREFIX):
            continue

        targets.append(Target(f"{DOCKER} {name}", (docker, "exec", "-it", name, CONTAINER_SHELL)))

    return targets


class WinPty:
    """A pywinpty process behind the Pty protocol; terminating ends its whole process tree."""

    def __init__(self, process: "PtyProcess", tree: ProcessTree | None) -> None:
        """Wraps a spawned pywinpty process and the tree it was adopted into, None when pywinpty reported no pid."""
        self.process = process
        self.tree = tree

    @classmethod
    def spawn(cls, argv: Sequence[str], rows: int, cols: int) -> "WinPty":
        """Starts a command in a pywinpty pseudo-terminal.

        Args:
            argv: The command and its arguments.
            rows: The terminal height in rows.
            cols: The terminal width in columns.
        """
        from winpty import PtyProcess

        process = PtyProcess.spawn(list(argv), dimensions=(rows, cols))
        pid = process.pid
        tree = ProcessTree.adopt(pid) if pid is not None else None

        return cls(process, tree)

    def read(self, size: int) -> str:
        """Reads at most `size` characters of the child's output."""
        return self.process.read(size)

    def write(self, data: str) -> int:
        """Writes `data` to the child's input and returns how much was written."""
        return self.process.write(data)

    def setwinsize(self, rows: int, cols: int) -> None:
        """Resizes the terminal to `rows` by `cols`."""
        self.process.setwinsize(rows, cols)

    def isalive(self) -> bool:
        """Whether the child still runs."""
        return self.process.isalive()

    def terminate(self) -> None:
        """Kills the child's process tree and closes the pseudo-terminal."""
        if self.tree is not None:
            _ = self.tree.kill()
            self.tree.close()

        with suppress(OSError):
            self.process.close(force=True)


def pty_spawner() -> Spawn | None:
    """The pty factory this platform offers our bridge: pywinpty on Windows, none elsewhere or when it is not installed."""
    if sys.platform != WINDOWS:
        return None

    try:
        import winpty  # noqa: F401
    except ModuleNotFoundError:
        return None

    return WinPty.spawn


class PtyBridge:
    """Pumps a pty's output through ``deliver`` from a reader thread and forwards the terminal's input and resizes to it."""

    def __init__(self, pty: Pty, deliver: Callable[[str], None]) -> None:
        """Starts the reader thread that hands the pty's output to `deliver`."""
        self.pty = pty
        self.deliver = deliver
        self.stopped = False
        self.thread = threading.Thread(target=self.pump, name="terminal-pty", daemon=True)
        self.thread.start()

    def pump(self) -> None:
        """Reads until the pty closes, then reports an exit the panel did not ask for."""
        while True:
            try:
                data = self.pty.read(READ_SIZE)
            except (EOFError, OSError):
                break

            if data:
                self.deliver(data)

        if not self.stopped:
            self.deliver(EXITED)

    def input(self, data: str) -> None:
        """Forwards typed text to a live pty."""
        if self.pty.isalive():
            _ = self.pty.write(data)

    def resize(self, cols: int, rows: int) -> None:
        """Resizes a live pty to a positive size."""
        if cols > 0 and rows > 0 and self.pty.isalive():
            self.pty.setwinsize(rows, cols)

    def stop(self) -> None:
        """Ends the child; the reader thread then sees the pty close without announcing it."""
        self.stopped = True
        self.pty.terminate()


class TerminalPanel(PanelComponent):
    """An interactive shell in the dock: a target select over the local shell and the running Elmer containers, restarted on every switch."""

    def __init__(
        self,
        app: "Workbench",
        id: str,
        title: str,
        *,
        spawn: Spawn | None = None,
        targets: Callable[[], list[Target]] = attach_targets,
        tap: LiveLogTap | None = None,
    ) -> None:
        """Builds the terminal; without a pty spawner it mirrors the log tap read-only.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            spawn: Starts a command in a pty; None takes the platform's, and with none the panel mirrors the tap.
            targets: Lists what the terminal can attach to.
            tap: The log tap the mirror shows; None leaves a mirror blank.
        """
        self.spawn = spawn if spawn is not None else pty_spawner()
        self.list_targets = targets
        self.targets: list[Target] = [Target(LOCAL_LABEL, local_shell())]
        self.terminal: Term | None = None
        self.bridge: PtyBridge | None = None
        self.opened = False
        self.listed = False
        self.size = (DEFAULT_COLS, DEFAULT_ROWS)
        self.mirror: QueueFeed[LogRow] | None = (
            QueueFeed(
                self,
                tap.subscribe,
                tap.unsubscribe,
                self.mirror_rows,
            )
            if tap is not None and self.mode is Mode.MIRROR
            else None
        )

        super().__init__(app, id, title)

        _ = app.ctrl.on_server_exited.add(self.stop)

    @property
    def mode(self) -> Mode:
        """Pty when a spawner is available, else Mirror."""
        return Mode.PTY if self.spawn is not None else Mode.MIRROR

    def build(self) -> None:
        """Builds the target select and the refresh and restart buttons over the terminal."""
        with html.Div(classes=str(Cls.TOOLBAR)):
            with labelled("Attach"):
                picker(self.key(TerminalVar.TARGET), self.key(TerminalVar.TARGET_ITEMS), width=PICKER_WIDTH, disabled=self.mode is not Mode.PTY)

            with (
                Tip(content="Look for Elmer containers again"),
                Button(
                    size=BUTTON_SIZE,
                    variant="ghost",
                    aria_label="Refresh the targets",
                    on_click=self.callback(self.refresh_targets),
                ),
            ):
                Icon("refresh-cw")

            with (
                Tip(content="Restart the shell"),
                Button(
                    size=BUTTON_SIZE,
                    variant="ghost",
                    aria_label="Restart the shell",
                    disabled=self.mode is not Mode.PTY,
                    on_click=self.callback(self.restart),
                ),
            ):
                Icon("play")

            html.Span([react.Bind(self.key(TerminalVar.STATUS))], classes=str(Cls.STATUS))

        with html.Div(classes=str(Cls.FILL)):
            self.terminal = Term(
                options=react.Bind(self.key(TerminalVar.OPTIONS)),
                on_data=self.callback(self.on_input, args="[$event]"),
                on_resize=self.callback(self.on_resize, args="[$event]"),
            )
            mounted(self.callback(self.on_opened))

        self.state.update(
            {
                self.key(TerminalVar.TARGET): LOCAL_LABEL,
                self.key(TerminalVar.TARGET_ITEMS): self.target_items(),
                self.key(TerminalVar.OPTIONS): self.options(),
                self.key(TerminalVar.STATUS): self.describe(),
            }
        )
        _ = self.listen(self.key(TerminalVar.TARGET))(self.on_target)

    def options(self) -> dict[str, object]:
        """xterm options: interactive unless mirroring, palette colours."""
        return {
            "disableStdin": self.mode is Mode.MIRROR,
            "cursorBlink": self.mode is not Mode.MIRROR,
            "convertEol": self.mode is Mode.MIRROR,
            "theme": self.app.palette.xterm_theme(),
        }

    def describe(self) -> str:
        """The status text: the target's label, or why the log is mirrored."""
        match self.mode:
            case Mode.PTY:
                return self.target().label
            case Mode.MIRROR:
                return "no pty available; mirroring the log"

    def target_items(self) -> list[dict[str, object]]:
        """The target select's items, one per target."""
        return [{ItemField.TITLE: target.label, ItemField.VALUE: target.label} for target in self.targets]

    def target(self) -> Target:
        """The target the select names, the local shell when it names none."""
        label = str(self.state[self.key(TerminalVar.TARGET)] or LOCAL_LABEL) if self.state.has(self.key(TerminalVar.TARGET)) else LOCAL_LABEL
        found = next((target for target in self.targets if target.label == label), None)

        return found if found is not None else self.targets[0]

    async def refresh_targets(self, **_: object) -> None:
        """Lists the containers off the loop and offers them beside the local shell."""
        self.targets = await asyncio.to_thread(self.list_targets)
        with self.state:
            self.state[self.key(TerminalVar.TARGET_ITEMS)] = self.target_items()

        log.bind(panel=self.id, targets=[target.label for target in self.targets]).info("Attach targets listed.")

    def deliver(self, text: str) -> None:
        """Hands pty output to the client from the reader thread; a stopped server drops it."""
        with suppress(RuntimeError):
            self.app.marshal(self.write, text)

    def write(self, text: str) -> None:
        """Writes `text` to the terminal."""
        if self.terminal is not None:
            self.terminal.write(text)

    @guard
    def start(self) -> None:
        """Spawns the target in a pty sized like the client's terminal and bridges it."""
        if self.spawn is None or self.bridge is not None:
            return

        target = self.target()
        cols, rows = self.size
        self.bridge = PtyBridge(self.spawn(target.argv, rows, cols), self.deliver)
        self.state[self.key(TerminalVar.STATUS)] = target.label

        log.bind(panel=self.id, target=target.label, argv=list(target.argv), cols=cols, rows=rows).info("Terminal started.")

    def stop(self, **_: object) -> None:
        """Ends the child, if any."""
        if self.bridge is None:
            return

        self.bridge.stop()
        self.bridge = None

        log.bind(panel=self.id).info("Terminal stopped.")

    def dispose(self) -> None:
        """Stops the child and the mirror, then releases the panel."""
        self.ctrl.on_server_exited.discard(self.stop)
        self.stop()
        if self.mirror is not None:
            self.mirror.stop()

        super().dispose()

    @guard
    def restart(self) -> None:
        """Ends the child and starts the selected target, once a client has a terminal to show it in."""
        self.stop()
        if self.terminal is not None and self.opened:
            self.terminal.clear()
            self.start()

    def on_target(self, **_: object) -> None:
        """Restarts the shell on the target just selected."""
        self.state[self.key(TerminalVar.STATUS)] = self.describe()
        self.restart()

    @guard
    def on_opened(self) -> None:
        """A client's terminal mounted: it fits its box and, in pty mode, gets the shell; the first mount also lists the containers."""
        self.opened = True
        if self.terminal is not None:
            self.terminal.fit()

        if self.mode is Mode.PTY:
            self.start()

        if not self.listed and self.app.loop is not None:
            self.listed = True
            _ = asyncio.create_task(self.refresh_targets())

    def on_input(self, data: object) -> None:
        """Forwards typed text to the shell."""
        if isinstance(data, str) and self.bridge is not None:
            self.bridge.input(data)

    def on_resize(self, event: object) -> None:
        """The client's terminal changed size; the pty follows."""
        if not isinstance(event, dict):
            return

        payload = cast("dict[str, object]", event)
        cols = payload.get(ResizeKey.COLS)
        rows = payload.get(ResizeKey.ROWS)
        if not isinstance(cols, int) or not isinstance(rows, int):
            return

        self.size = (cols, rows)
        if self.bridge is not None:
            self.bridge.resize(cols, rows)

    def mirror_rows(self, rows: list[LogRow]) -> None:
        """Writes tap records into the read-only terminal, coloured by level."""
        for row in rows:
            level = str(row.get(LogColumn.LEVEL) or LogLevel.INFO)
            line = f"{row.get(LogColumn.TIME)} {level:<8} {row.get(LogColumn.MESSAGE)}"
            self.write(colored(line, LevelColor.of(level)) + NEWLINE)

    def on_layout_changed(self) -> None:
        """Fits the terminal after the dock re-laid out."""
        if self.terminal is not None:
            self.terminal.fit()

    def on_visible(self, visible: bool) -> None:
        """Fits the terminal when shown; the mirror runs only while visible, a shell keeps running hidden."""
        if visible and self.terminal is not None:
            self.terminal.fit()

        if self.mirror is None:
            return

        if visible:
            self.mirror.start()
            return

        self.mirror.stop()
