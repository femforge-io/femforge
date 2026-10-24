"""Composable conditions: each condition's ``to_elmer()``, the discriminated-union serialization round-trip, and ``Magnetization`` normalization."""

import math
from typing import Annotated, Literal, cast

import pytest
from pydantic import TypeAdapter

from femforge.frames import Axis, Axisymmetric, Cartesian2D, Frame, Solid
from femforge.common.vector import Direction, Vec3
from femforge.elmer.physics import Physics
from femforge.elmer.sections import SifSection
from femforge.physical.conditions import (
    BoundarySite,
    CalculateMagneticForce,
    CoilDrive,
    CoilTerminal,
    Condition,
    ConditionGlyph,
    ConditionTarget,
    ConditionUnion,
    Convection,
    CurrentDensity,
    ElmerKeyword,
    FixedDisplacement,
    FixedVoltage,
    FixedTemperature,
    HeatFlux,
    HeatSource,
    JouleHeat,
    Magnetization,
    SummaryLine,
    SurfaceForce,
    shipped_conditions,
)
from femforge.physical.units import Temperature, U, quantity

MU0 = 1.25663706212e-6


# --- physics / target metadata --------------------------------------------


def test_conditions_carry_physics_and_target_enums() -> None:
    assert Magnetization(direction=Direction(0, 1, 0)).physics is Physics.MAGNETOSTATICS
    assert Magnetization(direction=Direction(0, 1, 0)).target is ConditionTarget.BODY
    assert FixedTemperature(value=350 * U.K).physics is Physics.THERMAL
    assert FixedTemperature(value=350 * U.K).target is ConditionTarget.BOUNDARY


def test_the_shipped_conditions_are_the_union_members_in_order() -> None:
    assert shipped_conditions() == (
        Magnetization,
        CurrentDensity,
        FixedTemperature,
        HeatFlux,
        Convection,
        HeatSource,
        FixedVoltage,
        JouleHeat,
        CoilDrive,
        CoilTerminal,
        CalculateMagneticForce,
        FixedDisplacement,
        SurfaceForce,
    )


@pytest.mark.parametrize("kind", shipped_conditions())
def test_a_condition_lands_in_a_boundary_section_exactly_when_it_targets_a_boundary(kind: type[Condition]) -> None:
    target: ConditionTarget = kind.model_construct().target

    assert (kind.section is SifSection.BOUNDARY_CONDITION) is (target is ConditionTarget.BOUNDARY)


def test_the_sections_route_material_component_and_body_force_conditions() -> None:
    assert Magnetization.section is SifSection.MATERIAL
    assert CoilDrive.section is CalculateMagneticForce.section is SifSection.COMPONENT
    assert CurrentDensity.section is HeatSource.section is JouleHeat.section is SifSection.BODY_FORCE


def test_a_condition_emits_the_same_keywords_on_any_boundary_by_default() -> None:
    cond = FixedTemperature(value=350 * U.K)
    site = BoundarySite(name="TIP", measure=1e-4, frame=Solid())

    assert cond.on_boundary(site) == cond.to_elmer()


class _OptionalTemperature(Condition):
    """A condition whose keyword field may be unset."""

    kind: Literal["optional_temperature"] = "optional_temperature"
    physics: Physics = Physics.THERMAL
    target: ConditionTarget = ConditionTarget.BOUNDARY
    value: Annotated[Temperature | None, ElmerKeyword("Temperature", U.K)] = None


def test_an_unset_keyword_field_emits_nothing() -> None:
    assert _OptionalTemperature().to_elmer() == {}
    assert _OptionalTemperature(value=300 * U.K).to_elmer() == {"Temperature": pytest.approx(300.0)}


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


def test_fixed_voltage_to_elmer_emits_electrode_keywords() -> None:
    cond = FixedVoltage(value=1 * U.mV)
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
    assert FixedVoltage.external_only is False


# --- CalculateMagneticForce.to_elmer ----------------------------------------


