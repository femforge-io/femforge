"""Streamed external commands: run one, teeing its output to a log file and a per-line callback as it is produced."""

from __future__ import annotations

import contextvars
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import IO, Protocol

from femforge.common.log import log


__all__ = ["CancelToken", "CommandError", "CommandResult", "LineSink", "log_tail", "require_success", "run_streaming"]

POLL_S = 0.1
"""`t` `[s]`: How often a command with a timeout or a cancel token checks on both."""

type LineSink = Callable[[str], None]
"""Receives each line of a command's merged output as the command produces it."""


class CancelToken(Protocol):
    """A flag a caller sets to stop a running command; ``threading.Event`` is one."""

    def is_set(self) -> bool:
        """Whether the caller has asked the command to stop."""
        ...


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one streamed external command."""

    returncode: int
    duration_s: float
    log_path: Path
    timed_out: bool
    cancelled: bool = False


class CommandError(RuntimeError):
    """An external command failed; carries the exit code and the tail of its log."""

    def __init__(self, message: str, *, returncode: int, log_tail: str = "") -> None:
        """Build the error from ``message`` with the log tail appended.

        Args:
            message: What failed.
            returncode: The command's exit code.
            log_tail: The end of the command's log, appended to the message when not empty.
        """
        super().__init__(f"{message}\n{log_tail}" if log_tail else message)
        self.returncode: int = returncode
        self.log_tail: str = log_tail


def log_tail(log_path: Path, limit: int = 2000) -> str:
    """The last ``limit`` characters of a command's log, for error messages."""
    return log_path.read_text(encoding="utf-8", errors="replace")[-limit:]


def require_success(result: CommandResult, program: str) -> CommandResult:
    """``result`` when the command exited cleanly, else a [`CommandError`][] naming ``program`` with the tail of its log."""
    if result.returncode == 0 and not result.timed_out and not result.cancelled:
        return result

    suffix: str = " after timeout" if result.timed_out else " after cancellation" if result.cancelled else ""
    raise CommandError(
        f"{program} exited with code {result.returncode}{suffix}",
        returncode=result.returncode,
        log_tail=log_tail(result.log_path),
    )


def run_streaming(
    args: list[str],
    *,
    cwd: Path,
    log_path: Path,
    on_line: LineSink | None = None,
    timeout_s: float | None = None,
    check: bool = False,
    kill: Callable[[], None] | None = None,
    cancel: CancelToken | None = None,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run ``args`` in ``cwd``, teeing each merged stdout/stderr line to ``log_path`` and ``on_line`` as it is produced.

    Args:
        args: The command and its arguments.
        cwd: The working directory.
        log_path: The file the merged output is written to.
        on_line: Receives each output line as it arrives.
        timeout_s: Seconds before the command is stopped; None waits for it to exit.
        check: Raise [`CommandError`][] unless the command exits cleanly.
        kill: Stops what the command started, such as a container, before the command itself is killed.
        cancel: Stops the command once it is set.
        env: The command's environment; None inherits this process's.
    """
    start: float = time.monotonic()

    program_name: str = Path(args[0]).name
    log.info(f"Running {program_name}.", args=args, cwd=str(cwd))

    proc: subprocess.Popen[str] = subprocess.Popen(
        args,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=dict(env) if env is not None else None,
    )

    def _pump() -> None:
        stdout: IO[str] | None = proc.stdout
        assert stdout is not None  # stdout=PIPE always yields a stream
        with log_path.open("w", encoding="utf-8", buffering=1) as sink:
            for line in stdout:
                _ = sink.write(line)
                if on_line is not None:
                    on_line(line)

    # A new thread starts with an empty context; the copy carries the caller's run_id and telemetry route to the streamed lines.
    reader = Thread(target=contextvars.copy_context().run, args=(_pump,), daemon=True)
    reader.start()

    poll_s: float | None = POLL_S if timeout_s is not None or cancel is not None else None
    timed_out = False
    cancelled = False
    returncode: int | None = None
    while returncode is None:
        try:
            returncode = proc.wait(timeout=poll_s)
        except subprocess.TimeoutExpired:
            cancelled = cancel is not None and cancel.is_set()
            timed_out = not cancelled and timeout_s is not None and time.monotonic() - start >= timeout_s
            if not (cancelled or timed_out):
                continue

            if kill is not None:
                kill()

            # Always also kill the client process: a Docker-side kill stops the container, but a wedged client would hang wait().
            proc.kill()
            returncode = proc.wait()

    reader.join()

    result = CommandResult(
        returncode=returncode,
        duration_s=time.monotonic() - start,
        log_path=log_path,
        timed_out=timed_out,
        cancelled=cancelled,
    )
    log.info(
        f"Finished {program_name}.",
        program=program_name,
        duration_s=result.duration_s,
        returncode=returncode,
        timed_out=timed_out,
        cancelled=cancelled,
    )

    if check:
        return require_success(result, program_name)

    return result
