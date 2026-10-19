"""Streaming ElmerSolver log parser (`SolverLogParser.feed`), the linear-solve `StallDetector`, and the fixed-width `ConvergenceRenderer` for terminal display."""

from __future__ import annotations

import bisect
import math
import re
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


# A signed integer / float / scientific-notation number, e.g. "62.30", "0.1483E+01" (parse.py precedent).
_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"
# "ComputeChange: NS (ITER=1) (NRM,RELC): ( 1.23E+00 4.56E-03 ) :: heat equation"; the ":: solver" tag is absent on some builds,
# and Elmer's assembly progress bar can overwrite the "ComputeChange:" prefix.
_COMPUTE_CHANGE_RE = re.compile(rf"(?:ComputeChange:\s+)?\b(NS|SS)\s+\(ITER=(\d+)\)\s+\(NRM,RELC\):\s+\(\s*({_NUM})\s+({_NUM})\s*\)(?:\s*::\s*(.+?)\s*$)?")
# "SOLVER TOTAL TIME(CPU,REAL):   62.30   62.30"
_TOTAL_TIME_RE = re.compile(rf"SOLVER TOTAL TIME\(CPU,REAL\):\s*({_NUM})\s+({_NUM})")
# Elmer emits "WARNING:: module: text" / "ERROR:: ..."; single-colon forms appear in older builds.
_MESSAGE_RE = re.compile(r"\b(WARNING|ERROR):{1,2}")
# An iterative-solver residual line: optional method prefix ("BiCGStabl:"), iteration, then one or two residuals (CG and BiCGStab print one).
# Fully anchored so counter lines like "OptimizeBandwidth: ... : 8384" can never match.
_LINEAR_RE = re.compile(rf"^(?:\w+:)?\s*(\d+)\s+({_NUM})(?:\s+({_NUM}))?\s*$")


STALL_REPEAT_ITERATIONS: int = 250
"""`n_repeat` `[-]`: Iterations one linear residual may print unchanged before the solve counts as stalled."""

STALL_WINDOW_MIN: int = 500
"""`n_window` `[-]`: The shortest window, in iterations, over which the best linear residual must improve."""

STALL_WINDOW_FRACTION: float = 0.9
"""`f_window` `[-]`: The window as a share of the iterations so far, when that is longer than ``STALL_WINDOW_MIN``."""

STALL_IMPROVEMENT: float = 0.1
"""`ΔR` `[-]`: The relative gain the best linear residual must make over the window."""

STALL_TOLERANCE_FACTOR: float = 10.0
"""`k_tol` `[-]`: A best residual within this factor of the tolerance never counts as no progress."""

STALL_TAIL: int = 20
"""`n_tail` `[-]`: How many of the last linear residuals a stall verdict keeps."""

LINEAR_HEARTBEAT_S: float = 30.0
"""`t_beat` `[s]`: The shortest gap between two progress lines of one linear solve."""


@dataclass(frozen=True)
class ConvergenceSample:
    """One ComputeChange line: a nonlinear (NS) or steady-state (SS) convergence step of a solver."""

    solver: str | None
    """The sif ``Equation`` name Elmer tags the line with, lowercased; None on builds that omit the tag."""

    loop: Literal["NS", "SS"]
    """Which convergence loop the step belongs to: the nonlinear inner loop or the steady-state outer loop."""

    iteration: int
    """One-based iteration count within the loop."""

    norm: float
    """`‖u‖₂` `[?]`: The solution's L₂ norm at this iteration."""

    relative_change: float
    """`Δ_rel` `[-]`: Relative change of the norm from the previous iteration; the quantity the loop's tolerance is tested against."""

    wall_time: float
    """`t_wall` `[s]`: Seconds since the parser started reading the stream."""


@dataclass(frozen=True)
class LinearResidual:
    """One linear-iteration residual line, emitted only when the parser was built with ``capture_linear=True``."""

    iteration: int
    """The linear solver's iteration count."""

    relative_residual: float
    """`r_rel` `[-]`: The residual relative to the initial one."""

    wall_time: float
    """`t_wall` `[s]`: Seconds since the parser started reading the stream."""

    solver: str | None = None
    """The normalized ``Equation`` of the solver the parser attributes the line to; None without linear targets."""