def test_calculate_magnetic_force_defaults_emit_only_the_force_flag() -> None:
    cond = CalculateMagneticForce()
    assert cond.physics is Physics.MAGNETOSTATICS
    assert cond.target is ConditionTarget.BODY
    assert cond.label is None
    assert cond.to_elmer() == {"Calculate Magnetic Force": "Logical True"}


def test_calculate_magnetic_force_emits_only_what_is_set() -> None:
    assert CalculateMagneticForce(calculate_magnetic_force=False).to_elmer() == {}
    torque_only = CalculateMagneticForce(calculate_magnetic_force=False, calculate_magnetic_torque=True)
    assert torque_only.to_elmer() == {"Calculate Magnetic Torque": "Logical True"}


def test_calculate_magnetic_force_renders_sized_vec3_keywords() -> None:
    cond = CalculateMagneticForce(calculate_magnetic_torque=True, torque_origin=Vec3(0, 0.005, 0), torque_axis=Direction(0, 1, 0))
    data = cond.to_elmer()
    assert data["Calculate Magnetic Force"] == "Logical True"
    assert data["Calculate Magnetic Torque"] == "Logical True"
    assert data["Torque Origin(3)"] == "Real 0 0.005 0"
    assert data["Torque Axis(3)"] == "Real 0 1 0"


def test_calculate_magnetic_force_round_trips_via_discriminator() -> None:
    adapter: TypeAdapter[ConditionUnion] = TypeAdapter(ConditionUnion)
    cond = CalculateMagneticForce(label="mover", calculate_magnetic_torque=True, torque_origin=Vec3(1, 2, 3))
    dumped = cast("dict[str, object]", adapter.dump_python(cond))
    assert dumped["kind"] == "calculate_magnetic_force"
    restored = adapter.validate_python(dumped)
    assert isinstance(restored, CalculateMagneticForce)
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
        FixedVoltage(value=1 * U.mV),
        FixedDisplacement(z=None),
        SurfaceForce(force=quantity(Vec3(0, 0, 100), "N")),
    ]
    dumped: list[dict[str, object]] = cast("list[dict[str, object]]", adapter.dump_python(conditions))
    # The discriminator is present so model_validate can reconstruct types.
    assert [c["kind"] for c in dumped] == ["magnetization", "current_density", "fixed_temperature", "heat_flux", "convection", "heat_source", "fixed_voltage", "fixed_displacement", "surface_force"]

    restored: list[ConditionUnion] = adapter.validate_python(dumped)
    assert [type(c) for c in restored] == [Magnetization, CurrentDensity, FixedTemperature, HeatFlux, Convection, HeatSource, FixedVoltage, FixedDisplacement, SurfaceForce]
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
    roller: Condition = restored[7]
    assert isinstance(roller, FixedDisplacement)
    assert roller.on_boundary(BoundarySite(name="ROOT", measure=None, frame=Solid())) == {"Displacement 1": 0.0, "Displacement 2": 0.0}


# --- linear elasticity -------------------------------------------------------


def test_a_fixed_displacement_clamps_every_direction_by_default() -> None:
    clamp = FixedDisplacement()

    assert clamp.physics is Physics.LINEAR_ELASTICITY and clamp.target is ConditionTarget.BOUNDARY
    assert clamp.on_boundary(BoundarySite(name="ROOT", measure=None, frame=Solid())) == {"Displacement 1": 0.0, "Displacement 2": 0.0, "Displacement 3": 0.0}


def test_a_free_fixed_displacement_component_is_left_out() -> None:
    roller = FixedDisplacement(x=None, y=quantity(2.0, "mm"))

    assert roller.on_boundary(BoundarySite(name="ROOT", measure=None, frame=Solid())) == {"Displacement 2": pytest.approx(0.002), "Displacement 3": 0.0}


