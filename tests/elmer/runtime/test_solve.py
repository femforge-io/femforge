"""solve_case: a backend that only relays lines still yields the parsed events, logged convergence and convergence.jsonl."""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import cast

import pytest

from femforge.common.log import LogKey, log
from femforge.elmer.runtime import solve
from femforge.elmer.runtime.backend import ExecutionBackend
from femforge.elmer.runtime.solve import solve_case
from femforge.elmer.solver_log import (
    ConvergenceSample,
    ConvergenceTargets,
    LinearTarget,
    LoopTargets,
    SolveFinished,
    SolverEvent,
    SolverLogParser,
    SolverMessage,
    StallReason,
)
from femforge.execution.streaming import CancelToken, CommandResult, LineSink

STALL_LOG = Path(__file__).parents[1] / "fixtures" / "stall" / "lucid_cactus.log"
ELASTICITY: tuple[LinearTarget, ...] = (LinearTarget(solver="linear elasticity", iterative=True, method="CG", tolerance=1e-8, max_iterations=5000),)

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

    def __init__(self, lines: list[str], returncode: int = 0, *, stop_when_cancelled: bool = False) -> None:
        self.lines: list[str] = lines
        self.returncode: int = returncode
        self.stop_when_cancelled: bool = stop_when_cancelled
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
                if self.stop_when_cancelled and cancel is not None and cancel.is_set():
                    break

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

    assert [call[:3] for call in backend.calls] == [(tmp_path, 4, 30.0)]
    token = backend.calls[0][3]
    assert token is not None and not token.is_set()
    cancel.set()
    assert token.is_set()


class _FakeClock:
    """A clock that advances one second per call."""

    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def _records[T](level: str, run: Callable[[], T]) -> tuple[T, list[tuple[str, dict[str, object]]]]:
    records: list[tuple[str, dict[str, object]]] = []
    sink_id: int = log.add(lambda message: records.append((message.record["message"], dict(message.record["extra"]))), level=level)
    try:
        out: T = run()
    finally:
        log.remove(sink_id)

    return out, records


def test_a_stalled_linear_solve_is_stopped_and_reported_once(tmp_path: Path) -> None:
    lines = [*STALL_LOG.read_text(encoding="utf-8").splitlines(), "ComputeChange: NS (ITER=1) (NRM,RELC): ( 1.0 2.0 ) :: linear elasticity", "       1 0.5000E+00"]
    backend = _ScriptedBackend(lines)

    run, records = _records("WARNING", lambda: solve_case(backend, tmp_path, linear_targets=ELASTICITY))

    assert run.stall is not None
    assert run.stall.reason is StallReason.REPEATED
    assert run.stall.iteration == 1339

    token = backend.calls[0][3]
    assert token is not None and token.is_set()

    stalled = [(message, extra) for message, extra in records if message.startswith("stalled:")]
    assert len(stalled) == 1
    assert stalled[0][1]["iteration"] == 1339
    assert stalled[0][1]["solver"] == "linear elasticity"


def test_the_backend_sees_the_stall_as_a_cancel(tmp_path: Path) -> None:
    backend = _ScriptedBackend(STALL_LOG.read_text(encoding="utf-8").splitlines(), stop_when_cancelled=True)

    run = solve_case(backend, tmp_path, linear_targets=ELASTICITY)

    assert run.stall is not None
    relayed = (tmp_path / "elmersolver.log").read_text(encoding="utf-8").splitlines()
    assert relayed[-1].split() == ["1339", "0.3138E-07"]


def test_a_long_linear_solve_logs_a_progress_line_with_its_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(solve, "SolverLogParser", partial(SolverLogParser, clock=_FakeClock()))
    lines = [f"{iteration:>8} {1.0 / iteration:.4E}" for iteration in range(1, 40)]

    _, records = _records("INFO", lambda: solve_case(_ScriptedBackend(lines), tmp_path, linear_targets=ELASTICITY))

    beats = [(message, extra) for message, extra in records if " r = " in message]
    assert len(beats) == 1
    message, extra = beats[0]
    assert "31/5000" in message
    assert {"solver", "method", "iteration", "max_iterations", "residual", "tolerance", "best_residual", "best_iteration", "started", "elapsed", "fraction"} <= set(extra)
    assert extra["iteration"] == 31 and extra["method"] == "CG" and extra["elapsed"] == 30.0


def test_the_convergence_and_progress_lines_bind_their_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    targets: ConvergenceTargets = {("mgdyn", "NS"): LoopTargets(max_iterations=50, tolerance=1e-10)}
    _, converging = _records("INFO", lambda: solve_case(_ScriptedBackend(FIXTURE_LINES), tmp_path, targets=targets))

    monkeypatch.setattr(solve, "SolverLogParser", partial(SolverLogParser, clock=_FakeClock()))
    lines = [f"{iteration:>8} {1.0 / iteration:.4E}" for iteration in range(1, 40)]
    _, linear = _records("INFO", lambda: solve_case(_ScriptedBackend(lines), tmp_path, linear_targets=ELASTICITY))

    computed = [extra for message, extra in converging if "Δ = " in message]
    beats = [extra for message, extra in linear if " r = " in message]
    assert computed[0][LogKey.PROGRESS] == 0.0 and computed[0][LogKey.SOLVER] == "mgdyn"
    assert [extra[LogKey.PROGRESS] for extra in beats] == [beats[0]["fraction"]]