@dataclass(frozen=True)
class SolverMessage:
    """A WARNING/ERROR line, carrying the full stripped line as text."""

    severity: Literal["warning", "error"]
    text: str


@dataclass(frozen=True)
class SolveFinished:
    """The SOLVER TOTAL TIME line that closes a run."""

    cpu_time: float
    """`t_cpu` `[s]`: Processor seconds ElmerSolver reports for the whole run."""

    wall_time: float
    """`t_wall` `[s]`: Real seconds ElmerSolver reports for the whole run."""


SolverEvent = ConvergenceSample | LinearResidual | SolverMessage | SolveFinished


@dataclass(frozen=True)
class LoopTargets:
    """The sif-authored iteration cap and convergence tolerance for one solver's NS or SS loop."""

    max_iterations: int | None
    """The loop's iteration cap; None when the sif leaves Elmer's default."""

    tolerance: float | None
    """`Δ_tol` `[-]`: The relative-change tolerance the loop converges to; None when the sif leaves Elmer's default."""


# Per-(solver, loop) targets, keyed by the solver tag Elmer prints after "::" on ComputeChange lines.
ConvergenceTargets = Mapping[tuple[str, Literal["NS", "SS"]], LoopTargets]


@dataclass(frozen=True)
class LinearTarget:
    """The linear-solve settings of one solver in the stack, listed in stack order."""

    solver: str
    """The solver's normalized ``Equation``, the tag Elmer prints on its ComputeChange lines."""

    iterative: bool
    """Whether the solver solves its linear system with a Krylov iteration rather than a factorization."""

    method: str | None
    """The Krylov method, such as ``CG``; None for a direct solve or when the sif leaves Elmer's default."""

    tolerance: float | None
    """`r_tol` `[-]`: The relative residual the linear solve converges to; None when the sif leaves Elmer's default."""

    max_iterations: int | None
    """The linear solve's iteration cap; None when the sif leaves Elmer's default."""


class StallReason(StrEnum):
    """Why the watchdog stopped a linear solve."""

    REPEATED = "repeated"
    """The residual printed one value for ``STALL_REPEAT_ITERATIONS`` iterations."""

    NO_PROGRESS = "no_progress"
    """The best residual improved by less than ``STALL_IMPROVEMENT`` over the window while still far above the tolerance."""


@dataclass(frozen=True)
class LinearProgress:
    """A snapshot of one linear solve; the field names are the bind keys of its progress log line."""

    solver: str | None
    """The normalized ``Equation`` of the solving solver; None when unknown."""

    method: str | None
    """The Krylov method; None when unknown."""

    iteration: int
    """The latest linear iteration."""

    max_iterations: int | None
    """The linear solve's iteration cap; None when unknown."""

    residual: float
    """`r_rel` `[-]`: The latest relative residual."""

    tolerance: float | None
    """`r_tol` `[-]`: The relative residual the solve converges to; None when unknown."""

    best_residual: float
    """`r_best` `[-]`: The lowest relative residual so far."""

    best_iteration: int
    """The iteration that reached ``best_residual``."""

    started: float
    """`t_0` `[s]`: Parser time of the solve's first residual."""

    elapsed: float
    """`t` `[s]`: Seconds since the solve's first residual."""

    fraction: float | None
    """`f` `[-]`: Log-scale share of the decades between the first residual and the tolerance the best residual has covered; None when unknown."""