def test_a_fixed_displacement_rejects_a_nonzero_component_the_frame_cannot_express() -> None:
    planar = Cartesian2D(thickness=10 * U.mm)

    FixedDisplacement().check_frame(Solid())
    FixedDisplacement().check_frame(planar)
    FixedDisplacement(x=quantity(1.0, "mm"), y=None).check_frame(planar)

    with pytest.raises(ValueError, match="nonzero z, which has no form in the Cartesian 2D normal to Z frame; set z=None"):
        FixedDisplacement(z=quantity(1.0, "mm")).check_frame(planar)

    with pytest.raises(ValueError, match="y=None"):
        FixedDisplacement(y=quantity(1.0, "mm")).check_frame(Axisymmetric())


def test_a_fixed_displacement_emits_only_through_on_boundary() -> None:
    with pytest.raises(ValueError, match="on_boundary"):
        _ = FixedDisplacement().to_elmer()


_THICKNESS = 10 * U.mm
_TILTED = Cartesian2D(x_dir=Direction(1, 1, 0), thickness=_THICKNESS)
_OBLIQUE_NORMAL = Cartesian2D(normal=Axis(direction=Direction(1, 1, 0)), thickness=_THICKNESS)


def _axis(x: float, y: float, z: float) -> Axis:
    return Axis(direction=Direction(x, y, z))


def _mm(value: float | None) -> object:
    return None if value is None else quantity(value, "mm")


_FREE: dict[str, object] = {"x": None, "y": None, "z": None}


@pytest.mark.parametrize(
    ("frame", "kwargs", "expected"),
    [
        (Solid(), {}, {"Displacement 1": 0.0, "Displacement 2": 0.0, "Displacement 3": 0.0}),
        (Solid(), {"y": None}, {"Displacement 1": 0.0, "Displacement 3": 0.0}),
        (Solid(), {**_FREE, "x": 1.0}, {"Displacement 1": 0.001}),
        (Cartesian2D(thickness=_THICKNESS), {}, {"Displacement 1": 0.0, "Displacement 2": 0.0}),
        (Cartesian2D(thickness=_THICKNESS), {"y": None}, {"Displacement 1": 0.0}),
        (Cartesian2D(normal=_axis(0, 0, -1), thickness=_THICKNESS), {**_FREE, "y": 1.0}, {"Displacement 2": -0.001}),
        (Cartesian2D(normal=Axis.X, thickness=_THICKNESS), {}, {"Displacement 1": 0.0, "Displacement 2": 0.0}),
        (Cartesian2D(normal=Axis.X, thickness=_THICKNESS), {"y": None}, {"Displacement 2": 0.0}),
        (Cartesian2D(normal=Axis.X, thickness=_THICKNESS), {**_FREE, "y": 1.0}, {"Displacement 1": 0.001}),
        (Cartesian2D(normal=Axis.X, thickness=_THICKNESS), {**_FREE, "z": 1.0}, {"Displacement 2": 0.001}),
        (Cartesian2D(normal=_axis(-1, 0, 0), thickness=_THICKNESS), {**_FREE, "z": 1.0}, {"Displacement 2": -0.001}),
        (Cartesian2D(normal=Axis.Y, thickness=_THICKNESS), {}, {"Displacement 1": 0.0, "Displacement 2": 0.0}),
        (Cartesian2D(normal=Axis.Y, thickness=_THICKNESS), {**_FREE, "z": 1.0}, {"Displacement 2": -0.001}),
        (Cartesian2D(normal=_axis(0, -1, 0), thickness=_THICKNESS), {**_FREE, "z": 1.0}, {"Displacement 2": 0.001}),
        (Cartesian2D(normal=Axis.X, x_dir=Direction(0, 0, 1), thickness=_THICKNESS), {**_FREE, "y": 1.0}, {"Displacement 2": -0.001}),
        (Cartesian2D(normal=Axis.X, x_dir=Direction(0, 0, 1), thickness=_THICKNESS), {**_FREE, "z": 1.0}, {"Displacement 1": 0.001}),
        (_TILTED, {}, {"Displacement 1": 0.0, "Displacement 2": 0.0}),
        (_TILTED, {"x": 1.0, "z": None}, {"Displacement 1": 7.0710678e-4, "Displacement 2": -7.0710678e-4}),
        (_OBLIQUE_NORMAL, {}, {"Displacement 1": 0.0, "Displacement 2": 0.0}),
        (_OBLIQUE_NORMAL, {**_FREE, "z": 1.0}, {"Displacement 1": 0.001}),
        (Axisymmetric(), {}, {"Displacement 1": 0.0, "Displacement 2": 0.0}),
        (Axisymmetric(), {**_FREE, "x": 1.0}, {"Displacement 1": 0.001}),
        (Axisymmetric(), {**_FREE, "z": 1.0}, {"Displacement 2": 0.001}),
        (Axisymmetric(), {"x": None, "z": None}, {}),
        (Axisymmetric(axis=_axis(0, 0, -1)), {**_FREE, "z": 1.0}, {"Displacement 2": -0.001}),
        (Axisymmetric(axis=Axis.X), {**_FREE, "x": 1.0}, {"Displacement 2": 0.001}),
        (Axisymmetric(axis=Axis.X), {**_FREE, "y": 1.0}, {"Displacement 1": 0.001}),
        (Axisymmetric(radial=Direction(0, 1, 0)), {**_FREE, "y": 1.0}, {"Displacement 1": 0.001}),
    ],
)
def test_a_fixed_displacement_fixes_the_solve_coordinate_each_axis_runs_along(frame: Frame, kwargs: dict[str, float | None], expected: dict[str, float]) -> None:
    condition = FixedDisplacement.model_validate({name: _mm(value) for name, value in kwargs.items()})

    emitted = condition.on_boundary(BoundarySite(name="ROOT", measure=None, frame=frame))

    condition.check_frame(frame)
    assert emitted == {key: pytest.approx(value, rel=1e-8, abs=1e-12) for key, value in expected.items()}


