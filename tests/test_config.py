"""The composed `config.SimulationConfig`: area sub-configs (`mesh`/`domain`/`physics`), each living with its backend, combine into one config, validate, and round-trip by concern."""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import ValidationError

from femforge.common.frame import Axisymmetric, Solid
from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.config import SimulationConfig
from femforge.elmer.config import CoordinateSystem, PhysicsSelection
from femforge.elmer.physics import Physics
from femforge.materials.properties import MaterialProperties
from femforge.meshing.config import MeshConfig
from femforge.meshing.gmsh_options import GmshOptions, MeshAlgorithm3D
from femforge.physical.conditions import ConditionUnion, CurrentDensity, ForceComponent, Magnetization
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import U


def _dig(node: object, *keys: str) -> object:
    """Walk a JSON-schema dict (whose values are `object`) by key, asserting each level is a dict."""
    for key in keys:
        assert isinstance(node, dict), f"expected a dict at {key!r}, got {type(node).__name__}"
        node = cast("dict[str, object]", node)[key]
    return node


def test_simulation_config_defaults() -> None:
    """The master config defaults to every area's own defaults, reachable by concern."""
    c: SimulationConfig = SimulationConfig()
    assert c.mesh.step == "data/geometry.step"
    assert c.mesh.global_mesh_size == 1 * U.mm
    assert c.mesh.global_mesh_size.units == U.mm
    assert isinstance(c.mesh.gmsh_options, GmshOptions)
    assert c.domain.materials == [] and c.domain.tags == []
    assert c.physics.selected is Physics.MAGNETOSTATICS


def test_simulation_config_areas_independently_meaningful() -> None:
    """Each area model stands on its own and composes into the master config unchanged."""
    mesh: MeshConfig = MeshConfig(step="data/halbach.step", global_mesh_size=0.5 * U.mm, gmsh_options=GmshOptions(algorithm_3d=MeshAlgorithm3D.HXT))
    domain: DomainConfig = DomainConfig(
        materials=[MaterialProperties(name="N52")],
        tags=[EntityTag(selector={"Region": "gap"}, mesh_size=0.1 * U.mm)],
    )
    physics: PhysicsSelection = PhysicsSelection(selected=Physics.THERMAL)

    c: SimulationConfig = SimulationConfig(mesh=mesh, domain=domain, physics=physics)
    assert c.mesh.step == "data/halbach.step"
    assert c.mesh.global_mesh_size == 0.5 * U.mm
    assert c.mesh.gmsh_options.algorithm_3d is MeshAlgorithm3D.HXT
    assert [m.name for m in c.domain.materials] == ["N52"]
    assert [t.selector for t in c.domain.tags] == [{"Region": "gap"}]
    assert c.physics.selected is Physics.THERMAL


def test_simulation_config_round_trip() -> None:
    """The nested config (with a polymorphic condition under domain.tags) round-trips through dump/validate."""
    c: SimulationConfig = SimulationConfig(
        mesh=MeshConfig(step="data/halbach.step", global_mesh_size=0.25 * U.mm),
        domain=DomainConfig(
            materials=[MaterialProperties(name="N52")],
            tags=[EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 1, 0))])],
        ),
        physics=PhysicsSelection(selected=Physics.ELECTROSTATICS),
    )
    restored: SimulationConfig = SimulationConfig.model_validate(c.model_dump())
    assert restored.mesh.step == "data/halbach.step"
    assert restored.mesh.global_mesh_size == 0.25 * U.mm
    assert [m.name for m in restored.domain.materials] == ["N52"]
    assert restored.physics.selected is Physics.ELECTROSTATICS
    # The discriminated condition union survives the round-trip with its concrete type.
    restored_condition: ConditionUnion = restored.domain.tags[0].conditions[0]
    assert isinstance(restored_condition, Magnetization)
    assert restored_condition.direction == Direction(0, 1, 0)


def test_simulation_config_schema_exports() -> None:
    """The master config exports its areas as nested $defs, keeping the discriminated conditions union under domain.tags."""
    schema: dict[str, object] = SimulationConfig.model_json_schema()
    # Each area is referenced as its own object schema.
    for area in ("mesh", "domain", "physics"):
        assert _dig(schema, "properties", area, "$ref")
    # domain.tags' EntityTag conditions remain a discriminated union after nesting.
    assert _dig(schema, "$defs", "EntityTag", "properties", "conditions", "items", "discriminator", "propertyName") == "kind"


def test_the_frame_decides_the_solved_physics_and_keeps_the_selection_as_authored() -> None:
    c: SimulationConfig = SimulationConfig(frame=Axisymmetric())
    assert c.physics.selected is Physics.MAGNETOSTATICS
    assert c.solved_physics() is Physics.MAGNETOSTATICS_2D
    assert c.resolved_stack().simulation.coordinate_system is CoordinateSystem.AXI_SYMMETRIC

    restored: SimulationConfig = SimulationConfig.model_validate_json(c.model_dump_json())
    assert isinstance(restored.frame, Axisymmetric)
    assert restored.physics.selected is Physics.MAGNETOSTATICS


def test_axisymmetric_thermal_solves_the_thermal_stack_in_axi_symmetric() -> None:
    c: SimulationConfig = SimulationConfig(frame=Axisymmetric(), physics=PhysicsSelection(selected=Physics.THERMAL))
    assert c.solved_physics() is Physics.THERMAL
    assert c.resolved_stack().simulation.coordinate_system is CoordinateSystem.AXI_SYMMETRIC


def test_switching_the_frame_back_and_forth_just_works() -> None:
    c: SimulationConfig = SimulationConfig()
    c.frame = Axisymmetric()
    assert c.solved_physics() is Physics.MAGNETOSTATICS_2D

    c.frame = Solid()
    assert c.solved_physics() is Physics.MAGNETOSTATICS
    assert c.physics.selected is Physics.MAGNETOSTATICS