@dataclass(frozen=True)
class StallVerdict:
    """The watchdog's finding that a linear solve stopped converging, with the residuals that led to it."""

    reason: StallReason
    """Which test fired."""

    solver: str | None
    """The normalized ``Equation`` of the stalled solver; None when unknown."""

    method: str | None
    """The Krylov method; None when unknown."""

    iteration: int
    """The linear iteration the verdict fired at."""

    residual: float
    """`r_rel` `[-]`: The relative residual at that iteration."""

    best_residual: float
    """`r_best` `[-]`: The lowest relative residual of the solve."""

    best_iteration: int
    """The iteration that reached ``best_residual``."""

    tolerance: float | None
    """`r_tol` `[-]`: The relative residual the solve was to converge to; None when unknown."""

    tail: tuple[LinearResidual, ...]
    """The last ``STALL_TAIL`` residuals of the solve, oldest first."""

    @property
    def summary(self) -> str:
        """One sentence naming the solver, the residual and the test that fired."""
        who: str = " ".join(part for part in (self.solver, self.method) if part) or "linear solve"
        target: str = f", tolerance {self.tolerance:.0e}" if self.tolerance is not None else ""

        if self.reason is StallReason.REPEATED:
            return (
                f"{who} residual {self.residual:.3e} unchanged for {STALL_REPEAT_ITERATIONS} iterations at iteration {self.iteration} (best {self.best_residual:.3e} at {self.best_iteration}{target})"
            )

        window: int = round(max(STALL_WINDOW_MIN, STALL_WINDOW_FRACTION * self.iteration))
        return f"{who} best {self.best_residual:.2e} improved less than {STALL_IMPROVEMENT:.0%} over the last {window} iterations at iteration {self.iteration} (residual {self.residual:.3e}{target})"


class StallDetector:
    """Watch the linear residuals of a solve and return a [`StallVerdict`][] once its residual stops improving."""

    def __init__(self, targets: Sequence[LinearTarget] = ()) -> None:
        """Judge each residual against its solver's entry in ``targets``; a solver without one is judged on repeats alone."""
        self._targets: tuple[LinearTarget, ...] = tuple(targets)
        self._verdict: StallVerdict | None = None
        self._progress: LinearProgress | None = None
        self._target: LinearTarget | None = None
        self._last: LinearResidual | None = None
        self._run_start: int = 0
        self._best_iteration: int = 0
        self._first: float = 0.0
        self._started: float = 0.0
        self._iterations: list[int] = []
        self._bests: list[float] = []
        self._tail: deque[LinearResidual] = deque(maxlen=STALL_TAIL)

    @property
    def verdict(self) -> StallVerdict | None:
        """The first verdict; None while every solve still converges."""
        return self._verdict

    @property
    def progress(self) -> LinearProgress | None:
        """The current solve's latest snapshot; None before its first residual."""
        return self._progress

    def end_solve(self) -> None:
        """Close the current linear solve, so the next residual starts a new one."""
        self._last = None
        self._progress = None

    def feed(self, sample: LinearResidual) -> StallVerdict | None:
        """Record ``sample`` and return the verdict when the solve stalled; None otherwise, and always None after the first verdict."""
        if self._verdict is not None:
            return None

        last: LinearResidual | None = self._last
        residual: float = sample.relative_residual
        if last is not None and sample.iteration == last.iteration and residual == last.relative_residual:
            return None

        if last is None or sample.iteration < last.iteration or sample.solver != last.solver:
            self._target = next((target for target in self._targets if target.solver == sample.solver), None)
            self._run_start = sample.iteration
            self._first = residual
            self._started = sample.wall_time
            self._iterations.clear()
            self._bests.clear()
            self._tail.clear()
        elif residual != last.relative_residual:
            self._run_start = sample.iteration

        previous_best: float = self._bests[-1] if self._bests else math.inf
        if residual < previous_best:
            self._best_iteration = sample.iteration

        best: float = min(residual, previous_best)
        self._iterations.append(sample.iteration)
        self._bests.append(best)
        self._tail.append(sample)
        self._last = sample

        target: LinearTarget | None = self._target
        tolerance: float | None = target.tolerance if target is not None else None
        self._progress = LinearProgress(
            solver=sample.solver,
            method=target.method if target is not None else None,
            iteration=sample.iteration,
            max_iterations=target.max_iterations if target is not None else None,
            residual=residual,
            tolerance=tolerance,
            best_residual=best,
            best_iteration=self._best_iteration,
            started=self._started,
            elapsed=sample.wall_time - self._started,
            fraction=convergence_progress(self._first, best, tolerance),
        )

        def stalled() -> StallReason | None:
            if sample.iteration - self._run_start >= STALL_REPEAT_ITERATIONS:
                return StallReason.REPEATED

            if tolerance is None or best <= STALL_TOLERANCE_FACTOR * tolerance:
                return None

            cut: float = sample.iteration - max(STALL_WINDOW_MIN, STALL_WINDOW_FRACTION * sample.iteration)
            if cut < self._iterations[0]:
                return None

            then: float = self._bests[bisect.bisect_right(self._iterations, cut) - 1]
            return StallReason.NO_PROGRESS if best > (1.0 - STALL_IMPROVEMENT) * then else None

        reason: StallReason | None = stalled()
        if reason is None:
            return None

        self._verdict = StallVerdict(
            reason=reason,
            solver=sample.solver,
            method=self._progress.method,
            iteration=sample.iteration,
            residual=residual,
            best_residual=best,
            best_iteration=self._best_iteration,
            tolerance=tolerance,
            tail=tuple(self._tail),
        )
        return self._verdict