def test_a_flipped_normal_clamp_emits_a_positive_zero() -> None:
    emitted = FixedDisplacement().on_boundary(BoundarySite(name="ROOT", measure=None, frame=Cartesian2D(normal=_axis(0, 0, -1), thickness=_THICKNESS)))

    assert emitted == {"Displacement 1": 0.0, "Displacement 2": 0.0}
    assert math.copysign(1.0, cast("float", emitted["Displacement 2"])) == 1.0


_SHIPPED_FRAMES: list[Frame] = [
    Solid(),
    *(Cartesian2D(normal=_axis(*direction), thickness=_THICKNESS) for direction in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1), (1, 1, 0))),
    *(Axisymmetric(axis=_axis(*direction)) for direction in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1), (1, 1, 0))),
]


@pytest.mark.parametrize("frame", _SHIPPED_FRAMES, ids=[frame.label for frame in _SHIPPED_FRAMES])
def test_every_shipped_frame_clamps_fully_by_default(frame: Frame) -> None:
    emitted = FixedDisplacement().on_boundary(BoundarySite(name="ROOT", measure=None, frame=frame))

    assert emitted == {f"Displacement {index}": 0.0 for index in range(1, frame.model_dim + 1)}


@pytest.mark.parametrize(
    ("frame", "kwargs", "match"),
    [
        (Cartesian2D(thickness=_THICKNESS), {"z": 1.0}, "nonzero z, which has no form in the Cartesian 2D normal to Z frame; set z=None"),
        (Cartesian2D(normal=Axis.X, thickness=_THICKNESS), {"x": 1.0}, "nonzero x"),
        (Axisymmetric(), {"y": 1.0}, "nonzero y"),
        (_TILTED, {"y": None}, "constrains x but leaves y free.*x_dir"),
        (_TILTED, {**_FREE, "y": 1.0}, "constrains y but leaves x free"),
        (_OBLIQUE_NORMAL, {"y": None}, "constrains x but leaves y free"),
    ],
)
def test_a_fixed_displacement_raises_when_its_frame_cannot_pose_it(frame: Frame, kwargs: dict[str, float | None], match: str) -> None:
    condition = FixedDisplacement.model_validate({name: _mm(value) for name, value in kwargs.items()})

    with pytest.raises(ValueError, match=match):
        condition.check_frame(frame)


