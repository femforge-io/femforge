"""The conditions sidecar records what each group received from the sif, in the solve frame, and round-trips."""

from __future__ import annotations

from pathlib import Path

import pytest

from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.elmer.physics import Physics
from femforge.elmer.sections import SifSection
from femforge.elmer.sim import Sim
from femforge.frames import Axis, Axisymmetric, Cartesian2D, Frame, Solid
from femforge.materials.air import Air
from femforge.materials.neodymium import N52
from femforge.materials.pcb import FR4
from femforge.materials.steel import Steel_316
from femforge.meshing.groups import PhysicalGroup
from femforge.physical.conditions import FixedDisplacement, FixedTemperature, HeatSource, Magnetization, SurfaceForce
from femforge.physical.domain import EntityTag
from femforge.physical.units import U, quantity
from femforge.run.conditions import ConditionRecord, ConditionsFile, GroupConditions, SifKeyword, SolveComponent
from tests.elmer.test_sif_coil_magnetostatics import _coil_boundary_groups, _coil_groups
from tests.elmer.test_sif_smoke import FakeBoundaryGroup, FakePhysicalGroup


def _capture(tmp_path: Path, physics: Physics, groups: list[PhysicalGroup], boundaries: list[FakeBoundaryGroup], tags: list[EntityTag], frame: Frame = Solid()) -> ConditionsFile:
    sim = Sim(groups, physics=physics, boundary_groups=boundaries, frame=frame)  # pyright: ignore[reportArgumentType]  # structural fake
    sim.generate()
    sim.write(str(tmp_path))

    return ConditionsFile.capture(sim, tags)


def _flexure(tmp_path: Path) -> ConditionsFile:
    body = EntityTag(selector={TagKey.NAME: "Flexure"})
    right = EntityTag(selector={TagKey.NAME: "RightFace"}, conditions=[SurfaceForce(force=quantity(Vec3(100, 0, 0), "N"))])
    left = EntityTag(selector={TagKey.NAME: "LeftFace"}, conditions=[FixedDisplacement()])
    boundaries = [
        FakeBoundaryGroup(11, "LEFTFACE", ("FLEXURE",), [101], tags=[left], measure=0.02, named=True),
        FakeBoundaryGroup(12, "RIGHTFACE", ("FLEXURE",), [102], tags=[right], measure=0.02, named=True),
        FakeBoundaryGroup(13, "FLEXURE__EXT", ("FLEXURE",), [103]),
    ]
    frame = Cartesian2D(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1)), thickness=10 * U.mm)

    return _capture(tmp_path, Physics.LINEAR_ELASTICITY, [FakePhysicalGroup(1, "FLEXURE", Steel_316(), [body])], boundaries, [body, right, left], frame)


def _only(group: GroupConditions | None) -> ConditionRecord:
    assert group is not None and len(group.conditions) == 1

    return group.conditions[0]


def test_a_surface_force_records_its_traction_and_its_solve_share(tmp_path: Path) -> None:
    right = _only(_flexure(tmp_path).boundary("RIGHTFACE"))

    assert (right.kind, right.section, right.block, right.block_name, right.tag) == ("surface_force", SifSection.BOUNDARY_CONDITION, 2, "RIGHTFACE_surface_force", 1)
    assert right.selector == {TagKey.NAME: "RightFace"}
    assert right.keywords == [SifKeyword(name="Force 1", value=500000.0), SifKeyword(name="Force 2", value=0.0)]
    assert [(component.index, component.value) for component in right.solve_components] == [(1, pytest.approx(10000.0)), (2, 0.0)]
    assert isinstance(right.condition, SurfaceForce)
    assert right.dropped == []


def test_a_clamp_records_the_axis_its_frame_drops(tmp_path: Path) -> None:
    left = _only(_flexure(tmp_path).boundary("LEFTFACE"))

    assert (left.kind, left.block, left.tag) == ("fixed_displacement", 1, 2)
    assert left.keywords == [SifKeyword(name="Displacement 1", value=0.0), SifKeyword(name="Displacement 2", value=0.0)]
    assert left.dropped == ["z"]


def test_every_group_is_listed_even_without_a_condition(tmp_path: Path) -> None:
    captured = _flexure(tmp_path)

    assert [group.group for group in captured.bodies] == ["FLEXURE"]
    assert [group.group for group in captured.boundaries] == ["LEFTFACE", "RIGHTFACE", "FLEXURE__EXT"]
    assert captured.boundary("FLEXURE__EXT") == GroupConditions(group="FLEXURE__EXT")
    assert captured.body("FLEXURE") == GroupConditions(group="FLEXURE")
    assert captured.body("NOPE") is None and captured.boundary("NOPE") is None


def test_the_sidecar_round_trips(tmp_path: Path) -> None:
    captured = _flexure(tmp_path)
    path = captured.write(tmp_path)

    assert path == tmp_path / "conditions.json"
    assert ConditionsFile.load(tmp_path) == captured
    assert isinstance(_only(ConditionsFile.load(tmp_path).boundary("RIGHTFACE")).condition, SurfaceForce)


