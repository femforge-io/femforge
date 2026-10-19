"""Streaming solver-log parser and ConvergenceRenderer: real-fixture stream plus unit lines for every shape."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from femforge.elmer.solver_log import (
    ConvergenceRenderer,
    ConvergenceSample,
    ConvergenceTargets,
    LinearProgress,
    LinearResidual,
    LinearTarget,
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


# --- one-value residual lines, unprefixed NS lines and solver attribution ---


MGDYN_3D: tuple[LinearTarget, ...] = (
    LinearTarget(solver="mgdyn", iterative=True, method="BiCGStabl", tolerance=1e-6, max_iterations=500),
    LinearTarget(solver="mgdyncalc", iterative=True, method="CG", tolerance=1e-8, max_iterations=500),
)

MGDYN_2D: tuple[LinearTarget, ...] = (
    LinearTarget(solver="mgdyn2d", iterative=False, method=None, tolerance=1e-8, max_iterations=None),
    LinearTarget(solver="mgdyncalc", iterative=True, method="CG", tolerance=1e-8, max_iterations=500),
)

ELASTICITY: tuple[LinearTarget, ...] = (LinearTarget(solver="linear elasticity", iterative=True, method="CG", tolerance=1e-8, max_iterations=5000),)

MGDYN_2D_LINES: list[str] = [
    "ComputeChange: NS (ITER=1) (NRM,RELC): ( 0.23508438E-03  2.0000000     ) :: mgdyn2d",
    "ComputeChange: NS (ITER=2) (NRM,RELC): ( 0.23363571E-03 0.33435978E-01 ) :: mgdyn2d",
    "ComputeChange: SS (ITER=1) (NRM,RELC): ( 0.23367298E-03  2.0000000     ) :: mgdyn2d",
    "       1 0.1400E-01",
    "       2 0.2080E-08",
    "       2 0.2080E-08",
    "ComputeChange: NS (ITER=1) (NRM,RELC): ( 0.90068768E-01  2.0000000     ) :: mgdyncalc",
    "       1 0.1601E-01",
    "       2 0.4283E-08",
    "ComputeChange: SS (ITER=1) (NRM,RELC): (  0.0000000      0.0000000     ) :: mgdyncalc",
]


def _residuals(lines: list[str], targets: tuple[LinearTarget, ...]) -> list[LinearResidual]:
    parser = SolverLogParser(clock=FakeClock(), capture_linear=True, linear_targets=targets)
    return [event for line in lines if isinstance(event := parser.feed(line), LinearResidual)]


@pytest.mark.parametrize(
    ("line", "iteration", "relative_residual"),
    [
        ("       1 0.7730E-02", 1, 0.7730e-02),
        ("    1089 0.3138E-07", 1089, 0.3138e-07),
    ],
)
def test_one_value_residual_lines(line: str, iteration: int, relative_residual: float) -> None:
    event = SolverLogParser(clock=FakeClock(), capture_linear=True).feed(line)
    assert event == LinearResidual(iteration=iteration, relative_residual=relative_residual, wall_time=1.0)


def test_an_ns_line_whose_prefix_the_progress_bar_ate_still_parses() -> None:
    event = SolverLogParser(clock=FakeClock()).feed("NS (ITER=1) (NRM,RELC): ( 0.59256258E-04  2.0000000     ) :: linear elasticity")
    assert isinstance(event, ConvergenceSample)
    assert event.solver == "linear elasticity"
    assert event.relative_change == pytest.approx(2.0)


def test_without_targets_residuals_carry_no_solver() -> None:
    assert {residual.solver for residual in _residuals(MGDYN_2D_LINES, ())} == {None}


def test_the_3d_fixture_puts_every_residual_on_mgdyn() -> None:
    residuals = _residuals(FIXTURE.read_text().splitlines(), MGDYN_3D)
    assert len(residuals) == 106
    assert {residual.solver for residual in residuals} == {"mgdyn"}


def test_a_direct_2d_solve_hands_the_residuals_to_mgdyncalc() -> None:
    residuals = _residuals(MGDYN_2D_LINES, MGDYN_2D)
    assert len(residuals) == 5
    assert {residual.solver for residual in residuals} == {"mgdyncalc"}


def test_stress_recovery_after_the_ns_step_stays_on_its_solver() -> None:
    lines = [
        "       1 0.6138E+00",
        "       2 0.8327E-08",
        "NS (ITER=1) (NRM,RELC): ( 0.59256258E-04  2.0000000     ) :: linear elasticity",
        "StressSolver: Solving for Stress(1,1)",
        "       1 0.1000E+00",
        "ComputeChange: SS (ITER=1) (NRM,RELC): ( 0.59256258E-04  2.0000000     ) :: linear elasticity",
        "       1 0.2000E+00",
    ]
    assert [residual.solver for residual in _residuals(lines, ELASTICITY)] == ["linear elasticity"] * 4


def test_untagged_and_unknown_compute_change_lines_steer_the_cursor() -> None:
    lines = [
        "ComputeChange: SS (ITER=1) (NRM,RELC): ( 1.0 2.0 ) :: mgdyn",
        "       1 0.1000E+00",
        "ComputeChange: NS (ITER=1) (NRM,RELC): ( 1.0 2.0 )",
        "       1 0.2000E+00",
        "ComputeChange: SS (ITER=1) (NRM,RELC): ( 1.0 2.0 )",
        "       1 0.3000E+00",
        "ComputeChange: NS (ITER=1) (NRM,RELC): ( 1.0 2.0 ) :: heat equation",
        "       1 0.4000E+00",
    ]
    assert [residual.solver for residual in _residuals(lines, MGDYN_3D)] == ["mgdyncalc", "mgdyncalc", "mgdyn", "mgdyn"]


# --- heartbeat ---


def _progress(elapsed: float, *, started: float = 0.0, max_iterations: int | None = 5000, tolerance: float | None = 1e-8) -> LinearProgress:
    return LinearProgress(
        solver="linear elasticity",
        method="CG",
        iteration=1339,
        max_iterations=max_iterations,
        residual=3.138e-08,
        tolerance=tolerance,
        best_residual=2.698e-08,
        best_iteration=658,
        started=started,
        elapsed=elapsed,
        fraction=None,
    )


def test_heartbeat_beats_every_thirty_seconds_of_a_solve() -> None:
    renderer = ConvergenceRenderer()
    beats = [renderer.heartbeat(_progress(elapsed)) for elapsed in (29.0, 30.0, 45.0, 60.0)]

    assert [beat is not None for beat in beats] == [False, True, False, True]
    line = beats[1]
    assert line is not None
    assert "1339/5000" in line
    assert "r = 3.138e-08 / 1e-08" in line
    assert "best 2.698e-08 @ 658" in line
    assert line.endswith("30 s")


def test_heartbeat_restarts_its_throttle_for_a_new_solve() -> None:
    renderer = ConvergenceRenderer()
    assert renderer.heartbeat(_progress(50.0)) is not None
    assert renderer.heartbeat(_progress(10.0, started=100.0)) is None
    assert renderer.heartbeat(_progress(30.0, started=100.0)) is not None


def test_heartbeat_pads_an_unknown_cap_and_tolerance() -> None:
    renderer = ConvergenceRenderer()
    known = renderer.heartbeat(_progress(30.0))
    unknown = ConvergenceRenderer().heartbeat(_progress(30.0, max_iterations=None, tolerance=None))

    assert known is not None and unknown is not None
    assert unknown.index("best") == known.index("best")
    assert "/" not in unknown