def test_a_fixed_displacement_drops_the_component_along_a_2d_normal() -> None:
    site = BoundarySite(name="ROOT", measure=None, frame=Cartesian2D(thickness=10 * U.mm))

    assert FixedDisplacement().on_boundary(site) == {"Displacement 1": 0.0, "Displacement 2": 0.0}
    assert FixedDisplacement(x=None).on_boundary(site) == {"Displacement 2": 0.0}


def test_a_surface_force_spreads_its_total_over_the_measured_face() -> None:
    load = SurfaceForce(force=quantity(Vec3(0, 0, 100), "N"))

    traction = load.on_boundary(BoundarySite(name="TIP", measure=1e-4, frame=Solid()))

    assert traction == {"Force 1": 0.0, "Force 2": 0.0, "Force 3": pytest.approx(1.0e6)}


@pytest.mark.parametrize("measure", [None, 0.0])
def test_a_surface_force_needs_a_measured_face(measure: float | None) -> None:
    load = SurfaceForce(force=quantity(Vec3(0, 0, 100), "N"))

    with pytest.raises(ValueError, match="TaggedFace"):
        _ = load.on_boundary(BoundarySite(name="BEAM__EXT", measure=measure, frame=Solid()))


def test_a_surface_force_has_no_area_free_emission() -> None:
    with pytest.raises(ValueError, match="on_boundary"):
        _ = SurfaceForce(force=quantity(Vec3(1, 0, 0), "N")).to_elmer()


def test_a_surface_force_checks_its_direction_against_the_frame() -> None:
    SurfaceForce(force=quantity(Vec3(1, 0, 0), "N")).check_frame(Solid())

    with pytest.raises(ValueError, match="no form"):
        SurfaceForce(force=quantity(Vec3(1, 0, 0), "N")).check_frame(Axisymmetric())


@pytest.mark.parametrize(
    ("frame", "force", "expected"),
    [
        (Axisymmetric(), Vec3(0, 0, 100), {"Force 1": 0.0, "Force 2": 159154.943}),
        (Axisymmetric(), Vec3(0, 0, -100), {"Force 1": 0.0, "Force 2": -159154.943}),
        (Axisymmetric(axis=Axis.X), Vec3(100, 0, 0), {"Force 1": 0.0, "Force 2": 159154.943}),
        (Cartesian2D(normal=Axis.X, thickness=_THICKNESS), Vec3(0, 100, 0), {"Force 1": 1e8, "Force 2": 0.0}),
        (Cartesian2D(normal=_axis(0, 0, -1), thickness=_THICKNESS), Vec3(0, 100, 0), {"Force 1": 0.0, "Force 2": -1e8}),
        (_TILTED, Vec3(100, 0, 0), {"Force 1": 7.0710678e7, "Force 2": -7.0710678e7}),
    ],
)
def test_a_surface_force_spreads_the_solve_share_of_its_solve_components(frame: Frame, force: Vec3, expected: dict[str, float]) -> None:
    traction = SurfaceForce(force=quantity(force, "N")).on_boundary(BoundarySite(name="TIP", measure=1e-4, frame=frame))

    assert traction == {key: pytest.approx(value, rel=1e-8, abs=1e-6) for key, value in expected.items()}


def test_an_axial_surface_force_has_a_form_under_an_axisymmetric_frame() -> None:
    SurfaceForce(force=quantity(Vec3(0, 0, 1), "N")).check_frame(Axisymmetric())


def test_a_magnetization_without_a_direction_has_nothing_to_emit() -> None:
    unmagnetized = Magnetization(direction=None)

    unmagnetized.check_frame(Axisymmetric())

    with pytest.raises(ValueError, match="without a direction"):
        unmagnetized.to_elmer(magnitude=10.0, frame=Solid())


