"""ForceComponent authoring and the auto per-magnet force Components: block emission, naming, torque keywords, validate() warnings, and the components.yml id map."""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import pytest

from femforge.common.frame import Axis, Axisymmetric, Frame, Solid
from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.elmer.config import MagnetoDynamicsCalcFieldsSolver, PhysicsConfig, SaveScalarsSolver, physics_config
from femforge.elmer.physics import Physics
from femforge.elmer.sim import Sim, SolverConfigWarning
from femforge.materials.air import Air
from femforge.materials.neodymium import N52
from femforge.meshing.groups import PhysicalGroup
from femforge.physical.conditions import ForceComponent, Magnetization
from femforge.physical.domain import EntityTag
from femforge.run.magnetic import read_component_names
from tests.elmer.test_sif_coil_magnetostatics import _coil_boundary_groups, _coil_groups
from tests.elmer.test_sif_smoke import FakePhysicalGroup


def _magnet_groups(force: ForceComponent) -> list[PhysicalGroup]:
    tag = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 1, 0)), force])
    return [
        FakePhysicalGroup(1, "N52_MAG_N", N52(), [tag]),
        FakePhysicalGroup(2, "AIR", Air(), []),
    ]


def _write(
    tmp_path: Path,
    groups: list[PhysicalGroup],
    physics: Physics = Physics.MAGNETOSTATICS,
    boundary_groups: list[object] | None = None,
    config: PhysicsConfig | None = None,
    frame: Frame = Solid(),
) -> str:
    sim = Sim(groups, physics=physics, boundary_groups=boundary_groups, config=config, frame=frame)  # pyright: ignore[reportArgumentType]  # structural fake boundary groups
    sim.generate()
    sim.write(str(tmp_path))
    return (tmp_path / "case.sif").read_text()


def _component_blocks(sif: str) -> list[str]:
    return re.findall(r"Component \d+\n(?:.*\n)*?End", sif)


def test_force_component_emits_one_component_block(tmp_path: Path) -> None:
    sif = _write(tmp_path, _magnet_groups(ForceComponent()))
    blocks = _component_blocks(sif)
    assert len(blocks) == 1
    block = blocks[0]
    assert "Calculate Magnetic Force = Logical True" in block
    assert "Master Bodies(1) = 1" in block
    # No label authored -> the authoring tag's name is the Component Name.
    assert "Name = MagnetizationN" in block
    # Torque was not requested, so none of its keywords appear.
    assert "Torque" not in block
    assert "Calculate Magnetic Torque" not in block


def test_label_overrides_the_tag_name(tmp_path: Path) -> None:
    sif = _write(tmp_path, _magnet_groups(ForceComponent(label="mover")))
    assert "Name = mover" in _component_blocks(sif)[0]


def test_shared_instance_gathers_every_matched_body(tmp_path: Path) -> None:
    # ONE authored instance whose tag matches two magnet bodies -> one Component over both sif body ids.
    force = ForceComponent()
    tag = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 1, 0)), force])
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "N52_MAG_A", N52(), [tag]),
        FakePhysicalGroup(2, "N52_MAG_B", N52(), [tag]),
        FakePhysicalGroup(3, "AIR", Air(), []),
    ]
    sif = _write(tmp_path, groups)
    blocks = _component_blocks(sif)
    assert len(blocks) == 1
    assert "Master Bodies(2) = 1 2" in blocks[0]


def test_torque_keywords_render_sized_real_vectors(tmp_path: Path) -> None:
    force = ForceComponent(calculate_magnetic_force=False, calculate_magnetic_torque=True, torque_origin=Vec3(0, 0.005, 0), torque_axis=Direction(0, 1, 0))
    sif = _write(tmp_path, _magnet_groups(force))
    block = _component_blocks(sif)[0]
    assert "Calculate Magnetic Torque = Logical True" in block
    assert "Torque Origin(3) = Real 0 0.005 0" in block
    assert "Torque Axis(3) = Real 0 1 0" in block
    assert "Calculate Magnetic Force" not in block


def test_force_component_is_never_a_body_force(tmp_path: Path) -> None:
    # Without the Body Force exclusion the force keywords would merge into a per-body Body Force block.
    sif = _write(tmp_path, _magnet_groups(ForceComponent()))
    assert "Body Force" not in sif


def test_active_under_the_2d_axisymmetric_solve(tmp_path: Path) -> None:
    # The same authored condition (physics=MAGNETOSTATICS) is active under MAGNETOSTATICS_2D via condition_physics.
    sif = _write(tmp_path, _magnet_groups(ForceComponent()), frame=Axisymmetric(axis=Axis.Y))
    assert "Calculate Magnetic Force = Logical True" in _component_blocks(sif)[0]