def test_customize_physics_materializes_the_solved_stack_once() -> None:
    c: SimulationConfig = SimulationConfig(frame=Axisymmetric())
    stack = c.customize_physics()
    assert c.physics.overrides is stack
    assert c.customize_physics() is stack
    assert c.resolved_stack() is stack
    assert stack.simulation.coordinate_system is CoordinateSystem.AXI_SYMMETRIC
    # Without overrides each resolve is a fresh preset, so edits to one cannot leak into the next.
    assert SimulationConfig().resolved_stack() is not SimulationConfig().resolved_stack()


def test_overrides_that_disagree_with_a_reassigned_frame_raise_and_leave_the_config_as_it_was() -> None:
    customized: SimulationConfig = SimulationConfig()
    _ = customized.customize_physics()

    with pytest.raises(ValidationError, match="Coordinate System 'Cartesian'"):
        customized.frame = Axisymmetric()

    assert isinstance(customized.frame, Solid)


def test_frames_reject_what_they_cannot_solve() -> None:
    with pytest.raises(ValidationError, match="not selectable"):
        SimulationConfig(frame=Axisymmetric(), physics=PhysicsSelection(selected=Physics.MAGNETOSTATICS_2D))

    with pytest.raises(ValidationError, match="no axisymmetric form"):
        SimulationConfig(frame=Axisymmetric(), physics=PhysicsSelection(selected=Physics.ELECTROSTATICS))

    coil = DomainConfig(tags=[EntityTag(selector={TagKey.PHASE: "A"}, conditions=[CurrentDensity(value=2 * U.A / U.mm**2)])])
    with pytest.raises(ValidationError, match="CurrentDensity"):
        SimulationConfig(domain=coil)

    torque = DomainConfig(tags=[EntityTag(selector={"Region": "mover"}, conditions=[ForceComponent(calculate_magnetic_torque=True)])])
    with pytest.raises(ValidationError, match="torque"):
        SimulationConfig(frame=Axisymmetric(), domain=torque)

    assert SimulationConfig(frame=Axisymmetric(), domain=coil).solved_physics() is Physics.MAGNETOSTATICS_2D


def test_mesh_config_rejects_removed_fields() -> None:
    with pytest.raises(ValidationError, match="model_dim"):
        MeshConfig(model_dim=2)  # pyright: ignore[reportCallIssue]  # exercising the removed field


def test_mesh_sizes_default_to_millimetres_and_dump_with_their_unit() -> None:
    mesh = MeshConfig()

    assert mesh.global_mesh_size.m_as("m") == pytest.approx(0.001)
    assert mesh.model_dump()["global_mesh_size"] == "1.0 mm"


def test_mesh_sizes_read_quantity_strings() -> None:
    mesh = MeshConfig.model_validate({"global_mesh_size": "0.5 mm"})
    tag = EntityTag.model_validate({"mesh_size": "200 um"})

    assert mesh.global_mesh_size == 0.5 * U.mm
    assert tag.mesh_size is not None and tag.mesh_size.m_as("mm") == pytest.approx(0.2)


def test_mesh_sizes_reject_a_bare_number_and_say_what_to_write() -> None:
    with pytest.raises(ValidationError, match='bare number 0.001; give it units, as in "0.001 m"'):
        MeshConfig.model_validate({"global_mesh_size": 0.001})

    with pytest.raises(ValidationError, match=r"expected dimensionality \[length\]"):
        EntityTag.model_validate({"mesh_size": 0.0005})

    with pytest.raises(ValidationError, match=r"expected dimensionality \[length\], got \[temperature\]"):
        MeshConfig.model_validate({"global_mesh_size": "1 K"})


def test_a_transverse_magnetization_fails_at_config_construction() -> None:
    # Caught before any meshing: a sweep would otherwise mesh every point and fail in the sif writer.
    def magnets(direction: Direction | None) -> DomainConfig:
        return DomainConfig(tags=[EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=direction)])])

    with pytest.raises(ValidationError, match="not axisymmetric"):
        SimulationConfig(frame=Axisymmetric(), domain=magnets(Direction(1, 0, 0)))

    assert SimulationConfig(frame=Axisymmetric(), domain=magnets(Direction(0, 0, -1))).solved_physics() is Physics.MAGNETOSTATICS_2D
    assert SimulationConfig(frame=Axisymmetric(), domain=magnets(None)).solved_physics() is Physics.MAGNETOSTATICS_2D
    assert SimulationConfig(domain=magnets(Direction(1, 0, 0))).solved_physics() is Physics.MAGNETOSTATICS


def test_a_scaled_axis_direction_is_the_same_run() -> None:
    from femforge.common.frame import Axis
    from femforge.run.kinds import cache_key

    unit = SimulationConfig(frame=Axisymmetric(axis=Axis(direction=Direction(0, 0, 1))))
    scaled = SimulationConfig(frame=Axisymmetric(axis=Axis(direction=Direction(0, 0, 2))))
    assert cache_key(unit) == cache_key(scaled)


def test_the_selectable_physics_schema_omits_the_internal_axisymmetric_stack() -> None:
    schema = cast("dict[str, object]", PhysicsSelection.model_json_schema())
    selectable = cast("list[str]", _dig(schema, "properties", "selected", "enum"))
    assert Physics.MAGNETOSTATICS_2D.value not in selectable
    assert Physics.MAGNETOSTATICS.value in selectable

    with pytest.raises(ValidationError, match="not selectable"):
        PhysicsSelection(selected=Physics.MAGNETOSTATICS_2D)