def test_a_zero_magnitude_magnetization_emits_zeros_in_every_frame() -> None:
    cond = Magnetization(direction=Direction(0, 0, 1))

    assert cond.to_elmer(magnitude=0.0, frame=Solid()) == {"Magnetization 1": "0", "Magnetization 2": "0", "Magnetization 3": "0"}
    assert cond.to_elmer(magnitude=0.0, frame=Axisymmetric()) == {"Magnetization 1": "0", "Magnetization 2": "0"}


def test_a_magnetization_under_a_cartesian_2d_frame_emits_its_plane_components() -> None:
    frame = Cartesian2D(normal=Axis.X, thickness=10 * U.mm)

    assert Magnetization(direction=Direction(0, 0.6, 0.8)).to_elmer(magnitude=10.0, frame=frame) == {"Magnetization 1": "6", "Magnetization 2": "8"}
    Magnetization(direction=Direction(0, 0, 1)).check_frame(frame)

    with pytest.raises(ValueError, match="has no form in the Cartesian 2D normal to X frame"):
        Magnetization(direction=Direction(1, 0, 0)).check_frame(frame)


def test_a_current_density_drives_a_cartesian_2d_magnetostatic_solve() -> None:
    CurrentDensity(value=1e6 * U.A / U.m**2).check_frame(Cartesian2D(thickness=10 * U.mm))


# --- setup hooks: glyph, solve components, dropped fields --------------------


def test_every_shipped_condition_but_the_force_output_draws_a_glyph() -> None:
    without = [condition for condition in shipped_conditions() if condition.glyph is None]

    assert without == [CalculateMagneticForce]
    assert {condition.glyph for condition in shipped_conditions()} == {*ConditionGlyph, None}


def test_a_coil_drive_shares_the_current_glyph_and_a_coil_terminal_has_its_own() -> None:
    assert (CoilDrive.glyph, CurrentDensity.glyph) == (ConditionGlyph.CURRENT_DENSITY, ConditionGlyph.CURRENT_DENSITY)
    assert (CoilTerminal.glyph, FixedVoltage.glyph) == (ConditionGlyph.COIL_TERMINAL, ConditionGlyph.VOLTAGE)
    assert {JouleHeat.glyph, Convection.glyph, HeatSource.glyph} == {ConditionGlyph.THERMAL}


@pytest.mark.parametrize(
    ("condition", "frame", "expected"),
    [
        (FixedDisplacement(), Cartesian2D(normal=_axis(0, 0, 1), thickness=_THICKNESS), ("z",)),
        (FixedDisplacement(z=None), Cartesian2D(normal=_axis(0, 0, 1), thickness=_THICKNESS), ()),
        (FixedDisplacement(), Solid(), ()),
        (FixedDisplacement(), Cartesian2D(normal=_axis(1, 1, 0), thickness=_THICKNESS), ()),
        (FixedDisplacement(), Axisymmetric(), ("y",)),
        (FixedTemperature(value=300 * U.K), Cartesian2D(thickness=_THICKNESS), ()),
    ],
)
def test_the_dropped_fields_are_the_held_axes_without_a_solve_coordinate(condition: Condition, frame: Frame, expected: tuple[str, ...]) -> None:
    assert condition.dropped_fields(frame) == expected


def test_the_solve_components_follow_the_frame() -> None:
    flexure = Cartesian2D(normal=_axis(0, 0, 1), thickness=10 * U.mm)

    assert SurfaceForce(force=quantity(Vec3(100, 0, 0), "N")).solve_components(flexure) == ((1, pytest.approx(10000.0)), (2, 0.0))
    assert FixedDisplacement(x=None).solve_components(flexure) == ((2, 0.0),)
    assert Magnetization(direction=Direction(0, 0, 1)).solve_components(Axisymmetric()) == ((1, 0.0), (2, 1.0))
    assert Magnetization(direction=None).solve_components(Solid()) == ()
    assert FixedTemperature(value=300 * U.K).solve_components(flexure) == ()