def normalize_solver_key(name: str) -> str:
    """Elmer tags ComputeChange lines with the sif ``Equation`` lowercased (inner spaces kept); normalize both sides to that form."""
    return " ".join(name.lower().split())


def convergence_progress(first: float | None, relc: float, tolerance: float | None) -> float | None:
    """Log-scale fraction of the decades between a loop's first relative change and its tolerance already covered; None when undefined.

    Args:
        first: The loop's first relative change.
        relc: The latest relative change.
        tolerance: The loop's convergence tolerance.
    """
    if tolerance is None or first is None or first <= 0 or relc <= 0 or tolerance <= 0 or first <= tolerance:
        return None

    if relc <= tolerance:
        return 1.0

    return min(max(math.log(first / relc) / math.log(first / tolerance), 0.0), 1.0)


class ConvergenceRenderer:
    """Render [`ConvergenceSample`][] as a fixed-width line with iter/max, relc vs tolerance, and a log-scale percent-converged."""

    def __init__(self, targets: ConvergenceTargets | None = None) -> None:
        """Render against ``targets``, the per-solver iteration caps and tolerances."""
        self._targets: dict[tuple[str, str], LoopTargets] = {(normalize_solver_key(solver), loop): t for (solver, loop), t in (targets or {}).items()}
        # First relative change per (solver, loop) — the progress baseline; iteration 1 (re)stores it, so NS restarts inside each SS sweep reset automatically.
        self._first_relc: dict[tuple[str, str], float] = {}
        self._beat_started: float | None = None
        self._last_beat: float = 0.0

    def render(self, sample: ConvergenceSample) -> str:
        """One aligned line; the iteration cap, tolerance, and percent appear only when the sample's solver has known targets."""
        key: tuple[str, str] = (normalize_solver_key(sample.solver or ""), sample.loop)
        progress: float | None = self.progress(sample)
        targets: LoopTargets | None = self._targets.get(key)
        max_iterations: int | None = targets.max_iterations if targets is not None else None
        tolerance: float | None = targets.tolerance if targets is not None else None

        iterations = f"{sample.iteration:>3}/{max_iterations:<4}" if max_iterations is not None else f"{sample.iteration:>3}     "
        # The arrow branch is 8 chars (" → 1e-10"); the no-target pad matches so mixed lines stay aligned.
        target = f" / {tolerance:.0e}" if tolerance is not None else " " * 8
        percent = f"{progress * 100:>4.0f}%" if progress is not None else ""

        line = f"{sample.solver or 'solver':<12.12} {sample.loop:<2} {iterations}  L₂ Norm = {sample.norm:.3e}  Δ = {sample.relative_change:.2e}{target}  {percent}"
        return line.rstrip()

    def heartbeat(self, progress: LinearProgress) -> str | None:
        """A progress line for a linear solve once ``LINEAR_HEARTBEAT_S`` passed since its start or its last line; None until then."""
        if progress.started != self._beat_started:
            self._beat_started = progress.started
            self._last_beat = 0.0

        if progress.elapsed - self._last_beat < LINEAR_HEARTBEAT_S:
            return None

        self._last_beat = progress.elapsed
        iterations = f"{progress.iteration:>5}/{progress.max_iterations:<5}" if progress.max_iterations is not None else f"{progress.iteration:>5}      "
        target = f" / {progress.tolerance:.0e}" if progress.tolerance is not None else " " * 8
        best = f"best {progress.best_residual:.3e} @ {progress.best_iteration}"

        return f"{progress.solver or 'solver':<12.12} {progress.method or '':<9} {iterations} r = {progress.residual:.3e}{target}  {best}  {progress.elapsed:>5.0f} s"

    def progress(self, sample: ConvergenceSample) -> float | None:
        """The sample's loop progress against its tolerance, from the loop's first relative change; iteration 1 sets that baseline."""
        key: tuple[str, str] = (normalize_solver_key(sample.solver or ""), sample.loop)
        if sample.iteration == 1:
            self._first_relc[key] = sample.relative_change

        targets: LoopTargets | None = self._targets.get(key)
        tolerance: float | None = targets.tolerance if targets is not None else None

        return convergence_progress(self._first_relc.get(key), sample.relative_change, tolerance)


