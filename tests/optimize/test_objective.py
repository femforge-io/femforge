"""An Objective reduces a typed Result to the scalar the search optimizes, stripping units at the SI boundary."""

import pytest

from femforge.common.frame import Solid
from femforge.common.vector import Vec3
from femforge.optimize.objective import NetForceMagnitude, Objective
from femforge.physical.units import U, quantity
from femforge.run.result import Result


def test_net_force_magnitude_is_euclidean_norm_in_newtons() -> None:
    result = Result(frame=Solid(), status="ok", force=quantity(Vec3(3.0, 4.0, 0.0), U.N))
    assert NetForceMagnitude()(result) == pytest.approx(5.0)


def test_net_force_magnitude_converts_to_newtons() -> None:
    # Force stored in kN must be reported in N — the objective converts before stripping.
    result = Result(frame=Solid(), status="ok", force=quantity(Vec3(3.0, 4.0, 0.0), U.kN))
    assert NetForceMagnitude()(result) == pytest.approx(5000.0)


def test_net_force_magnitude_scores_missing_force_as_worst() -> None:
    result = Result(frame=Solid(), status="error", force=None)
    assert NetForceMagnitude()(result) == float("-inf")


def test_net_force_magnitude_satisfies_objective_protocol() -> None:
    assert isinstance(NetForceMagnitude(), Objective)


def test_phase_resistance_from_power_inverts_the_joule_integral() -> None:
    from femforge.optimize.objective import PhaseResistanceFromPower

    # Three phases at 1 mV with R = 2 mΩ each dissipate P = 3 V²/R = 1.5e-3 W.
    result = Result(frame=Solid(), status="ok", raw={"int: joule heating": 1.5e-3})
    objective = PhaseResistanceFromPower(drive_volts=1e-3)
    assert objective(result) == pytest.approx(2e-3)


def test_phase_resistance_from_power_scores_missing_or_bad_power_as_nan() -> None:
    import math

    from femforge.optimize.objective import PhaseResistanceFromPower

    objective = PhaseResistanceFromPower(drive_volts=1e-3)
    assert math.isnan(objective(Result(frame=Solid(), status="error", raw={})))
    assert math.isnan(objective(Result(frame=Solid(), status="ok", raw={"int: joule heating": 0.0})))


def test_phase_resistance_from_power_satisfies_objective_protocol() -> None:
    from femforge.optimize.objective import PhaseResistanceFromPower

    assert isinstance(PhaseResistanceFromPower(drive_volts=1e-3), Objective)
