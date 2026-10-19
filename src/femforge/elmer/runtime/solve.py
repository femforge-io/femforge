"""The solve every backend shares: run ElmerSolver through a backend and parse its stream, so a remote backend only relays lines."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO

from femforge.common.files import BundleFile
from femforge.common.log import LogKey, log
from femforge.elmer.runtime.backend import ExecutionBackend
from femforge.elmer.solver_log import (
    ConvergenceRenderer,
    ConvergenceSample,
    ConvergenceTargets,
    LinearProgress,
    LinearResidual,
    LinearTarget,
    SolverEvent,
    SolverLogParser,
    SolverMessage,
    StallDetector,
    StallVerdict,
)
from femforge.execution.streaming import CancelToken, CommandResult, LineSink


__all__ = ["SolveRun", "solve_case"]


@dataclass(frozen=True)
class SolveRun:
    """One ElmerSolver run: the command outcome plus the convergence samples and warning/error messages parsed from its stream."""

    result: CommandResult
    samples: list[ConvergenceSample]
    messages: list[SolverMessage]
    stall: StallVerdict | None = None
    """The watchdog's verdict when it stopped the solve; None otherwise."""


@dataclass(frozen=True)
class _StallOrCancel:
    """A cancel token set by the caller's token or by the stall watchdog."""

    stalled: threading.Event
    cancel: CancelToken | None

    def is_set(self) -> bool:
        """Whether the watchdog or the caller asked the solve to stop."""
        return self.stalled.is_set() or (self.cancel is not None and self.cancel.is_set())


def solve_case(
    backend: ExecutionBackend,
    sim_dir: Path,
    *,
    n_procs: int = 1,
    targets: ConvergenceTargets | None = None,
    linear_targets: Sequence[LinearTarget] = (),
    convergence_jsonl: bool = True,
    on_event: Callable[[SolverEvent], None] | None = None,
    on_line: LineSink | None = None,
    timeout_s: float | None = None,
    cancel: CancelToken | None = None,
) -> SolveRun:
    """Run ElmerSolver on ``sim_dir`` through ``backend``, logging and collecting what its stream reports, and stop a linear solve that stalls.

    A failed solve returns its nonzero exit; ``run.parse`` grades the unchanged ``elmersolver.log``.

    Args:
        backend: Where ElmerSolver runs.
        sim_dir: The case directory.
        n_procs: The MPI rank count.
        targets: The sif-authored iteration caps and tolerances the logged convergence lines report against.
        linear_targets: The stack's linear solvers in sif order, which attribute and judge the linear residuals.
        convergence_jsonl: Write each convergence sample to ``convergence.jsonl`` in ``sim_dir``.
        on_event: Receives each parsed solver event.
        on_line: Receives each raw output line.
        timeout_s: Seconds before the solve is stopped; None waits for it.
        cancel: Stops the solve once it is set.
    """
    parser = SolverLogParser(capture_linear=True, linear_targets=linear_targets)
    # One renderer per solve: its first-relc progress baseline must not leak into the next solve.
    renderer = ConvergenceRenderer(targets)
    detector = StallDetector(linear_targets)
    stalled = threading.Event()
    samples: list[ConvergenceSample] = []
    messages: list[SolverMessage] = []
    stream: IO[str] | None = (sim_dir / BundleFile.CONVERGENCE).open("w", encoding="utf-8") if convergence_jsonl else None
    if linear_targets:
        solvers = [target.solver for target in linear_targets]
        direct = [target.solver for target in linear_targets if not target.iterative]
        log.bind(**{str(LogKey.LINEAR_SOLVERS): solvers, str(LogKey.DIRECT_SOLVERS): direct}).debug("Linear solvers listed.")

    def watch(residual: LinearResidual) -> None:
        verdict: StallVerdict | None = detector.feed(residual)
        if verdict is not None:
            stalled.set()
            log.bind(
                **{
                    str(LogKey.STALL): verdict.reason,
                    str(LogKey.SOLVER): verdict.solver,
                    str(LogKey.ITERATION): verdict.iteration,
                    str(LogKey.RESIDUAL): verdict.residual,
                },
                best_residual=verdict.best_residual,
                tolerance=verdict.tolerance,
            ).warning(f"stalled: {verdict.summary}")
            return

        progress: LinearProgress | None = detector.progress
        if progress is None:
            return

        line: str | None = renderer.heartbeat(progress)
        if line is not None:
            log.bind(**{**asdict(progress), str(LogKey.PROGRESS): progress.fraction}).info(line)

    def handle(line: str) -> None:
        if on_line is not None:
            on_line(line)

        event: SolverEvent | None = parser.feed(line)
        if event is None:
            return

        if isinstance(event, ConvergenceSample):
            detector.end_solve()
            samples.append(event)
            if stream is not None:
                record: dict[str, object] = {
                    "wall_time": event.wall_time,
                    "solver": event.solver,
                    "loop": event.loop,
                    "iter": event.iteration,
                    "nrm": event.norm,
                    "relc": event.relative_change,
                }
                _ = stream.write(json.dumps(record) + "\n")
                stream.flush()
        elif isinstance(event, SolverMessage):
            messages.append(event)
        elif isinstance(event, LinearResidual):
            watch(event)

        if on_event is not None:
            on_event(event)

        if isinstance(event, ConvergenceSample):
            log.bind(**{str(LogKey.PROGRESS): renderer.progress(event), str(LogKey.SOLVER): event.solver}).info(renderer.render(event))
        elif isinstance(event, SolverMessage):
            (log.warning if event.severity == "warning" else log.error)(event.text)

    try:
        result: CommandResult = backend.solve(
            sim_dir,
            n_procs=n_procs,
            on_line=handle,
            timeout_s=timeout_s,
            cancel=_StallOrCancel(stalled, cancel),
        )
    finally:
        if stream is not None:
            stream.close()

    return SolveRun(result=result, samples=samples, messages=messages, stall=detector.verdict)
