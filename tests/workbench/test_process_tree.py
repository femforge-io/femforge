"""ProcessTree on faked platform calls: a POSIX process group end to end and a Windows job that fails to form."""

import os
import runpy
import signal
import sys
from collections.abc import Iterator
from dataclasses import dataclass

import psutil
import pytest

from femforge_workbench import process_tree
from femforge_workbench.process_tree import ProcessTree


LEADER = 100
SIGKILL = 9


@dataclass
class _Process:
    pid: int
    state: str = psutil.STATUS_RUNNING

    def status(self) -> str:
        return self.state


class _Posix:
    """A process table and the group calls over it, installed in place of the platform's."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, groups: dict[int, int], zombies: frozenset[int] = frozenset()) -> None:
        self.groups = groups
        self.zombies = zombies
        self.signals: list[tuple[int, int]] = []
        self.failure: type[OSError] | None = None
        monkeypatch.setattr(process_tree, "WINDOWS", False)
        monkeypatch.setattr(signal, "SIGKILL", SIGKILL, raising=False)
        monkeypatch.setattr(os, "getpgid", self.getpgid, raising=False)
        monkeypatch.setattr(os, "killpg", self.killpg, raising=False)
        monkeypatch.setattr(psutil, "process_iter", self.process_iter)

    def getpgid(self, pid: int) -> int:
        if pid not in self.groups:
            raise ProcessLookupError(pid)

        return self.groups[pid]

    def killpg(self, pgid: int, signum: int) -> None:
        self.signals.append((pgid, signum))
        if self.failure is not None:
            raise self.failure(pgid)

    def process_iter(self) -> Iterator[_Process]:
        statuses = {pid: psutil.STATUS_ZOMBIE if pid in self.zombies else psutil.STATUS_RUNNING for pid in self.groups}

        return iter([_Process(pid, state) for pid, state in statuses.items()] + [_Process(999)])


def test_posix_adopts_only_a_group_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _Posix(monkeypatch, {LEADER: LEADER, 101: LEADER})

    assert ProcessTree.adopt(LEADER) == ProcessTree(LEADER)
    with pytest.raises(ValueError, match="does not lead its own group"):
        _ = ProcessTree.adopt(101)


def test_posix_kill_terms_the_group_then_kills_what_is_left_and_counts_the_live_members(monkeypatch: pytest.MonkeyPatch) -> None:
    posix = _Posix(monkeypatch, {LEADER: LEADER, 101: LEADER, 102: LEADER, 200: 200}, zombies=frozenset({102}))
    posix.failure = ProcessLookupError

    assert ProcessTree(LEADER).kill(grace=0.06) == 2
    assert posix.signals == [(LEADER, signal.SIGTERM), (LEADER, SIGKILL)]


def test_posix_kill_and_close_of_an_empty_group_signal_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    posix = _Posix(monkeypatch, {200: 200})
    tree = ProcessTree(LEADER)

    assert tree.kill() == 0
    tree.close()
    assert posix.signals == []


def test_posix_close_kills_a_group_still_running(monkeypatch: pytest.MonkeyPatch) -> None:
    posix = _Posix(monkeypatch, {LEADER: LEADER})

    ProcessTree(LEADER).close()

    assert posix.signals == [(LEADER, SIGKILL)]


def test_posix_interrupt_breaks_the_group_and_reports_a_failed_send(monkeypatch: pytest.MonkeyPatch) -> None:
    posix = _Posix(monkeypatch, {LEADER: LEADER})
    tree = ProcessTree(LEADER)

    assert tree.interrupt()
    posix.failure = PermissionError
    assert not tree.interrupt()
    assert posix.signals == [(LEADER, process_tree.BREAK_SIGNAL)] * 2


def test_posix_breaks_with_sigterm_and_needs_no_console_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    names = runpy.run_path(process_tree.__file__, run_name="process_tree_on_posix")

    assert (names["BREAK_SIGNAL"], names["NEW_GROUP"]) == (signal.SIGTERM, 0)


@pytest.mark.skipif(not process_tree.WINDOWS, reason="the Win32 error type exists on Windows only")
def test_a_job_that_fails_to_form_terminates_the_suspended_child(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = 7
    calls: list[tuple[str, tuple[object, ...]]] = []

    def win32(name: str, *args: object) -> int:
        calls.append((name, args))

        return handle if name == "OpenProcess" else 0

    monkeypatch.setattr(process_tree, "_win32", win32)

    with pytest.raises(OSError, match="CreateJobObjectW"):
        _ = ProcessTree.adopt(LEADER)

    assert [name for name, _ in calls] == ["OpenProcess", "CreateJobObjectW", "TerminateProcess", "CloseHandle"]
    assert calls[2][1] == (handle, 1) and calls[3][1] == (handle,)
