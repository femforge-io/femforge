"""The dev supervisor's edges: a stop while it waits, a child that exits on its own, Ctrl-C and the shell-core watch."""

import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

import femforge_workbench
from femforge_workbench import dev
from femforge_workbench.dev import Supervisor, supervise
from femforge_workbench.reload import package_root, shell_files


class _ExitedChild:
    """A child that has already exited; looking at it asks the supervisor to stop."""

    def __init__(self, supervisor: Supervisor) -> None:
        self.supervisor = supervisor
        self.returncode = 3
        self.polls = 0

    def poll(self) -> int:
        self.polls += 1
        self.supervisor.stopping.set()

        return self.returncode


def test_waiting_notes_a_child_that_exits_and_returns_on_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev, "POLL_SECONDS", 0.01)
    supervisor = Supervisor([sys.executable, "-c", "pass"], [tmp_path])
    child = _ExitedChild(supervisor)

    supervisor.await_save(cast("subprocess.Popen[bytes]", child))

    assert child.polls == 1


def test_ctrl_c_stops_the_supervisor_and_its_watcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor = Supervisor([sys.executable, "-c", "pass"], [tmp_path])

    def interrupted() -> subprocess.Popen[bytes]:
        raise KeyboardInterrupt

    monkeypatch.setattr(supervisor, "spawn", interrupted)

    supervisor.serve()

    assert supervisor.child is None and supervisor.watcher.observer is None


def test_supervise_runs_the_module_and_watches_the_shell_core_only(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[Supervisor] = []

    def serve(self: Supervisor) -> None:
        served.append(self)

    monkeypatch.setattr(Supervisor, "serve", serve)

    supervise("femforge_workbench.cli", ["demo", dev.DEV_FLAG])

    [supervisor] = served
    assert supervisor.command == [sys.executable, "-m", "femforge_workbench.cli", "demo"]
    assert supervisor.watcher.roots == [package_root(femforge_workbench.__name__)]
    assert supervisor.watcher.files == {file.resolve() for file in shell_files()}
