"""solve_case: a backend that only relays lines still yields the parsed events, logged convergence and convergence.jsonl."""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from femforge.common.log import log
from femforge.elmer.runtime.backend import ExecutionBackend
from femforge.elmer.runtime.solve import solve_case
from femforge.elmer.solver_log import ConvergenceSample, ConvergenceTargets, LoopTargets, SolveFinished, SolverEvent, SolverMessage
from femforge.execution.streaming import CancelToken, CommandResult, LineSink

FIXTURE_LINES: list[str] = [
    "MAIN: Loading input file",
    "ComputeChange: NS (ITER=1) (NRM,RELC): ( 1.23E+00 4.56E-03 ) :: mgdyn",
    "WARNING:: Solver: something looks off",
    "ComputeChange: SS (ITER=1) (NRM,RELC): ( 1.10E+00 1.00E-08 ) :: mgdyn",
    "ERROR:: Solver: something broke",
    "SOLVER TOTAL TIME(CPU,REAL):  4.55  5.12",
]


class _ScriptedBackend(ExecutionBackend):
    """Relays canned solver lines through ``on_line`` and returns a fixed exit, the way a remote backend relays a stream."""

    def __init__(self, lines: list[str], returncode: int = 0) -> None:
        self.lines: list[str] = lines
        self.returncode: int = returncode
        self.calls: list[tuple[Path, int, float | None, CancelToken | None]] = []

    @property
    def partitioned(self) -> bool:
        return True

    def grid_convert(
        self,
        mesh_dir: Path,
        msh_name: str,
        *,
        out_dir: Path,
        scale: float | None = None,
        autoclean: bool = False,
        keep_mesh_dir: bool = False,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        raise AssertionError("solve_case never converts")

    def grid_partition(
        self,
        mesh_dir: Path,
        n_parts: int,
        *,
        halobc: bool,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        raise AssertionError("solve_case never partitions")

    def solve(
        self,
        sim_dir: Path,
        *,
        n_procs: int = 1,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        self.calls.append((sim_dir, n_procs, timeout_s, cancel))
        log_path = sim_dir / "elmersolver.log"
        with log_path.open("w", encoding="utf-8") as stream:
            for line in self.lines:
                _ = stream.write(line + "\n")
                if on_line is not None:
                    on_line(line + "\n")

        return CommandResult(returncode=self.returncode, duration_s=0.0, log_path=log_path, timed_out=False)


def _logged(level: str, run: Callable[[], object]) -> str:
    buffer = io.StringIO()
    sink_id: int = log.add(buffer, level=level, format="{message}")
    try:
        _ = run()
    finally:
        log.remove(sink_id)

    return buffer.getvalue()


def test_solve_case_parses_the_relayed_stream_into_events_and_jsonl(tmp_path: Path) -> None:
    events: list[SolverEvent] = []
    raw: list[str] = []

    run = solve_case(_ScriptedBackend(FIXTURE_LINES), tmp_path, on_event=events.append, on_line=raw.append)

    assert run.result.returncode == 0
    assert [type(event) for event in events] == [ConvergenceSample, SolverMessage, ConvergenceSample, SolverMessage, SolveFinished]
    assert [sample.loop for sample in run.samples] == ["NS", "SS"]
    assert run.samples[0].solver == "mgdyn" and run.samples[0].iteration == 1
    assert [message.severity for message in run.messages] == ["warning", "error"]
    assert [line.rstrip("\n") for line in raw] == FIXTURE_LINES

    rows: list[dict[str, object]] = [cast("dict[str, object]", json.loads(line)) for line in (tmp_path / "convergence.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [set(row) for row in rows] == [{"wall_time", "solver", "loop", "iter", "nrm", "relc"}] * 2
    assert rows[0]["loop"] == "NS" and rows[0]["iter"] == 1 and rows[0]["solver"] == "mgdyn"
    assert rows[0]["nrm"] == pytest.approx(1.23) and rows[0]["relc"] == pytest.approx(4.56e-03)
    assert rows[1]["loop"] == "SS" and rows[1]["relc"] == pytest.approx(1.0e-08)


def test_solve_case_logs_convergence_against_the_targets_and_the_solver_messages(tmp_path: Path) -> None:
    targets: ConvergenceTargets = {
        ("mgdyn", "NS"): LoopTargets(max_iterations=50, tolerance=1e-10),
        ("mgdyn", "SS"): LoopTargets(max_iterations=1, tolerance=1e-06),
    }

    out = _logged("INFO", lambda: solve_case(_ScriptedBackend(FIXTURE_LINES), tmp_path, targets=targets))

    assert "1/50" in out
    assert "/ 1e-10" in out
    assert "Δ = 4.56e-03" in out
    assert "0%" in out
    assert "something looks off" in out and "something broke" in out


def test_solve_case_without_targets_logs_plain_convergence_lines(tmp_path: Path) -> None:
    out = _logged("INFO", lambda: solve_case(_ScriptedBackend([FIXTURE_LINES[1]]), tmp_path))

    assert "Δ = 4.56e-03" in out
    assert "/" not in out
    assert "%" not in out


def test_solve_case_can_skip_convergence_jsonl(tmp_path: Path) -> None:
    run = solve_case(_ScriptedBackend([FIXTURE_LINES[1]]), tmp_path, convergence_jsonl=False)

    assert len(run.samples) == 1
    assert not (tmp_path / "convergence.jsonl").exists()


def test_solve_case_returns_a_failed_solve_without_raising(tmp_path: Path) -> None:
    run = solve_case(_ScriptedBackend(["ERROR:: Solver: diverged"], returncode=9), tmp_path)

    assert run.result.returncode == 9
    assert [message.severity for message in run.messages] == ["error"]


def test_solve_case_forwards_the_ranks_the_timeout_and_the_cancel_token(tmp_path: Path) -> None:
    backend = _ScriptedBackend([])
    cancel = threading.Event()

    _ = solve_case(backend, tmp_path, n_procs=4, timeout_s=30.0, cancel=cancel)

    assert backend.calls == [(tmp_path, 4, 30.0, cancel)]