class SolverLogParser:
    """Feed ElmerSolver log lines one at a time; each convergence/message/finish line comes back as a typed event."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        capture_linear: bool = False,
        linear_targets: Sequence[LinearTarget] = (),
    ) -> None:
        """Start the ``clock`` that stamps each event; ``capture_linear`` also reports linear residual lines, attributed to a solver of ``linear_targets``.

        Args:
            clock: The monotonic clock that stamps each event.
            capture_linear: Report linear-solver residual lines.
            linear_targets: The stack's linear solvers in sif order; residuals go to the solver the ComputeChange lines point at.
        """
        self._clock = clock
        self._capture_linear = capture_linear
        self._start: float = clock()
        self._targets: tuple[LinearTarget, ...] = tuple(linear_targets)
        self._cursor: int | None = self._next_iterative(-1)

    def feed(self, line: str) -> SolverEvent | None:
        """Parse one log line into an event, or None for lines the stream does not report (malformed lines included)."""
        if (change := _COMPUTE_CHANGE_RE.search(line)) is not None:
            loop: Literal["NS", "SS"] = "NS" if change.group(1) == "NS" else "SS"
            tag: str | None = change.group(5)
            # An NS step's residuals precede it and post-processing follows it; an SS step hands over to the next solver.
            index: int | None = self._cursor if tag is None else next((i for i, target in enumerate(self._targets) if target.solver == normalize_solver_key(tag)), None)
            if index is not None:
                self._cursor = index if loop == "NS" else self._next_iterative(index)

            return ConvergenceSample(
                solver=tag,
                loop=loop,
                iteration=int(change.group(2)),
                norm=float(change.group(3)),
                relative_change=float(change.group(4)),
                wall_time=self._elapsed(),
            )
        if (total := _TOTAL_TIME_RE.search(line)) is not None:
            return SolveFinished(cpu_time=float(total.group(1)), wall_time=float(total.group(2)))
        if (message := _MESSAGE_RE.search(line)) is not None:
            severity: Literal["warning", "error"] = "warning" if message.group(1) == "WARNING" else "error"
            return SolverMessage(severity=severity, text=line.strip())
        if self._capture_linear and (linear := _LINEAR_RE.match(line)) is not None:
            residual: str = linear.group(3) if linear.group(3) is not None else linear.group(2)
            solver: str | None = self._targets[self._cursor].solver if self._cursor is not None else None
            return LinearResidual(
                iteration=int(linear.group(1)),
                relative_residual=float(residual),
                wall_time=self._elapsed(),
                solver=solver,
            )
        return None

    def _next_iterative(self, after: int) -> int | None:
        """The index of the first iterative target after ``after``, wrapping around; None when no target is iterative."""
        count: int = len(self._targets)
        order: list[int] = [(after + step) % count for step in range(1, count + 1)]

        return next((index for index in order if self._targets[index].iterative), None)

    def _elapsed(self) -> float:
        """Wall seconds since the parser was constructed."""
        return self._clock() - self._start
