"""One child process and everything it starts, ended together: a job object on Windows, a process group elsewhere."""

import ctypes
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from functools import cache
from typing import Final, cast

import psutil


WINDOWS: Final = sys.platform == "win32"
"""Whether a tree is a job object (Windows) or a process group (elsewhere)."""

CREATION_FLAGS: Final = 0x00000004 if WINDOWS else 0
"""creationflags for Popen: CREATE_SUSPENDED on Windows, so the child waits until it is in its job and nothing it starts can escape; 0 elsewhere."""

NEW_SESSION: Final = not WINDOWS
"""start_new_session for Popen: on POSIX the child leads a process group of its own, whose id is its pid."""

if sys.platform == "win32":
    BREAK_SIGNAL = signal.CTRL_BREAK_EVENT
    NEW_GROUP = subprocess.CREATE_NEW_PROCESS_GROUP
    """The child gets its own console group, so the break reaches it and not its parent."""
else:
    BREAK_SIGNAL = signal.SIGTERM
    NEW_GROUP = 0

KILL_GRACE: Final = 2.0
"""Seconds a terminated tree gets before what is left is killed."""

POLL: Final = 0.05
"""Seconds between looks at a tree that was asked to exit."""

_KILL_ON_JOB_CLOSE = 0x2000
_BASIC_ACCOUNTING = 1
_EXTENDED_LIMITS = 9
_SET_QUOTA_AND_TERMINATE = 0x0100 | 0x0001


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimits(ctypes.Structure):
    LimitFlags: int
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimits(ctypes.Structure):
    BasicLimitInformation: _BasicLimits
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _BasicAccounting(ctypes.Structure):
    ActiveProcesses: int
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


@cache
def _kernel32() -> "ctypes.WinDLL":
    """The kernel32 bindings, loaded on first use so the module imports on every platform."""
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _win32(name: str, *args: object) -> int:
    """Calls a kernel32 function and returns its result."""
    return cast("int", getattr(_kernel32(), name)(*args))


def _checked(name: str, *args: object) -> int:
    """Calls a kernel32 function that reports failure with a zero result and raises the Win32 error for it."""
    result = _win32(name, *args)
    if not result:
        raise ctypes.WinError(ctypes.get_last_error(), name)

    return result


@dataclass(slots=True)
class ProcessTree:
    """One process and every process it starts: a job object on Windows, the process group the leader heads elsewhere."""

    pid: int
    """The process the tree was built around; on POSIX also the group id."""

    job: int = 0
    """The Windows job handle; 0 on POSIX, where a group needs none."""

    @classmethod
    def adopt(cls, pid: int) -> "ProcessTree":
        """Takes a process spawned with `CREATION_FLAGS` and `NEW_SESSION`: into a new job and let run on Windows, checked to lead its own group elsewhere."""
        if not WINDOWS:
            if os.getpgid(pid) != pid:
                raise ValueError(f"process {pid} does not lead its own group")

            return cls(pid)

        process = _checked("OpenProcess", _SET_QUOTA_AND_TERMINATE, False, pid)
        try:
            job = _checked("CreateJobObjectW", None, None)
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
            _ = _checked("SetInformationJobObject", job, _EXTENDED_LIMITS, ctypes.byref(limits), ctypes.sizeof(limits))
            _ = _checked("AssignProcessToJobObject", job, process)
        except OSError:
            _ = _win32("TerminateProcess", process, 1)
            raise
        finally:
            _ = _win32("CloseHandle", process)

        psutil.Process(pid).resume()

        return cls(pid, job)

    def kill(self, grace: float = KILL_GRACE) -> int:
        """Ends every process in the tree and returns how many there were; POSIX asks first and kills what is left after `grace` seconds, Windows has no gentle signal."""
        if WINDOWS:
            accounting = _BasicAccounting()
            _ = _checked("QueryInformationJobObject", self.job, _BASIC_ACCOUNTING, ctypes.byref(accounting), ctypes.sizeof(accounting), None)
            _ = _checked("TerminateJobObject", self.job, 1)

            return accounting.ActiveProcesses

        held = len(self._members())
        if held == 0:
            return 0

        with suppress(ProcessLookupError):
            os.killpg(self.pid, signal.SIGTERM)

        deadline = time.monotonic() + grace
        while self._members() and time.monotonic() < deadline:
            time.sleep(POLL)

        with suppress(ProcessLookupError):
            os.killpg(self.pid, signal.SIGKILL)

        return held

    def interrupt(self) -> bool:
        """Sends `BREAK_SIGNAL` to the tree's process group, which a child spawned with `NEW_GROUP` leads; False when it cannot be sent, as without a console."""
        try:
            if WINDOWS:
                os.kill(self.pid, BREAK_SIGNAL)
            else:
                os.killpg(self.pid, BREAK_SIGNAL)
        except OSError:
            return False

        return True

    def close(self) -> None:
        """Ends anything still in the tree and releases it: a job dies with its handle, a group gets SIGKILL."""
        if WINDOWS:
            _ = _win32("CloseHandle", self.job)

            return

        if self._members():
            with suppress(ProcessLookupError):
                os.killpg(self.pid, signal.SIGKILL)

    def _members(self) -> list[int]:
        found: list[int] = []
        for process in psutil.process_iter():
            with suppress(psutil.Error, ProcessLookupError, PermissionError):
                if os.getpgid(process.pid) == self.pid and process.status() != psutil.STATUS_ZOMBIE:
                    found.append(process.pid)

        return found
