"""Streamed commands: run_streaming against real python children, its timeouts, cancels and kill hook, and the caller's log context."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from femforge.common.log import log
from femforge.execution.streaming import CommandError, CommandResult, require_success, run_streaming


def _child(code: str) -> list[str]:
    """A python child process printing/behaving per ``code``."""
    return [sys.executable, "-u", "-c", code]


def test_run_streaming_tees_to_log_and_callback_in_order(tmp_path: Path) -> None:
    lines: list[str] = []
    result = run_streaming(_child("print('alpha'); print('beta'); print('gamma')"), cwd=tmp_path, log_path=tmp_path / "out.log", on_line=lines.append)
    assert result.returncode == 0
    assert result.timed_out is False
    assert result.duration_s >= 0.0
    assert result.log_path == tmp_path / "out.log"
    assert [line.rstrip("\n") for line in lines] == ["alpha", "beta", "gamma"]
    assert (tmp_path / "out.log").read_text(encoding="utf-8") == "alpha\nbeta\ngamma\n"


def test_run_streaming_reports_nonzero_exit_without_check(tmp_path: Path) -> None:
    result = run_streaming(_child("import sys; sys.exit(3)"), cwd=tmp_path, log_path=tmp_path / "out.log")
    assert result.returncode == 3
    assert result.timed_out is False


def test_run_streaming_check_raises_with_log_tail(tmp_path: Path) -> None:
    with pytest.raises(CommandError) as excinfo:
        _ = run_streaming(_child("import sys; print('boom detail'); sys.exit(2)"), cwd=tmp_path, log_path=tmp_path / "out.log", check=True)
    assert excinfo.value.returncode == 2
    assert "boom detail" in excinfo.value.log_tail
    assert "boom detail" in str(excinfo.value)


def test_run_streaming_timeout_kills_the_child(tmp_path: Path) -> None:
    start: float = time.monotonic()
    result = run_streaming(
        _child("import time; print('started', flush=True); time.sleep(60)"), cwd=tmp_path, log_path=tmp_path / "out.log", timeout_s=2.0
    )
    assert result.timed_out is True
    assert result.returncode != 0
    assert time.monotonic() - start < 30.0
    assert "started" in (tmp_path / "out.log").read_text(encoding="utf-8")


def test_run_streaming_timeout_calls_custom_kill_and_check_raises(tmp_path: Path) -> None:
    killed: list[str] = []

    def _kill() -> None:
        killed.append("killed")

    with pytest.raises(CommandError, match="timeout"):
        _ = run_streaming(_child("import time; time.sleep(60)"), cwd=tmp_path, log_path=tmp_path / "out.log", timeout_s=1.0, check=True, kill=_kill)
    assert killed == ["killed"]


def test_a_set_cancel_token_stops_the_child_and_calls_the_kill_hook(tmp_path: Path) -> None:
    cancel = threading.Event()
    killed: list[str] = []

    def on_line(line: str) -> None:
        if "started" in line:
            cancel.set()

    def kill() -> None:
        killed.append("container")

    start: float = time.monotonic()

    result = run_streaming(
        _child("import time; print('started', flush=True); time.sleep(60)"),
        cwd=tmp_path,
        log_path=tmp_path / "out.log",
        on_line=on_line,
        kill=kill,
        cancel=cancel,
    )

    assert result.cancelled is True and result.timed_out is False
    assert result.returncode != 0
    assert killed == ["container"]
    assert time.monotonic() - start < 30.0


def test_a_cancelled_command_raises_under_check(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(CommandError, match="after cancellation"):
        _ = run_streaming(
            _child("import time; time.sleep(60)"),
            cwd=tmp_path,
            log_path=tmp_path / "out.log",
            check=True,
            cancel=cancel,
        )


def test_an_unset_cancel_token_lets_the_command_finish(tmp_path: Path) -> None:
    result = run_streaming(_child("print('done')"), cwd=tmp_path, log_path=tmp_path / "out.log", cancel=threading.Event())

    assert result.returncode == 0 and result.cancelled is False


def test_run_streaming_gives_the_child_the_given_environment(tmp_path: Path) -> None:
    env: dict[str, str] = {**os.environ, "FEMFORGE_STREAMING_PROBE": "seen"}

    result = run_streaming(_child("import os; print(os.environ['FEMFORGE_STREAMING_PROBE'])"), cwd=tmp_path, log_path=tmp_path / "out.log", env=env)

    assert result.returncode == 0
    assert (tmp_path / "out.log").read_text(encoding="utf-8") == "seen\n"


def test_require_success_passes_a_clean_exit_through(tmp_path: Path) -> None:
    result = CommandResult(returncode=0, duration_s=0.0, log_path=tmp_path / "out.log", timed_out=False)

    assert require_success(result, "tool") is result


def test_streamed_lines_keep_the_callers_run_and_telemetry_route(tmp_path: Path) -> None:
    """The reader thread runs in a copy of the caller's context, so a streamed line lands in the caller's root under its run_id."""
    from femforge.common.log import _reset_for_tests, telemetry_scope
    from femforge.telemetry.paths import Telemetry
    from femforge.telemetry.schemas import LogColumn
    from femforge.telemetry.sink import scan_logs

    def on_line(line: str) -> None:
        log.bind(text=line.strip()).info("Streamed line.")

    _reset_for_tests()
    try:
        with telemetry_scope(tmp_path / "telemetry"), log.contextualize(run_id="run-1"):
            _ = run_streaming(
                [sys.executable, "-c", "print('solver says hi')"],
                cwd=tmp_path,
                log_path=tmp_path / "out.log",
                on_line=on_line,
            )

        rows = scan_logs(Telemetry(tmp_path / "telemetry")).collect()
    finally:
        _reset_for_tests()

    streamed = rows.filter(rows.get_column(LogColumn.MESSAGE) == "Streamed line.")
    assert streamed.get_column(LogColumn.RUN_ID).to_list() == ["run-1"]
