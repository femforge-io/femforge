"""Streaming solver-log parser and ConvergenceRenderer: real-fixture stream plus unit lines for every shape."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from femforge.elmer.solver_log import (
    ConvergenceRenderer,
    ConvergenceSample,
    ConvergenceTargets,
    LinearResidual,
    LoopTargets,
    SolveFinished,
    SolverEvent,
    SolverLogParser,
    SolverMessage,
    normalize_solver_key,
)


FIXTURE = Path(__file__).parent / "fixtures" / "elmersolver_sample.log"


class FakeClock:
    """Deterministic monotonic clock: every call advances one second."""

    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def feed_fixture(*, capture_linear: bool) -> list[SolverEvent]:
    parser = SolverLogParser(clock=FakeClock(), capture_linear=capture_linear)
    return [event for line in FIXTURE.read_text().splitlines() if (event := parser.feed(line)) is not None]


def test_fixture_convergence_samples() -> None:
    events = feed_fixture(capture_linear=False)
    samples = [e for e in events if isinstance(e, ConvergenceSample)]
    assert len(samples) == 16
    assert [s.loop for s in samples].count("NS") == 14
    assert [s.loop for s in samples].count("SS") == 2

    first = samples[0]
    assert first.solver == "mgdyn"
    assert first.loop == "NS"
    assert first.iteration == 1
    assert first.norm == pytest.approx(0.95324352e-06)
    assert first.relative_change == pytest.approx(2.0)

    last = samples[-1]
    assert last.solver == "mgdyncalc"
    assert last.loop == "SS"
    assert last.iteration == 1
    assert last.norm == 0.0
    assert last.relative_change == 0.0

    walls = [s.wall_time for s in samples]
    assert walls == sorted(walls)
    assert walls[0] < walls[-1]
    assert all(w > 0.0 for w in walls)


def test_fixture_messages_and_finish() -> None:
    events = feed_fixture(capture_linear=False)
    messages = [e for e in events if isinstance(e, SolverMessage)]
    assert len(messages) == 1
    assert messages[0].severity == "warning"
    assert messages[0].text.startswith("WARNING:: AddVtuOutputSolverHack:")

    finishes = [e for e in events if isinstance(e, SolveFinished)]
    assert finishes == [SolveFinished(cpu_time=62.30, wall_time=62.30)]

    assert not any(isinstance(e, LinearResidual) for e in events)


def test_fixture_linear_capture() -> None:
    events = feed_fixture(capture_linear=True)
    residuals = [e for e in events if isinstance(e, LinearResidual)]
    assert len(residuals) == 106
    assert residuals[0].iteration == 10
    assert residuals[0].relative_residual == pytest.approx(0.2216e-01)
    assert residuals[-1].iteration == 1
    assert residuals[-1].relative_residual == pytest.approx(0.7749e-08)
    assert len([e for e in events if isinstance(e, ConvergenceSample)]) == 16


def test_compute_change_with_solver_tag() -> None:
    parser = SolverLogParser(clock=FakeClock())
    event = parser.feed("ComputeChange: NS (ITER=1) (NRM,RELC): ( 1.23E+00 4.56E-03 ) :: heat equation")
    assert event == ConvergenceSample(solver="heat equation", loop="NS", iteration=1, norm=1.23, relative_change=4.56e-03, wall_time=1.0)


def test_compute_change_without_solver_tag() -> None:
    parser = SolverLogParser(clock=FakeClock())
    event = parser.feed("ComputeChange: NS (ITER=2) (NRM,RELC): ( 0.1483E+01 0.1102E-05 )")
    assert isinstance(event, ConvergenceSample)
    assert event.solver is None
    assert event.iteration == 2
    assert event.norm == pytest.approx(1.483)
    assert event.relative_change == pytest.approx(1.102e-06)


def test_compute_change_ss_variant() -> None:
    parser = SolverLogParser(clock=FakeClock())
    event = parser.feed("ComputeChange: SS (ITER=1) (NRM,RELC): ( 0.1483E+01 0.7240E-08 ) :: mgdyn")
    assert isinstance(event, ConvergenceSample)
    assert event.loop == "SS"
    assert event.solver == "mgdyn"


@pytest.mark.parametrize(
    "line",
    [
        "ComputeChange: NS (ITER=1) (NRM,RELC): ( NaN ******** ) :: mgdyn",
        "ComputeChange: NS (ITER=) (NRM,RELC): ( 1.0 2.0 ) :: mgdyn",
        "ComputeChange: XX (ITER=1) (NRM,RELC): ( 1.0 2.0 ) :: mgdyn",
        "ComputeChange: NS (ITER=1) (NRM,RELC): ( 1.0 ) :: mgdyn",
    ],
)
def test_garbled_compute_change_returns_none(line: str) -> None:
    assert SolverLogParser(clock=FakeClock(), capture_linear=True).feed(line) is None


def test_total_time() -> None:
    parser = SolverLogParser(clock=FakeClock())
    event = parser.feed("SOLVER TOTAL TIME(CPU,REAL):         4.55        5.12")
    assert event == SolveFinished(cpu_time=4.55, wall_time=5.12)


@pytest.mark.parametrize(
    ("line", "severity"),
    [
        ("WARNING:: AddVtuOutputSolverHack: instance already exists", "warning"),
        ("ERROR:: LoadInputFile: something broke", "error"),
        ("   WARNING: old-style single colon   ", "warning"),
        ("ERROR: old-style single colon", "error"),
    ],
)
def test_messages(line: str, severity: Literal["warning", "error"]) -> None:
    event = SolverLogParser(clock=FakeClock()).feed(line)
    assert event == SolverMessage(severity=severity, text=line.strip())


@pytest.mark.parametrize(
    ("line", "iteration", "relative_residual"),
    [
        ("      10 0.2216E-01 0.2216E-01", 10, 0.2216e-01),
        ("BiCGStabl:      156 0.5879E-08 0.5879E-08", 156, 0.5879e-08),
    ],
)
def test_linear_residual_lines(line: str, iteration: int, relative_residual: float) -> None:
    event = SolverLogParser(clock=FakeClock(), capture_linear=True).feed(line)
    assert event == LinearResidual(iteration=iteration, relative_residual=relative_residual, wall_time=1.0)


def test_linear_residual_dropped_by_default() -> None:
    assert SolverLogParser(clock=FakeClock()).feed("      10 0.2216E-01 0.2216E-01") is None


@pytest.mark.parametrize(
    "line",
    [
        "MAIN: Reading Model: case.sif",
        "OptimizeBandwidth: Optimized bandwidth for mgdyn: 8384",
        "LoadMesh: Elapsed REAL time:     1.0010 (s)",
        "",
    ],
)
def test_uninteresting_lines_return_none(line: str) -> None:
    assert SolverLogParser(clock=FakeClock(), capture_linear=True).feed(line) is None


def test_default_clock_yields_nonnegative_wall_time() -> None:
    event = SolverLogParser().feed("ComputeChange: NS (ITER=1) (NRM,RELC): ( 1.0 0.5 ) :: mgdyn")
    assert isinstance(event, ConvergenceSample)
    assert event.wall_time >= 0.0


# --- ConvergenceRenderer ---


_MGDYN_TARGETS: ConvergenceTargets = {("MgDyn", "NS"): LoopTargets(max_iterations=50, tolerance=1e-10)}


def _sample(iteration: int, relc: float, *, solver: str | None = "mgdyn", loop: Literal["NS", "SS"] = "NS", norm: float = 9.556e-07) -> ConvergenceSample:
    return ConvergenceSample(solver=solver, loop=loop, iteration=iteration, norm=norm, relative_change=relc, wall_time=1.0)


def test_normalize_solver_key_lowercases_and_collapses_spaces() -> None:
    assert normalize_solver_key("Stat Current  Solver") == "stat current solver"
    assert normalize_solver_key("MgDyn") == "mgdyn"


def test_render_with_targets_shows_cap_tolerance_and_percent() -> None:
    renderer = ConvergenceRenderer(_MGDYN_TARGETS)
    _ = renderer.render(_sample(1, 1.0))

    # relc fell from 1e0 halfway (in decades) to the 1e-10 tolerance -> 50%.
    line = renderer.render(_sample(2, 1e-05))
    assert "2/50" in line
    assert "Δ = 1.00e-05" in line
    assert "/ 1e-10" in line
    assert line.endswith("50%")


def test_render_percent_saturates_at_tolerance() -> None:
    renderer = ConvergenceRenderer(_MGDYN_TARGETS)
    _ = renderer.render(_sample(1, 1.0))
    assert renderer.render(_sample(2, 1e-12)).endswith("100%")


def test_render_iteration_one_shows_zero_percent() -> None:
    renderer = ConvergenceRenderer(_MGDYN_TARGETS)
    assert renderer.render(_sample(1, 2.0)).endswith("0%")


def test_render_iteration_one_resets_the_baseline() -> None:
    # NS restarts at ITER=1 inside each SS sweep; the second sweep must not inherit the first sweep's baseline.
    renderer = ConvergenceRenderer(_MGDYN_TARGETS)
    _ = renderer.render(_sample(1, 1.0))
    _ = renderer.render(_sample(2, 1e-10))
    line = renderer.render(_sample(1, 1e-2))
    assert line.endswith("0%")


def test_render_without_targets_degrades() -> None:
    renderer = ConvergenceRenderer()
    line = renderer.render(_sample(3, 9.0e-04))
    assert "Δ = 9.00e-04" in line
    assert "%" not in line
    assert "/" not in line


def test_render_unknown_solver_never_raises() -> None:
    renderer = ConvergenceRenderer(_MGDYN_TARGETS)
    line = renderer.render(_sample(1, 0.5, solver=None))
    assert line.startswith("solver")
    assert "%" not in line


def test_render_no_percent_when_targets_lack_tolerance() -> None:
    targets: ConvergenceTargets = {("mgdyn", "NS"): LoopTargets(max_iterations=50, tolerance=None)}
    renderer = ConvergenceRenderer(targets)
    line = renderer.render(_sample(1, 1.0))
    assert "1/50" in line
    assert "%" not in line


def test_render_no_percent_on_degenerate_baselines() -> None:
    renderer = ConvergenceRenderer(_MGDYN_TARGETS)
    # Zero first relc (already converged at iteration 1) leaves the percent undefined rather than dividing by zero.
    assert "%" not in renderer.render(_sample(1, 0.0))


def test_render_lines_are_width_stable() -> None:
    renderer = ConvergenceRenderer(_MGDYN_TARGETS)
    _ = renderer.render(_sample(1, 1.0))
    lines = [renderer.render(_sample(i, relc)) for i, relc in ((2, 1.1e-05), (3, 4.0e-06), (10, 9.9e-08))]
    positions = [(line.index("L₂ Norm"), line.index("Δ ="), line.index("%")) for line in lines]
    assert len(set(positions)) == 1