def test_coexists_with_a_coil_component(tmp_path: Path) -> None:
    # Under COIL_MAGNETOSTATICS a ForceComponent (physics=MAGNETOSTATICS) is active too: two Component blocks, one per kind.
    groups = _coil_groups()
    groups[3].tags.append(EntityTag(selector={"Region": "Board"}, conditions=[ForceComponent(label="board")]))

    # The coil preset ships Calculate Nodal Forces off, so authoring a ForceComponent under it must warn.
    with pytest.warns(SolverConfigWarning, match="Calculate Nodal Forces off"):
        sif = _write(tmp_path, groups, physics=Physics.COIL_MAGNETOSTATICS, boundary_groups=list(_coil_boundary_groups()))
    blocks = _component_blocks(sif)
    assert len(blocks) == 2
    coil = next(b for b in blocks if 'Coil Type = "massive"' in b)
    force = next(b for b in blocks if "Calculate Magnetic Force = Logical True" in b)
    assert coil is not force
    assert "Name = board" in force
    assert "Master Bodies(1) = 4" in force  # the FR4 body only, not the coil bodies


def _preset_without(kind: type) -> PhysicsConfig:
    config = physics_config(Physics.MAGNETOSTATICS)
    config.solvers = [solver for solver in config.solvers if not isinstance(solver, kind)]
    return config


def test_validate_warns_without_save_scalars(tmp_path: Path) -> None:
    with pytest.warns(SolverConfigWarning, match="no SaveScalarsSolver"):
        _ = _write(tmp_path, _magnet_groups(ForceComponent()), config=_preset_without(SaveScalarsSolver))


def test_validate_warns_without_calc_fields(tmp_path: Path) -> None:
    with pytest.warns(SolverConfigWarning, match="no MagnetoDynamicsCalcFieldsSolver"):
        _ = _write(tmp_path, _magnet_groups(ForceComponent()), config=_preset_without(MagnetoDynamicsCalcFieldsSolver))


def test_validate_is_silent_with_the_full_preset(tmp_path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", SolverConfigWarning)
        _ = _write(tmp_path, _magnet_groups(ForceComponent()))


def test_components_yml_written_and_read_back(tmp_path: Path) -> None:
    _ = _write(tmp_path, _magnet_groups(ForceComponent()))
    yml = tmp_path / "components.yml"
    assert yml.read_text() == "1: MagnetizationN\n"
    assert read_component_names(yml) == {1: "MagnetizationN"}


def test_no_components_means_no_components_yml(tmp_path: Path) -> None:
    # No magnets and no authored Components -> no components.yml.
    groups: list[PhysicalGroup] = [FakePhysicalGroup(1, "AIR", Air(), [])]
    _ = _write(tmp_path, groups)
    assert not (tmp_path / "components.yml").exists()


# --- auto per-magnet force Components (no authoring needed) ---


def _bare_magnet_groups() -> list[PhysicalGroup]:
    north = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 1, 0))])
    south = EntityTag(selector={TagKey.MAGNETIZATION: "S"}, conditions=[Magnetization(direction=Direction(0, -1, 0))])
    return [
        FakePhysicalGroup(1, "N52_MAG_N", N52(), [north]),
        FakePhysicalGroup(2, "N52_MAG_S", N52(), [south]),
        FakePhysicalGroup(3, "AIR", Air(), []),
    ]


@pytest.mark.parametrize("frame", [Solid(), Axisymmetric(axis=Axis.Y)])
def test_auto_component_per_magnet_group(tmp_path: Path, frame: Frame) -> None:
    # Magnet groups with no authored ForceComponent each get an auto force Component, named by group.
    sif = _write(tmp_path, _bare_magnet_groups(), frame=frame)
    blocks = _component_blocks(sif)

    assert len(blocks) == 2
    for block, name, body_id in zip(blocks, ("N52_MAG_N", "N52_MAG_S"), (1, 2)):
        assert f"Name = {name}" in block
        assert "Calculate Magnetic Force = Logical True" in block
        assert f"Master Bodies(1) = {body_id}" in block


def test_authored_force_suppresses_the_auto_component(tmp_path: Path) -> None:
    # The authored instance owns its group; only the OTHER magnet group gets the auto Component.
    groups = _bare_magnet_groups()
    groups[0].tags[0].conditions.append(ForceComponent(label="mover"))

    sif = _write(tmp_path, groups)
    blocks = _component_blocks(sif)

    assert len(blocks) == 2
    assert sum("Name = mover" in block for block in blocks) == 1
    assert sum("Name = N52_MAG_S" in block for block in blocks) == 1
    assert not any("Name = N52_MAG_N" in block for block in blocks)


def test_auto_component_only_for_magnets(tmp_path: Path) -> None:
    groups: list[PhysicalGroup] = [FakePhysicalGroup(1, "AIR", Air(), [])]
    sif = _write(tmp_path, groups)
    assert not _component_blocks(sif)


def test_validate_warns_when_nodal_forces_off(tmp_path: Path) -> None:
    # A magnet under a stack whose CalcFields skips Calculate Nodal Forces would export a zero force.
    config = physics_config(Physics.MAGNETOSTATICS)
    for solver in config.solvers:
        if isinstance(solver, MagnetoDynamicsCalcFieldsSolver):
            solver.calculate_nodal_forces = False

    with pytest.warns(SolverConfigWarning, match="Calculate Nodal Forces off"):
        _ = _write(tmp_path, _bare_magnet_groups(), config=config)