# --- the Setup tab copy ------------------------------------------------------

SUMMARY_SAMPLES: tuple[Condition, ...] = (
    Magnetization(direction=Direction(0, 0, 1)),
    CurrentDensity(value=quantity(-2.5e6, "A/m**2")),
    FixedTemperature(value=quantity(300, "K")),
    HeatFlux(value=quantity(500, "W/m**2")),
    Convection(coefficient=quantity(10, "W/(m**2*K)"), ext_temperature=quantity(293, "K")),
    HeatSource(power_density=quantity(1.2e6, "W/m**3")),
    FixedVoltage(value=quantity(0.5, "V")),
    JouleHeat(),
    CoilDrive(current=quantity(12, "A")),
    CoilTerminal(role="end"),
    CalculateMagneticForce(),
    FixedDisplacement(),
    SurfaceForce(force=quantity(Vec3(100, 0, 0), "N")),
)


def _read(lines: tuple[SummaryLine, ...]) -> list[list[str]]:
    """Each summary line as its labels and values, in reading order."""
    return [[text for part in line for text in (part.label, part.value) if text] for line in lines]


def test_every_shipped_condition_reads_as_at_least_one_line() -> None:
    assert {type(sample) for sample in SUMMARY_SAMPLES} == set(shipped_conditions())
    assert all(len(sample.summary()) >= 1 for sample in SUMMARY_SAMPLES)


def test_a_surface_force_reads_its_total_then_the_traction_the_face_received() -> None:
    pull = SurfaceForce(force=quantity(Vec3(100, 0, 0), "N"))

    assert _read(pull.summary({"Force 1": 500000.0, "Force 2": 0.0})) == [["100 N Along (1, 0, 0)"], ["Traction", "(500, 0) kPa"]]
    assert _read(pull.summary({"Force 1": 1.0, "Force 2": -2.0, "Force 3": 0.5})) == [["100 N Along (1, 0, 0)"], ["Traction", "(1, -2, 0.5) Pa"]]
    assert _read(pull.summary()) == [["100 N Along (1, 0, 0)"]], "without the emitted keywords there is no traction to read"


def test_a_traction_reads_the_sign_the_frame_mapping_gave_it() -> None:
    sideways = Cartesian2D(normal=Axis(direction=Direction(1, 0, 0)), thickness=quantity(10, "mm"))
    pull = SurfaceForce(force=quantity(Vec3(0, -100, 0), "N"))
    emitted = pull.on_boundary(BoundarySite(name="TIP", measure=2e-2, frame=sideways))
    share = dict(pull.solve_components(sideways))

    assert share[1] < 0.0, "authored -Y runs along the solve +X of a plane normal to X"
    assert _read(pull.summary(emitted)) == [["100 N Along (0, -1, 0)"], ["Traction", "(-500, 0) kPa"]]


def test_a_fixed_displacement_reads_each_axis_with_its_component() -> None:
    clamp = FixedDisplacement().summary()[0]
    roller = FixedDisplacement(x=quantity(0.1, "mm"), y=None).summary()[0]

    assert [(part.label, part.value, part.component) for part in clamp] == [("X", "0", "x"), ("Y", "0", "y"), ("Z", "0", "z")]
    assert [part.value for part in roller] == ["0.1 mm", "Free", "0"]


def _solved(condition: FixedDisplacement, frame: Frame) -> list[tuple[str, str, str | None]]:
    """The summary of what ``condition`` emitted on a boundary of ``frame``, as label, value and authored component."""
    emitted = condition.on_boundary(BoundarySite(name="FACE", measure=None, frame=frame))

    return [(part.label, part.value, part.component) for part in condition.summary(emitted, frame=frame)[0]]