def test_a_face_value_shadows_the_body_value_it_overrides(tmp_path: Path) -> None:
    body = EntityTag(selector={TagKey.NAME: "Box"}, conditions=[FixedTemperature(value=300 * U.K)])
    tip = EntityTag(selector={TagKey.NAME: "Tip"}, conditions=[FixedTemperature(value=350 * U.K)])
    boundaries = [FakeBoundaryGroup(11, "BOX__EXT", ("BOX",), [101]), FakeBoundaryGroup(12, "TIP", ("BOX",), [102], tags=[tip], measure=1e-4, named=True)]
    captured = _capture(tmp_path, Physics.THERMAL, [FakePhysicalGroup(1, "BOX", FR4(), [body])], boundaries, [body, tip])
    group = captured.boundary("TIP")

    assert group is not None
    assert [(record.tag, record.block_name) for record in group.conditions] == [(0, "TIP_fixed_temperature_fixed_temperature"), (1, "TIP_fixed_temperature_fixed_temperature")]
    assert group.conditions[0].keywords == [SifKeyword(name="Temperature", value=300.0, shadowed=True)]
    assert group.conditions[1].keywords == [SifKeyword(name="Temperature", value=350.0, shadowed=False)]
    assert _only(captured.boundary("BOX__EXT")).keywords == [SifKeyword(name="Temperature", value=300.0)]


def test_a_component_shared_by_several_bodies_shadows_nothing(tmp_path: Path) -> None:
    groups = _coil_groups()
    unique: dict[int, EntityTag] = {id(tag): tag for group in groups for tag in group.tags}
    captured = _capture(tmp_path, Physics.COIL_MAGNETOSTATICS, groups, _coil_boundary_groups(), list(unique.values()))

    def _drives(name: str) -> list[ConditionRecord]:
        group = captured.body(name)
        assert group is not None

        return [record for record in group.conditions if record.kind == "coil_drive"]

    drives: list[ConditionRecord] = [record for name in ("COPPER_PHASE0", "COPPER_PHASE0_ENTRY0", "COPPER_PHASE0_EXIT0") for record in _drives(name)]

    assert len(drives) == 3
    assert {(record.block, record.block_name) for record in drives} == {(1, "Phase0_coil")}
    assert all(not keyword.shadowed for record in drives for keyword in record.keywords)
    assert [keyword.name for keyword in drives[0].keywords] == ["Desired Coil Current", "Coil Type"]


def test_a_body_force_lands_in_the_body_force_block(tmp_path: Path) -> None:
    body = EntityTag(selector={TagKey.NAME: "Box"}, conditions=[HeatSource(power_density=1e6 * U.W / U.m**3), FixedTemperature(value=300 * U.K)])
    captured = _capture(tmp_path, Physics.THERMAL, [FakePhysicalGroup(1, "BOX", FR4(), [body])], [FakeBoundaryGroup(11, "BOX__EXT", ("BOX",), [101])], [body])
    source = _only(captured.body("BOX"))

    assert (source.kind, source.section, source.block, source.block_name) == ("heat_source", SifSection.BODY_FORCE, 1, "BOX_body_force")
    assert source.keywords == [SifKeyword(name="Volumetric Heat Source", value=1e6)]


def test_a_magnet_records_its_material_and_its_automatic_force_component(tmp_path: Path) -> None:
    north = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 1, 0))])
    groups: list[PhysicalGroup] = [FakePhysicalGroup(1, "N52_MAG_N", N52(), [north]), FakePhysicalGroup(2, "AIR", Air(), [])]
    boundaries = [FakeBoundaryGroup(11, "AIR__EXT", ("AIR",), [101]), FakeBoundaryGroup(12, "AIR__N52_MAG_N", ("AIR", "N52_MAG_N"), [102])]
    captured = _capture(tmp_path, Physics.MAGNETOSTATICS, groups, boundaries, [north])
    magnet = captured.body("N52_MAG_N")

    assert magnet is not None
    material, force = magnet.conditions
    assert (material.kind, material.section, material.block_name, material.tag) == ("magnetization", SifSection.MATERIAL, "N52_MAG_N", 0)
    assert [keyword.name for keyword in material.keywords] == ["Magnetization 1", "Magnetization 2", "Magnetization 3"]
    assert material.solve_components == [SolveComponent(index=1, value=0.0), SolveComponent(index=2, value=1.0), SolveComponent(index=3, value=0.0)]
    assert (force.kind, force.condition, force.tag, force.selector, force.section) == ("calculate_magnetic_force", None, None, {}, SifSection.COMPONENT)
    assert force.keywords == [SifKeyword(name="Calculate Magnetic Force", value="Logical True")]

    far_field = _only(captured.boundary("AIR__EXT"))
    assert (far_field.kind, far_field.condition, far_field.keywords) == ("far_field", None, [SifKeyword(name="AV {e}", value="Real 0")])
    assert captured.boundary("AIR__N52_MAG_N") == GroupConditions(group="AIR__N52_MAG_N")


def test_an_axisymmetric_axis_records_its_symmetry_block(tmp_path: Path) -> None:
    groups: list[PhysicalGroup] = [FakePhysicalGroup(1, "AIR", Air(), [])]
    boundaries = [FakeBoundaryGroup(11, "AIR__EXT", ("AIR",), [101]), FakeBoundaryGroup(12, "AIR__AXIS", ("AIR",), [102], on_axis=True)]
    captured = _capture(tmp_path, Physics.MAGNETOSTATICS, groups, boundaries, [], Axisymmetric())
    axis = _only(captured.boundary("AIR__AXIS"))

    assert (axis.kind, axis.condition, axis.block_name) == ("symmetry_axis", None, "AIR__AXIS_symmetry_axis")
    assert axis.keywords == [SifKeyword(name="Potential", value="Real 0")]
