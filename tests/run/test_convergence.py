"""Convergence ladder re-runs one config at increasing refinement; richardson() extrapolates a ladder's finest rungs."""

import math

import pytest

from femforge.frames import Solid
from femforge.common.vector import Vec3
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.physical.units import Quantity, U, quantity
from femforge.run.convergence import convergence_ladder, richardson
from femforge.run.result import Result

# Force magnitude (N) the fake solve returns at each refinement level; it converges toward 10 N.
_FORCE_BY_REFINEMENT: dict[float, float] = {1.0: 9.0, 2.0: 9.9, 4.0: 9.99}
_BASE_SIZE: Quantity = 1.0 * U.m


def _fake_evaluate(config: SimulationConfig) -> Result:
    # Recover the refinement level from the global mesh size and return the canned force.
    refinement: float = (_BASE_SIZE / config.mesh.global_mesh_size).m_as("dimensionless")
    magnitude: float = _FORCE_BY_REFINEMENT[round(refinement, 6)]
    return Result(frame=Solid(), status="ok", force=quantity(Vec3(magnitude, 0.0, 0.0), U.N))


def _force_magnitude(result: Result) -> Quantity:
    assert result.force is not None
    return abs(result.force.magnitude) * U.N


def test_convergence_ladder_reports_shrinking_deltas() -> None:
    base = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step", global_mesh_size=_BASE_SIZE))
    points = convergence_ladder(base, [1.0, 2.0, 4.0], _force_magnitude, _fake_evaluate)

    assert [p.refinement for p in points] == [1.0, 2.0, 4.0]
    assert points[0].delta_vs_previous is None
    delta1, delta2 = points[1].delta_vs_previous, points[2].delta_vs_previous
    assert delta1 is not None and delta2 is not None
    assert delta1 == pytest.approx((9.9 - 9.0) / 9.0)
    assert delta2 == pytest.approx((9.99 - 9.9) / 9.9)
    # Converging: each rung moves less than the last.
    assert delta2 < delta1
    assert points[1].quantity.to(U.N).magnitude == pytest.approx(9.9)


def test_convergence_ladder_does_not_mutate_base_config() -> None:
    base = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step", global_mesh_size=_BASE_SIZE))
    convergence_ladder(base, [1.0, 2.0], _force_magnitude, _fake_evaluate)
    assert base.mesh.global_mesh_size == _BASE_SIZE


# --- richardson ---------------------------------------------------------------


def _power_law(scales: list[float], limit: float, c: float, p: float) -> list[float]:
    return [limit + c * h**p for h in scales]


def test_richardson_recovers_a_second_order_law() -> None:
    scales = [2.0, 1.0, 0.5]
    estimate = richardson(scales, _power_law(scales, limit=5.0, c=2.0, p=2.0))
    assert estimate.observed_order == pytest.approx(2.0, rel=1e-6)
    assert estimate.extrapolated_value == pytest.approx(5.0, rel=1e-9)
    assert estimate.uncertainty == pytest.approx(2.0 * 0.5**2, rel=1e-6)


def test_richardson_handles_first_order_and_nonuniform_ratios() -> None:
    scales = [1.0, 0.4, 0.25]  # ratios 2.5 and 1.6
    estimate = richardson(scales, _power_law(scales, limit=10.0, c=-3.0, p=1.0))
    assert estimate.observed_order == pytest.approx(1.0, rel=1e-6)
    assert estimate.extrapolated_value == pytest.approx(10.0, rel=1e-9)


def test_richardson_uses_the_finest_three_rungs_regardless_of_input_order() -> None:
    scales = [0.5, 4.0, 1.0, 2.0]
    values = _power_law(scales, limit=5.0, c=2.0, p=2.0)
    values[scales.index(4.0)] = 999.0  # the coarsest rung is off the law entirely; it must not participate
    estimate = richardson(scales, values)
    assert estimate.observed_order == pytest.approx(2.0, rel=1e-6)
    assert estimate.extrapolated_value == pytest.approx(5.0, rel=1e-9)


def test_richardson_identical_finest_rungs_mean_converged() -> None:
    estimate = richardson([2.0, 1.0, 0.5], [9.0, 9.9, 9.9])
    assert estimate.observed_order == math.inf
    assert estimate.extrapolated_value == pytest.approx(9.9)
    assert estimate.uncertainty == 0.0


def test_richardson_rejects_non_shrinking_deltas() -> None:
    with pytest.raises(ValueError, match="not shrinking"):
        _ = richardson([2.0, 1.0, 0.5], [1.0, 2.0, 1.5])  # oscillating
    with pytest.raises(ValueError, match="not shrinking"):
        _ = richardson([2.0, 1.0, 0.5], [1.0, 1.1, 1.4])  # deltas growing


def test_richardson_rejects_incredible_orders() -> None:
    # A delta ratio of 1000 at refinement ratio 1.05 would imply order ~141.
    with pytest.raises(ValueError, match="not credible"):
        _ = richardson([1.1025, 1.05, 1.0], [1001.0, 1.0, 0.0])


def test_richardson_validates_its_inputs() -> None:
    with pytest.raises(ValueError, match="differ in length"):
        _ = richardson([1.0, 0.5], [1.0, 2.0, 3.0])
    with pytest.raises(ValueError, match="at least three"):
        _ = richardson([1.0, 0.5], [1.0, 2.0])
    with pytest.raises(ValueError, match="positive"):
        _ = richardson([1.0, 0.5, 0.0], [1.0, 2.0, 3.0])
    with pytest.raises(ValueError, match="repeat"):
        _ = richardson([1.0, 0.5, 0.5], [1.0, 2.0, 3.0])