def test_a_fixed_displacement_reads_what_each_solve_coordinate_received() -> None:
    roller = FixedDisplacement(x=quantity(-2, "mm"), y=None, z=None)

    assert _solved(FixedDisplacement(), Solid()) == [("X", "0", "x"), ("Y", "0", "y"), ("Z", "0", "z")]
    assert _solved(roller, Solid()) == [("X", "-2 mm", "x"), ("Y", "Free", "y"), ("Z", "Free", "z")]
    assert _read(roller.summary({"Displacement 1": -0.002})) == [["X", "-2 mm", "Y", "Free", "Z", "Free"]], "no frame reads the identity frame"


def test_a_fixed_displacement_reads_the_authored_axes_whatever_the_frame_solves() -> None:
    sideways = Cartesian2D(normal=Axis(direction=Direction(1, 0, 0)), thickness=quantity(10, "mm"))
    flipped = Cartesian2D(normal=Axis(direction=Direction(0, 0, -1)), thickness=quantity(10, "mm"))
    lifted = FixedDisplacement(y=quantity(1, "mm"))

    assert _solved(FixedDisplacement(y=None), sideways) == [("X", "–", "x"), ("Y", "Free", "y"), ("Z", "0", "z")]
    assert _solved(FixedDisplacement(z=None), sideways) == [("X", "–", "x"), ("Y", "0", "y"), ("Z", "Free", "z")]
    assert lifted.on_boundary(BoundarySite(name="FACE", measure=None, frame=flipped)) == {"Displacement 1": 0.0, "Displacement 2": -0.001}
    assert _solved(lifted, flipped) == [("X", "0", "x"), ("Y", "1 mm", "y"), ("Z", "–", "z")], "the sign the frame gave the solve Y is restored"
    assert _solved(FixedDisplacement(), Axisymmetric()) == [("X", "0", "x"), ("Y", "–", "y"), ("Z", "0", "z")]
    assert FixedDisplacement().dropped_fields(Axisymmetric()) == ("y",), "the dash marks exactly the axis the record drops"


def test_an_axis_oblique_to_the_solve_axes_reads_its_authored_value() -> None:
    oblique = Cartesian2D(normal=Axis(direction=Direction(0, 0, 1)), x_dir=Direction(1, 1, 0), thickness=quantity(10, "mm"))
    held = FixedDisplacement(x=quantity(1, "mm"), y=quantity(1, "mm"), z=None)

    assert _solved(held, oblique) == [("X", "1 mm", "x"), ("Y", "1 mm", "y"), ("Z", "Free", "z")]


def test_a_condition_with_several_values_labels_each_with_its_glyph() -> None:
    cooled = Convection(coefficient=quantity(10, "W/(m**2*K)"), ext_temperature=quantity(293, "K"))

    assert _read(cooled.summary()) == [["h", "10 W/(m²·K)", "T", "293 K"]]
    assert _read(FixedVoltage(value=quantity(0.5, "V")).summary()) == [["0.5 V"]], "a single value needs no glyph"
    assert _read(JouleHeat().summary()) == [[]], "a flag reads as its kind alone"


def test_a_magnet_reads_its_direction_and_the_remanence_it_was_emitted_with() -> None:
    magnet = Magnetization(direction=Direction(0, 0, 1))
    emitted = {"Magnetization 1": "0", "Magnetization 2": f"{1.32 / MU0:.6g}", "Magnetization 3": "Real MATC"}

    assert _read(magnet.summary(emitted)) == [["Along (0, 0, 1)"], ["Br", "(0, 1.32, expr) T"]], "an expression keeps its place in the tuple"
    assert magnet.summary(emitted)[1][0].tip == "Real MATC", "the placeholder's tooltip is the expression"
    assert _read(magnet.summary()) == [["Along (0, 0, 1)"]]
    assert _read(Magnetization(direction=None).summary()) == [["No Direction"]]


def test_the_terminal_and_the_force_output_read_as_words() -> None:
    assert _read(CoilTerminal(role="start").summary()) == [["Start"]]
    assert _read(CalculateMagneticForce().summary()) == [["(Output, No Glyph)"]]
