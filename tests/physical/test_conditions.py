"""Composable conditions: each condition's ``to_elmer()``, the discriminated-union serialization round-trip, and ``Magnetization`` normalization."""

import math
from typing import cast

import pytest
from pydantic import TypeAdapter

from femforge.frames import Axis, Axisymmetric, Solid
from femforge.common.vector import Direction, Vec3
from femforge.elmer.physics import Physics
from femforge.physical.conditions import (
    CoilTerminal,
    Condition,
    ConditionTarget,
    ConditionUnion,
    Convection,
    CurrentDensity,
    FixedPotential,
    FixedTemperature,
    ForceComponent,
    HeatFlux,
    HeatSource,
    Magnetization,
)
from femforge.physical.units import U

MU0 = 1.25663706212e-6


# --- physics / target metadata --------------------------------------------


def test_conditions_carry_physics_and_target_enums() -> None:
    assert Magnetization(direction=Direction(0, 1, 0)).physics is Physics.MAGNETOSTATICS
    assert Magnetization(direction=Direction(0, 1, 0)).target is ConditionTarget.BODY
    assert FixedTemperature(value=350 * U.K).physics is Physics.THERMAL
    assert FixedTemperature(value=350 * U.K).target is ConditionTarget.BOUNDARY


# --- Magnetization.to_elmer ------------------------------------------------


def test_magnetization_scales_unit_direction() -> None:
    cond = Magnetization(direction=Direction(0, 1, 0))
    data = cond.to_elmer(magnitude=1000.0, frame=Solid())
    assert float(data["Magnetization 1"]) == pytest.approx(0.0)
    assert float(data["Magnetization 2"]) == pytest.approx(1000.0)
    assert float(data["Magnetization 3"]) == pytest.approx(0.0)


def test_magnetization_normalizes_non_unit_direction() -> None:
    # A non-unit direction is normalized before scaling, so |M| equals the requested magnitude.
    cond = Magnetization(direction=Direction(3, 4, 0))  # |v| = 5
    data = cond.to_elmer(magnitude=10.0, frame=Solid())
    mx, my, mz = (float(data[f"Magnetization {i}"]) for i in (1, 2, 3))
    assert mx == pytest.approx(6.0)  # 10 * 3/5
    assert my == pytest.approx(8.0)  # 10 * 4/5
    assert mz == pytest.approx(0.0)
    assert math.sqrt(mx**2 + my**2 + mz**2) == pytest.approx(10.0)


def test_magnetization_projects_onto_the_axisymmetric_axis() -> None:
    # Authored along the CAD axis (here a tilted one): the whole magnitude is axial (Magnetization 2), none radial.
    axis = Axis(direction=Direction(0, 3, 4))
    data = Magnetization(direction=Direction(0, -0.6, -0.8)).to_elmer(magnitude=10.0, frame=Axisymmetric(axis=axis))
    assert data == {"Magnetization 1": "0", "Magnetization 2": "-10"}


def test_magnetization_off_the_axisymmetric_axis_raises() -> None:
    with pytest.raises(ValueError, match="not along the symmetry axis"):
        Magnetization(direction=Direction(1, 0, 1)).to_elmer(magnitude=10.0, frame=Axisymmetric())


# --- thermal carriers' to_elmer --------------------------------------------


def test_fixed_temperature_to_elmer() -> None:
    assert FixedTemperature(value=350 * U.K).to_elmer() == {"Temperature": pytest.approx(350.0)}


def test_fixed_temperature_strips_to_kelvin() -> None:
    # Authored in a non-SI unit; to_elmer hands Elmer bare kelvin.
    cond = FixedTemperature(value=25 * U.degC)
    assert cond.to_elmer()["Temperature"] == pytest.approx(298.15)


def test_heat_flux_to_elmer() -> None:
    cond = HeatFlux(value=500 * U.W / U.m**2)
    assert cond.to_elmer() == {"Heat Flux": pytest.approx(500.0)}


def test_convection_to_elmer() -> None:
    cond = Convection(coefficient=12 * U.W / (U.m**2 * U.K), ext_temperature=300 * U.K)
    data = cond.to_elmer()
    assert data["Heat Transfer Coefficient"] == pytest.approx(12.0)
    assert data["External Temperature"] == pytest.approx(300.0)


def test_fixed_potential_to_elmer_emits_electrode_keywords() -> None:
    cond = FixedPotential(value=1 * U.mV)
    assert cond.physics is Physics.CURRENT_CONDUCTION
    assert cond.target is ConditionTarget.BOUNDARY
    data = cond.to_elmer()
    # Stripped to bare volts, plus the SaveScalars flag that makes the electrode a current-measurement surface.
    assert data["Potential"] == pytest.approx(1e-3)
    assert data["Save Scalars"] == "Logical True"


# --- CurrentDensity / HeatSource body sources -------------------------------


def test_current_density_metadata_and_unit_stripping() -> None:
    cond = CurrentDensity(value=-4.5 * U.A / U.mm**2)
    assert cond.physics is Physics.MAGNETOSTATICS_2D
    assert cond.target is ConditionTarget.BODY
    # Signed azimuthal J, stripped to A/m².
    assert cond.to_elmer() == {"Current Density": pytest.approx(-4.5e6)}


def test_heat_source_constant_to_elmer() -> None:
    cond = HeatSource(power_density=2.5 * U.W / U.cm**3)
    assert cond.physics is Physics.THERMAL
    assert cond.target is ConditionTarget.BODY
    assert cond.to_elmer() == {"Volumetric Heat Source": pytest.approx(2.5e6)}


