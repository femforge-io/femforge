"""StallDetector: the repeat and no-progress tests, their resets, and replays of real and synthetic residual streams."""

from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import Path

import pytest

from femforge.elmer.solver_log import (
    STALL_REPEAT_ITERATIONS,
    STALL_TAIL,
    ConvergenceSample,
    LinearResidual,
    LinearTarget,
    SolverLogParser,
    StallDetector,
    StallReason,
    StallVerdict,
)


FIXTURES = Path(__file__).parent / "fixtures"
SOLVER = "linear elasticity"
CG = LinearTarget(solver=SOLVER, iterative=True, method="CG", tolerance=1e-8, max_iterations=5000)
BICGSTAB = LinearTarget(solver=SOLVER, iterative=True, method="BiCGStab", tolerance=1e-8, max_iterations=5000)


def _sample(iteration: int, residual: float, *, solver: str | None = SOLVER) -> LinearResidual:
    return LinearResidual(iteration=iteration, relative_residual=residual, wall_time=float(iteration), solver=solver)


def _first_verdict(detector: StallDetector, values: Iterable[float], *, start: int = 1, solver: str | None = SOLVER) -> StallVerdict | None:
    for iteration, value in enumerate(values, start=start):
        verdict = detector.feed(_sample(iteration, value, solver=solver))
        if verdict is not None:
            return verdict

    return None


def _printed(value: float) -> float:
    return float(f"{value:.4E}")


def _alternating(level: float, count: int) -> list[float]:
    return [level * (1.02 if index % 2 == 0 else 0.98) for index in range(count)]


def _replay(path: Path, targets: tuple[LinearTarget, ...]) -> StallVerdict | None:
    parser = SolverLogParser(capture_linear=True, linear_targets=targets)
    detector = StallDetector(targets)
    for line in path.read_text(encoding="utf-8").splitlines():
        event = parser.feed(line)
        if isinstance(event, ConvergenceSample):
            detector.end_solve()
        elif isinstance(event, LinearResidual):
            _ = detector.feed(event)

    return detector.verdict


def test_repeated_fires_at_a_span_of_250_iterations_not_249() -> None:
    detector = StallDetector((CG,))
    falling = [1.0 / iteration for iteration in range(1, 10)]

    assert _first_verdict(detector, [*falling, *[1e-3] * STALL_REPEAT_ITERATIONS]) is None
    verdict = detector.feed(_sample(10 + STALL_REPEAT_ITERATIONS, 1e-3))

    assert verdict is not None
    assert verdict.reason is StallReason.REPEATED
    assert verdict.iteration == 260


def test_no_progress_needs_a_best_above_ten_times_the_tolerance() -> None:
    assert _first_verdict(StallDetector((CG,)), _alternating(9e-8, 5000)) is None

    verdict = _first_verdict(StallDetector((CG,)), _alternating(1.1e-7, 5000))
    assert verdict is not None and verdict.reason is StallReason.NO_PROGRESS


def test_no_progress_waits_until_the_window_reaches_back_to_the_first_sample() -> None:
    detector = StallDetector((CG,))
    values = _alternating(1e-3, 5000)

    assert _first_verdict(detector, values[:500]) is None
    verdict = detector.feed(_sample(501, values[500]))

    assert verdict is not None and verdict.reason is StallReason.NO_PROGRESS


def test_a_lower_iteration_starts_a_new_solve() -> None:
    detector = StallDetector((CG,))

    assert _first_verdict(detector, [1e-3] * 200) is None
    assert _first_verdict(detector, [1e-3] * 200) is None


def test_end_solve_starts_a_new_solve() -> None:
    detector = StallDetector((CG,))

    assert _first_verdict(detector, [1e-3] * 200) is None
    assert detector.progress is not None

    detector.end_solve()
    assert detector.progress is None
    assert _first_verdict(detector, [1e-3] * 200, start=201) is None


def test_a_new_solver_starts_a_new_solve() -> None:
    detector = StallDetector((CG,))

    assert _first_verdict(detector, [1e-3] * 200) is None
    assert _first_verdict(detector, [1e-3] * 200, start=201, solver="mgdyncalc") is None


def test_cg_reprinting_its_last_line_is_skipped() -> None:
    detector = StallDetector((CG,))
    assert _first_verdict(detector, [1e-2, *[1e-3] * (STALL_REPEAT_ITERATIONS - 1)]) is None

    last = _sample(STALL_REPEAT_ITERATIONS, 1e-3)
    assert detector.feed(last) is None
    assert detector.feed(_sample(STALL_REPEAT_ITERATIONS + 1, 1e-3)) is None
    verdict = detector.feed(_sample(STALL_REPEAT_ITERATIONS + 2, 1e-3))

    assert verdict is not None
    iterations = [sample.iteration for sample in verdict.tail]
    assert iterations == sorted(set(iterations))


def test_the_first_verdict_latches() -> None:
    detector = StallDetector((CG,))
    verdict = _first_verdict(detector, [1e-3] * 300)

    assert verdict is not None
    assert detector.feed(_sample(400, 1e-3)) is None
    assert detector.verdict is verdict


