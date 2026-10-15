"""Streaming ElmerSolver log parser (`SolverLogParser.feed`) plus the fixed-width `ConvergenceRenderer` for terminal display."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal


# A signed integer / float / scientific-notation number, e.g. "62.30", "0.1483E+01" (parse.py precedent).
_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"
# "ComputeChange: NS (ITER=1) (NRM,RELC): ( 1.23E+00 4.56E-03 ) :: heat equation"; the ":: solver" tag is absent on some builds.
_COMPUTE_CHANGE_RE = re.compile(rf"ComputeChange:\s+(NS|SS)\s+\(ITER=(\d+)\)\s+\(NRM,RELC\):\s+\(\s*({_NUM})\s+({_NUM})\s*\)(?:\s*::\s*(.+?)\s*$)?")
# "SOLVER TOTAL TIME(CPU,REAL):   62.30   62.30"
_TOTAL_TIME_RE = re.compile(rf"SOLVER TOTAL TIME\(CPU,REAL\):\s*({_NUM})\s+({_NUM})")
# Elmer emits "WARNING:: module: text" / "ERROR:: ..."; single-colon forms appear in older builds.
_MESSAGE_RE = re.compile(r"\b(WARNING|ERROR):{1,2}")
# An iterative-solver residual line: optional method prefix ("BiCGStabl:"), iteration, then residual + relative residual.
# Fully anchored so counter lines like "OptimizeBandwidth: ... : 8384" can never match.
_LINEAR_RE = re.compile(rf"^(?:\w+:)?\s*(\d+)\s+{_NUM}\s+({_NUM})\s*$")


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

    def render(self, sample: ConvergenceSample) -> str:
        """One aligned line; the iteration cap, tolerance, and percent appear only when the sample's solver has known targets."""
        key: tuple[str, str] = (normalize_solver_key(sample.solver or ""), sample.loop)

        if sample.iteration == 1:
            self._first_relc[key] = sample.relative_change

        targets: LoopTargets | None = self._targets.get(key)
        max_iterations: int | None = targets.max_iterations if targets is not None else None
        tolerance: float | None = targets.tolerance if targets is not None else None

        iterations = f"{sample.iteration:>3}/{max_iterations:<4}" if max_iterations is not None else f"{sample.iteration:>3}     "
        # The arrow branch is 8 chars (" → 1e-10"); the no-target pad matches so mixed lines stay aligned.
        target = f" / {tolerance:.0e}" if tolerance is not None else " " * 8
        progress: float | None = self._progress(key, sample.relative_change, tolerance)
        percent = f"{progress * 100:>4.0f}%" if progress is not None else ""

        line = f"{sample.solver or 'solver':<12.12} {sample.loop:<2} {iterations}  L₂ Norm = {sample.norm:.3e}  Δ = {sample.relative_change:.2e}{target}  {percent}"
        return line.rstrip()

    def _progress(self, key: tuple[str, str], relc: float, tolerance: float | None) -> float | None:
        """This loop's progress against its tolerance, measured from the first relative change the renderer saw for it."""
        return convergence_progress(self._first_relc.get(key), relc, tolerance)


class SolverLogParser:
    """Feed ElmerSolver log lines one at a time; each convergence/message/finish line comes back as a typed event."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, capture_linear: bool = False) -> None:
        """Start the ``clock`` that stamps each event; ``capture_linear`` also reports linear-solver residual lines."""
        self._clock = clock
        self._capture_linear = capture_linear
        self._start: float = clock()

    def feed(self, line: str) -> SolverEvent | None:
        """Parse one log line into an event, or None for lines the stream does not report (malformed lines included)."""
        if (change := _COMPUTE_CHANGE_RE.search(line)) is not None:
            loop: Literal["NS", "SS"] = "NS" if change.group(1) == "NS" else "SS"
            return ConvergenceSample(
                solver=change.group(5),
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
            return LinearResidual(iteration=int(linear.group(1)), relative_residual=float(linear.group(2)), wall_time=self._elapsed())
        return None

    def _elapsed(self) -> float:
        """Wall seconds since the parser was constructed."""
        return self._clock() - self._start