def test_heat_source_temperature_scaling_emits_matc() -> None:
    cond = HeatSource(power_density=1000 * U.W / U.m**3, temperature_coefficient=3.93e-3, reference_temperature=298.15 * U.K)
    value = cond.to_elmer()["Volumetric Heat Source"]
    assert isinstance(value, str)
    assert value.startswith("Variable Temperature")
    assert 'Real MATC "1000*(1.0+0.00393*(tx(0)-298.15))"' in value


def test_heat_source_coefficient_requires_reference_temperature() -> None:
    cond = HeatSource(power_density=1000 * U.W / U.m**3, temperature_coefficient=1e-3)
    with pytest.raises(ValueError, match="reference_temperature"):
        _ = cond.to_elmer()


def test_external_only_flags() -> None:
    # Film BCs and coil terminals attach only to a body's external shell; Dirichlet/flux conditions project everywhere.
    assert Convection.external_only is True
    assert CoilTerminal.external_only is True
    assert FixedTemperature.external_only is False
    assert HeatFlux.external_only is False
    assert FixedPotential.external_only is False


# --- ForceComponent.to_elmer ------------------------------------------------


def test_force_component_defaults_emit_only_the_force_flag() -> None:
    cond = ForceComponent()
    assert cond.physics is Physics.MAGNETOSTATICS
    assert cond.target is ConditionTarget.BODY
    assert cond.label is None
    assert cond.to_elmer() == {"Calculate Magnetic Force": "Logical True"}


def test_force_component_emits_only_what_is_set() -> None:
    assert ForceComponent(calculate_magnetic_force=False).to_elmer() == {}
    torque_only = ForceComponent(calculate_magnetic_force=False, calculate_magnetic_torque=True)
    assert torque_only.to_elmer() == {"Calculate Magnetic Torque": "Logical True"}


def test_force_component_renders_sized_vec3_keywords() -> None:
    cond = ForceComponent(calculate_magnetic_torque=True, torque_origin=Vec3(0, 0.005, 0), torque_axis=Direction(0, 1, 0))
    data = cond.to_elmer()
    assert data["Calculate Magnetic Force"] == "Logical True"
    assert data["Calculate Magnetic Torque"] == "Logical True"
    assert data["Torque Origin(3)"] == "Real 0 0.005 0"
    assert data["Torque Axis(3)"] == "Real 0 1 0"


def test_force_component_round_trips_via_discriminator() -> None:
    adapter: TypeAdapter[ConditionUnion] = TypeAdapter(ConditionUnion)
    cond = ForceComponent(label="mover", calculate_magnetic_torque=True, torque_origin=Vec3(1, 2, 3))
    dumped = cast("dict[str, object]", adapter.dump_python(cond))
    assert dumped["kind"] == "force_component"
    restored = adapter.validate_python(dumped)
    assert isinstance(restored, ForceComponent)
    assert restored.label == "mover"
    assert restored.torque_origin is not None
    assert (restored.torque_origin.x, restored.torque_origin.y, restored.torque_origin.z) == (1.0, 2.0, 3.0)
    assert restored.torque_axis is None


# --- discriminated-union round-trip ----------------------------------------


def test_condition_list_round_trips_via_discriminator() -> None:
    adapter: TypeAdapter[list[ConditionUnion]] = TypeAdapter(list[ConditionUnion])
    conditions: list[ConditionUnion] = [
        Magnetization(direction=Direction(0, 1, 0)),
        CurrentDensity(value=2 * U.A / U.mm**2),
        FixedTemperature(value=350 * U.K),
        HeatFlux(value=500 * U.W / U.m**2),
        Convection(coefficient=12 * U.W / (U.m**2 * U.K), ext_temperature=300 * U.K),
        HeatSource(power_density=1e6 * U.W / U.m**3),
        FixedPotential(value=1 * U.mV),
    ]
    dumped: list[dict[str, object]] = cast("list[dict[str, object]]", adapter.dump_python(conditions))
    # The discriminator is present so model_validate can reconstruct types.
    assert [c["kind"] for c in dumped] == ["magnetization", "current_density", "fixed_temperature", "heat_flux", "convection", "heat_source", "fixed_potential"]

    restored: list[ConditionUnion] = adapter.validate_python(dumped)
    assert [type(c) for c in restored] == [Magnetization, CurrentDensity, FixedTemperature, HeatFlux, Convection, HeatSource, FixedPotential]
    # Values survive the round trip.
    first: Condition = restored[0]
    assert isinstance(first, Magnetization)
    assert first.direction == Direction(0, 1, 0)
    second: Condition = restored[1]
    assert isinstance(second, CurrentDensity)
    assert second.to_elmer()["Current Density"] == pytest.approx(2e6)
    third: Condition = restored[2]
    assert isinstance(third, FixedTemperature)
    assert third.to_elmer()["Temperature"] == pytest.approx(350.0)


def test_a_magnetization_without_a_direction_has_nothing_to_emit() -> None:
    unmagnetized = Magnetization(direction=None)

    unmagnetized.check_frame(Axisymmetric())

    with pytest.raises(ValueError, match="without a direction"):
        unmagnetized.to_elmer(magnitude=10.0, frame=Solid())


def test_a_zero_magnitude_magnetization_emits_zeros_in_every_frame() -> None:
    cond = Magnetization(direction=Direction(0, 0, 1))

    assert cond.to_elmer(magnitude=0.0, frame=Solid()) == {"Magnetization 1": "0", "Magnetization 2": "0", "Magnetization 3": "0"}
    assert cond.to_elmer(magnitude=0.0, frame=Axisymmetric()) == {"Magnetization 1": "0", "Magnetization 2": "0"}