def test_an_unknown_solver_is_judged_on_repeats_alone() -> None:
    assert _first_verdict(StallDetector((CG,)), _alternating(1e-3, 5000), solver="mgdyncalc") is None

    verdict = _first_verdict(StallDetector((CG,)), [1e-3] * 300, solver="mgdyncalc")
    assert verdict is not None and verdict.reason is StallReason.REPEATED
    assert verdict.method is None and verdict.tolerance is None


def test_a_verdict_keeps_the_last_twenty_residuals() -> None:
    verdict = _first_verdict(StallDetector((CG,)), [1e-3] * 300)

    assert verdict is not None
    assert len(verdict.tail) == STALL_TAIL
    assert verdict.tail[-1].iteration == verdict.iteration


def test_progress_reports_the_solve_so_far() -> None:
    detector = StallDetector((CG,))
    _ = _first_verdict(detector, [1.0, 1e-4, 1e-2])

    progress = detector.progress
    assert progress is not None
    assert (progress.iteration, progress.residual, progress.best_residual, progress.best_iteration) == (3, 1e-2, 1e-4, 2)
    assert (progress.method, progress.max_iterations, progress.tolerance) == ("CG", 5000, 1e-8)
    assert (progress.started, progress.elapsed) == (1.0, 2.0)
    assert progress.fraction == pytest.approx(0.5)


def test_the_summary_names_the_solver_the_residual_and_the_test() -> None:
    repeated = StallVerdict(
        reason=StallReason.REPEATED,
        solver=SOLVER,
        method="CG",
        iteration=1339,
        residual=3.138e-08,
        best_residual=2.698e-08,
        best_iteration=658,
        tolerance=1e-08,
        tail=(),
    )
    stuck = StallVerdict(
        reason=StallReason.NO_PROGRESS,
        solver=None,
        method=None,
        iteration=550,
        residual=1.02e-3,
        best_residual=9.8e-4,
        best_iteration=52,
        tolerance=None,
        tail=(),
    )

    assert repeated.summary == "linear elasticity CG residual 3.138e-08 unchanged for 250 iterations at iteration 1339 (best 2.698e-08 at 658, tolerance 1e-08)"
    assert stuck.summary == "linear solve best 9.80e-04 improved less than 10% over the last 500 iterations at iteration 550 (residual 1.020e-03)"


def test_replaying_the_frozen_cg_solve_stops_it_at_iteration_1339() -> None:
    verdict = _replay(FIXTURES / "stall" / "lucid_cactus.log", (CG,))

    assert verdict is not None
    assert verdict.reason is StallReason.REPEATED
    assert (verdict.solver, verdict.method, verdict.iteration) == (SOLVER, "CG", 1339)
    assert verdict.residual == pytest.approx(3.138e-08)
    assert verdict.best_residual == pytest.approx(2.698e-08)
    assert verdict.best_iteration == 658


@pytest.mark.parametrize(
    ("log", "targets"),
    [
        (FIXTURES / "stall" / "blocky_prune.log", (BICGSTAB,)),
        (FIXTURES / "stall" / "humid_pita.log", (CG,)),
        (FIXTURES / "elmersolver_sample.log", (LinearTarget(solver="mgdyn", iterative=True, method="BiCGStabl", tolerance=1e-6, max_iterations=500),)),
        (FIXTURES.parent.parent / "run" / "fixtures" / "elmersolver.log", ()),
    ],
    ids=["blocky-prune", "humid-pita", "sample", "run-fixture"],
)
def test_replaying_a_converged_solve_gives_no_verdict(log: Path, targets: tuple[LinearTarget, ...]) -> None:
    assert _replay(log, targets) is None


def test_an_exact_plateau_trips_250_iterations_after_it_starts() -> None:
    decay = [_printed(math.exp(math.log(1.089e-7) * iteration / 600)) for iteration in range(1, 601)]
    verdict = _first_verdict(StallDetector((BICGSTAB,)), [*decay, *[_printed(1.089e-7)] * 4400])

    assert verdict is not None
    assert verdict.reason is StallReason.REPEATED
    assert verdict.iteration == 850


def test_an_early_stall_trips_no_progress_at_iteration_550() -> None:
    decay = [_printed(math.exp(math.log(1e-3) * iteration / 50)) for iteration in range(1, 51)]
    verdict = _first_verdict(StallDetector((BICGSTAB,)), [*decay, *(_printed(value) for value in _alternating(1e-3, 4950))])

    assert verdict is not None
    assert verdict.reason is StallReason.NO_PROGRESS
    assert verdict.iteration == 550


def test_a_noisy_late_plateau_is_not_caught() -> None:
    decay = [_printed(math.exp(math.log(1.089e-7) * iteration / 600)) for iteration in range(1, 601)]

    assert _first_verdict(StallDetector((BICGSTAB,)), [*decay, *(_printed(value) for value in _alternating(1.089e-7, 4400))]) is None


def test_a_hump_that_then_converges_gives_no_verdict() -> None:
    hump = [1.0 + 9 * math.sin(math.pi * iteration / 300) for iteration in range(1, 300)]
    fall = [math.exp(math.log(1e-9) * iteration / 400) for iteration in range(1, 401)]

    assert _first_verdict(StallDetector((CG,)), [*hump, *fall]) is None
