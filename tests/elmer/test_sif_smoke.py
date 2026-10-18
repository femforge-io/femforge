"""Smoke test: the magnetostatics sif generates correctly. Uses a fake PhysicalGroup so it needs no gmsh — just the typed config + materials + Sim."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from femforge.frames import Axis, Axisymmetric, Cartesian2D, Frame, PlaneState, Solid
from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.elmer.config import OperatingPoint, StressSolver, physics_config
from femforge.elmer.physics import Physics
from femforge.elmer.sim import Sim
from femforge.physical.domain import EntityTag
from femforge.meshing.groups import PhysicalGroup
from femforge.physical.conditions import (
    CalculateMagneticForce,
    ConditionUnion,
    Convection,
    CurrentDensity,
    FixedDisplacement,
    FixedTemperature,
    HeatFlux,
    HeatSource,
    Magnetization,
    SurfaceForce,
)
from femforge.materials.air import Air
from femforge.materials.copper import Copper
from femforge.materials.neodymium import N52
from femforge.materials.pcb import FR4
from femforge.materials.properties import MaterialProperties
from femforge.materials.steel import Steel_316
from femforge.physical.units import U, quantity

MU0 = 1.25663706212e-6


class FakePhysicalGroup(PhysicalGroup):
    """A 3D ``PhysicalGroup`` from an id, a name, a material and tags."""

    def __init__(self, gid: int, name: str, material: MaterialProperties, tags: list[EntityTag]) -> None:
        self.dim: int = 3
        self.id: int = gid
        self.name: str = name
        self.material = material
        self.tags: list[EntityTag] = tags
        self.entity_tags: list[int] = [gid]


class FakeBoundaryGroup:
    """Mimics meshing.boundary.BoundaryGroup without requiring gmsh (the dim-2 sibling of FakePhysicalGroup)."""

    def __init__(
        self,
        gid: int,
        name: str,
        bodies: tuple[str, ...],
        face_tags: list[int],
        on_axis: bool = False,
        tags: list[EntityTag] | None = None,
        measure: float | None = None,
        named: bool = False,
    ) -> None:
        self.dim: int = 2
        self.id: int = gid
        self.name: str = name
        self.bodies: tuple[str, ...] = bodies
        self.entity_tags: list[int] = face_tags
        self.on_axis: bool = on_axis
        self.tags: list[EntityTag] = tags or []
        self.measure: float | None = measure
        self.named: bool = named

    @property
    def is_external(self) -> bool:
        return len(self.bodies) == 1 and not self.on_axis

    @property
    def is_axis(self) -> bool:
        return self.on_axis


def _groups(n_tag: EntityTag | None = None) -> list[PhysicalGroup]:
    if n_tag is None:
        n_tag = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 1, 0))])
    return [
        FakePhysicalGroup(1, "N52_MAG_N", N52(), [n_tag]),
        FakePhysicalGroup(2, "AIR", Air(), []),
    ]


def _boundary_groups() -> list[FakeBoundaryGroup]:
    # The air box's external shell (far-field target) plus a magnet↔air interface.
    return [
        FakeBoundaryGroup(11, "AIR__EXT", ("AIR",), [101]),
        FakeBoundaryGroup(12, "AIR__N52_MAG_N", ("AIR", "N52_MAG_N"), [102]),
    ]


def _write_sif(
    physics: Physics | str = Physics.MAGNETOSTATICS,
    groups: list[PhysicalGroup] | None = None,
    validate: bool = True,
    boundary_groups: list[FakeBoundaryGroup] | None = None,
    frame: Frame = Solid(),
) -> str:
    sim = Sim(
        groups if groups is not None else _groups(),
        physics=physics,
        boundary_groups=boundary_groups,  # pyright: ignore[reportArgumentType]  # structural fake
        frame=frame,
    )
    sim.generate(validate=validate)
    d = Path(tempfile.mkdtemp())
    sim.write(str(d))
    return (d / "case.sif").read_text()


def test_magnetostatics_sif_generates() -> None:
    sif = _write_sif()
    assert "WhitneyAVSolver" in sif
    assert "Relative Permeability" in sif


def test_magnet_body_gets_magnetization_on_material() -> None:
    # The N-pointing magnet's orientation is carried by a Magnetization condition; |M| = Br/mu0 comes from the N52 material.
    # The WhitneyAVSolver reads M from the magnet's Material (not a Body Force), so the keyword lands in a Material block.
    sif = _write_sif()
    m = re.search(r"Magnetization 2 = ([-0-9.eE+]+)", sif)
    assert m, "expected a Magnetization 2 keyword for the N-pointing magnet"
    # N points +y, so |M| lands on component 2 ~ Br(300 K)/mu0 with Br(T) = 1.48 (1 - 0.0012 (T - 293.15)).
    assert float(m.group(1)) == pytest.approx(1.48 * (1 - 0.0012 * (300 - 293.15)) / MU0, rel=1e-3)
    # The magnetization is a material property, not a Body Force.
    assert "Body Force" not in sif


def test_magnet_without_direction_emits_missing_marker() -> None:
    # N52 is a magnet, but this region carries no orientation at all.
    # With validation on this raises (see test_sif_validation.py); the MISSING DIRECTION TAG marker is the validate=False fallback.
    n_tag = EntityTag(selector={TagKey.MAGNETIZATION: "N"})
    sif = _write_sif(groups=_groups(n_tag=n_tag), validate=False)
    assert "MISSING DIRECTION TAG" in sif
    assert "Magnetization 2 =" not in sif


def test_physics_accepts_enum_or_string() -> None:
    assert _write_sif(physics=Physics.MAGNETOSTATICS)
    assert _write_sif(physics="magnetostatics")


def test_bad_physics_raises_at_construction() -> None:
    with pytest.raises(ValueError):
        Sim(_groups(), physics="nonsense")


def test_duplicate_nonmagnet_material_deduped_to_one_block() -> None:
    # Two bodies of the same NON-magnet material share a single Elmer Material block (the dedup `continue` in _build_materials).
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "FR4_A", FR4(), []),
        FakePhysicalGroup(2, "FR4_B", FR4(), []),
        FakePhysicalGroup(3, "AIR", Air(), []),
    ]
    # Thermal physics: no magnet special-casing, so the dedup path is what's under test.
    sif = _write_sif(physics=Physics.THERMAL, groups=groups, validate=False)
    # Three bodies, but only two distinct materials (FR4, Air) -> two Material blocks.
    assert len(re.findall(r"\nMaterial \d+\n", sif)) == 2


def test_each_magnet_gets_its_own_material_block() -> None:
    # Under magnetostatics, a magnet's Magnetization vector is per-region, so each magnet body gets its OWN
    # Material (base props + its Magnetization) rather than the shared deduplicated block — even two identical
    # N52 magnets. Mirrors the reference, where N/S poles are distinct Materials.
    n_tag = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 1, 0))])
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "N52_MAG_A", N52(), [n_tag]),
        FakePhysicalGroup(2, "N52_MAG_B", N52(), [n_tag]),
        FakePhysicalGroup(3, "AIR", Air(), []),
    ]
    sif = _write_sif(groups=groups)
    # Two distinct magnet Materials + one Air Material = three blocks (no magnet dedup).
    assert len(re.findall(r"\nMaterial \d+\n", sif)) == 3
    # Both magnet materials carry the Magnetization vector.
    assert len(re.findall(r"Magnetization 2 = ", sif)) == 2


def test_far_field_targets_external_boundary_group() -> None:
    # The magnetostatics far field lands on a real surface id: the air box's __EXT group, folded into the uniform boundary loop (named <group>_far_field).
    sif = _write_sif(boundary_groups=_boundary_groups())
    assert "AV {e} = Real 0" in sif
    assert "AIR__EXT_far_field" in sif
    # Target Boundaries points at the AIR__EXT group id (11), not the interface (12).
    targets = re.findall(r"Target Boundaries\([^)]*\) = ([^\n]*)", sif)
    assert targets == ["11"], f"far field should target only the AIR__EXT id 11, got {targets}"


def test_no_boundary_groups_means_no_far_field() -> None:
    # Constructed standalone without boundary groups: nothing to attach to -> no boundary block.
    sif = _write_sif()
    assert "FarField" not in sif
    assert "AV {e}" not in sif


def test_far_field_only_wired_for_magnetostatics() -> None:
    # The far field is magnetostatics-only.
    sif = _write_sif(physics=Physics.THERMAL, boundary_groups=_boundary_groups(), validate=False)
    assert "AV {e}" not in sif


def _beam_groups() -> list[PhysicalGroup]:
    return [FakePhysicalGroup(1, "BEAM", Steel_316(), [])]


def _beam_boundaries() -> list[FakeBoundaryGroup]:
    root = EntityTag(selector={TagKey.NAME: "Root"}, conditions=[FixedDisplacement()])
    tip = EntityTag(selector={TagKey.NAME: "Tip"}, conditions=[SurfaceForce(force=quantity(Vec3(0, 0, 100), "N"))])

    return [
        FakeBoundaryGroup(11, "BEAM__EXT", ("BEAM",), [101]),
        FakeBoundaryGroup(12, "ROOT", ("BEAM",), [102], tags=[root], measure=1e-4, named=True),
        FakeBoundaryGroup(13, "TIP", ("BEAM",), [103], tags=[tip], measure=1e-4, named=True),
    ]


def test_linear_elasticity_sif_clamps_the_root_and_loads_the_tip() -> None:
    sif = _write_sif(physics=Physics.LINEAR_ELASTICITY, groups=_beam_groups(), boundary_groups=_beam_boundaries())

    assert "StressSolver" in sif
    assert "Displace Mesh = False" in sif
    assert "Poisson Ratio = 0.27" in sif
    assert "! ROOT_fixed_displacement" in sif and "Displacement 1 = 0.0" in sif
    assert "! TIP_surface_force" in sif and "Force 3 = 1000000.0" in sif
    assert "BEAM__EXT" not in sif


def test_linear_elasticity_is_selectable_by_string() -> None:
    assert _write_sif(physics="linear_elasticity", groups=_beam_groups(), boundary_groups=_beam_boundaries())


# --- thermal boundary conditions on adjacency-derived surfaces -----


def _thermal_groups() -> list[PhysicalGroup]:
    # A heated FR4 board inside an air box. The board carries a fixed temperature, the air a convective exchange — both boundary-target thermal conditions.
    fr4_tag = EntityTag(selector={"Region": "Board"}, conditions=[FixedTemperature(value=350 * U.K)])
    air_tag = EntityTag(selector={"Region": "Ambient"}, conditions=[Convection(coefficient=12 * U.W / (U.m**2 * U.K), ext_temperature=300 * U.K)])
    return [
        FakePhysicalGroup(1, "FR4", FR4(), [fr4_tag]),
        FakePhysicalGroup(2, "AIR", Air(), [air_tag]),
    ]


def _thermal_boundary_groups() -> list[FakeBoundaryGroup]:
    # The air box's external shell (id 11) and the air↔board interface (id 12, sorted name AIR__FR4).
    return [
        FakeBoundaryGroup(11, "AIR__EXT", ("AIR",), [101]),
        FakeBoundaryGroup(12, "AIR__FR4", ("AIR", "FR4"), [102]),
    ]


def _boundary_blocks(sif: str) -> dict[str, str]:
    # Map each emitted boundary's name (the "! <name>" comment the writer puts above the block) to its block body text.
    return {m.group(1): m.group(2) for m in re.finditer(r"! (\S+)\nBoundary Condition \d+\n(.*?)\nEnd", sif, re.DOTALL)}


def test_thermal_sif_generates_with_heat_solver() -> None:
    sif = _write_sif(physics=Physics.THERMAL, groups=_thermal_groups(), boundary_groups=_thermal_boundary_groups())
    assert "HeatSolver" in sif
    assert "Heat Conductivity" in sif  # FR4/Air thermal conductivity reaches the material block
    assert "AV {e}" not in sif  # the far field is magnetostatics-only


def test_fixed_temperature_reaches_only_the_board_surface() -> None:
    # FR4's FixedTemperature must project onto the surface groups bordering FR4 (the AIR__FR4 interface, id 12) and nowhere else — not the air box's external shell (id 11).
    sif = _write_sif(physics=Physics.THERMAL, groups=_thermal_groups(), boundary_groups=_thermal_boundary_groups())
    blocks = _boundary_blocks(sif)

    assert "AIR__FR4_fixed_temperature" in blocks
    assert not any("fixed_temperature" in name and name.startswith("AIR__EXT") for name in blocks)  # FR4 does not border the external shell

    ft = blocks["AIR__FR4_fixed_temperature"]
    assert "Target Boundaries(1) = 12" in ft
    m = re.search(r"^\s*Temperature = ([-0-9.eE+]+)", ft, re.MULTILINE)  # anchored: "External Temperature" also matches unanchored
    assert m and float(m.group(1)) == pytest.approx(350.0)


def test_convection_lands_only_on_external_air_surfaces() -> None:
    # A film BC on an interior interface would bypass the conduction path, so Convection is external_only:
    # it reaches the air box's shell (11) but NOT the air↔board interface (12).
    sif = _write_sif(physics=Physics.THERMAL, groups=_thermal_groups(), boundary_groups=_thermal_boundary_groups())
    blocks = _boundary_blocks(sif)

    ext = blocks["AIR__EXT_convection"]
    assert "Target Boundaries(1) = 11" in ext
    assert "Heat Transfer Coefficient" in ext and "External Temperature" in ext

    assert "Heat Transfer Coefficient" not in blocks["AIR__FR4_fixed_temperature"]


def test_one_boundary_block_per_group_with_conditions_merged() -> None:
    # ONE Boundary per surface group (Elmer honors a single BC section per boundary element): the board's
    # fixed_temperature + heat_flux merge into the interface's single block.
    fr4_tag = EntityTag(selector={"Region": "Board"}, conditions=[FixedTemperature(value=350 * U.K), HeatFlux(value=500 * U.W / U.m**2)])
    air_tag = EntityTag(selector={"Region": "Ambient"}, conditions=[Convection(coefficient=12 * U.W / (U.m**2 * U.K), ext_temperature=300 * U.K)])
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "FR4", FR4(), [fr4_tag]),
        FakePhysicalGroup(2, "AIR", Air(), [air_tag]),
    ]
    sif = _write_sif(physics=Physics.THERMAL, groups=groups, boundary_groups=_thermal_boundary_groups())
    blocks = _boundary_blocks(sif)
    assert set(blocks) == {"AIR__EXT_convection", "AIR__FR4_fixed_temperature_heat_flux"}
    merged = blocks["AIR__FR4_fixed_temperature_heat_flux"]
    assert "Heat Flux" in merged and re.search(r"^\s*Temperature = ", merged, re.MULTILINE)


# --- body-force sources (CurrentDensity / HeatSource) ------------------------


def _body_force_block(sif: str, name: str) -> tuple[str, str]:
    m = re.search(rf"! {name}\nBody Force (\d+)\n(.*?)\nEnd", sif, re.DOTALL)
    assert m, f"no Body Force block {name!r} in:\n{sif}"
    return m.group(1), m.group(2)


def test_current_density_becomes_a_body_force_under_axisymmetric_magnetostatics() -> None:
    n_tag = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 1, 0))])
    coil_tag = EntityTag(selector={TagKey.PHASE: "A"}, conditions=[CurrentDensity(value=-3.2 * U.A / U.mm**2)])
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "N52_MAG_N", N52(), [n_tag]),
        FakePhysicalGroup(2, "COPPER_PHASEA", Copper(), [coil_tag]),
        FakePhysicalGroup(3, "AIR", Air(), []),
    ]
    sif = _write_sif(groups=groups, frame=Axisymmetric(axis=Axis.Y))

    force_id, block = _body_force_block(sif, "COPPER_PHASEA_body_force")
    m = re.search(r"Current Density = ([-0-9.eE+]+)", block)
    assert m and float(m.group(1)) == pytest.approx(-3.2e6)
    assert re.search(rf"Body Force = {force_id}\s", sif), "coil body does not reference its Body Force"
    # The drive rides beside the magnet source, which stays on the magnet's Material.
    assert "Magnetization 2 = " in sif


def test_heat_source_becomes_a_body_force_under_thermal() -> None:
    coil_tag = EntityTag(selector={TagKey.PHASE: "A"}, conditions=[HeatSource(power_density=5e5 * U.W / U.m**3)])
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "COPPER_PHASEA", Copper(), [coil_tag]),
        FakePhysicalGroup(2, "AIR", Air(), []),
    ]
    # validate=False: no thermal boundary condition is authored here, which validate() rightly warns about.
    sif = _write_sif(physics=Physics.THERMAL, groups=groups, validate=False)

    _, block = _body_force_block(sif, "COPPER_PHASEA_body_force")
    m = re.search(r"Volumetric Heat Source = ([-0-9.eE+]+)", block)
    assert m and float(m.group(1)) == pytest.approx(5e5)


# --- write() error branches + magnet "missing remanence" marker -------------


class _NoRemanenceMagnet(MaterialProperties):
    """A material that claims is_magnet but resolves no remanence, forcing the `magnitude is None` branch in `_wire_magnet_body`."""

    name: str = "PhantomMagnet"

    @property
    def is_magnet(self) -> bool:
        return True


def test_magnet_with_unresolvable_remanence_emits_marker() -> None:
    # is_magnet True but magnetization_magnitude() returns None, so the body is marked MISSING REMANENCE rather than emitting a zero field. validate=False so the direction checks don't fire first.
    groups: list[PhysicalGroup] = [FakePhysicalGroup(1, "PHANTOM", _NoRemanenceMagnet(), [])]
    sif = _write_sif(groups=groups, validate=False)
    assert "MISSING REMANENCE" in sif
    assert "Magnetization 1 =" not in sif


def test_write_raises_when_output_dir_missing(tmp_path: Path) -> None:
    sim = Sim(_groups(), physics=Physics.MAGNETOSTATICS).generate()
    missing = tmp_path / "does_not_exist"
    with pytest.raises(ValueError, match="does not exist"):
        sim.write(str(missing))


def test_write_raises_when_output_dir_is_a_file(tmp_path: Path) -> None:
    sim = Sim(_groups(), physics=Physics.MAGNETOSTATICS).generate()
    a_file = tmp_path / "iam_a_file.txt"
    _ = a_file.write_text("not a directory")
    with pytest.raises(ValueError, match="is a file"):
        sim.write(str(a_file))


def test_write_before_generate_raises() -> None:
    # The two-phase contract: write() requires generate() to have built the simulation first.
    sim = Sim(_groups(), physics=Physics.MAGNETOSTATICS)
    with pytest.raises(RuntimeError, match="generate"):
        sim.write(str(Path(tempfile.mkdtemp())))


def test_validate_before_generate_raises() -> None:
    # validate() is normally run by generate(); calling it standalone before the sim is built fails loudly rather than touching a None.
    sim = Sim(_groups(), physics=Physics.MAGNETOSTATICS)
    with pytest.raises(RuntimeError, match="generate"):
        sim.validate()


def test_thermal_groups_conditions_typed_as_union() -> None:
    # Guard that the thermal tag conditions are a proper ConditionUnion list (keeps the typed fixtures honest).
    conds: list[ConditionUnion] = [FixedTemperature(value=350 * U.K)]
    assert isinstance(conds[0], FixedTemperature)


def test_axisymmetric_2d_sif_generates() -> None:
    sif = _write_sif(frame=Axisymmetric(axis=Axis.Y), boundary_groups=_boundary_groups())
    assert '"MagnetoDynamics2D" "MagnetoDynamics2D"' in sif
    assert "Axi Symmetric" in sif
    # CalcFields latches onto the scalar A_phi variable.
    assert 'Potential Variable = String "Potential"' in sif
    # Magnets keep the shared per-region Material Magnetization wiring.
    assert re.search(r"Magnetization 2 = [-0-9.eE+]+", sif)


def test_axisymmetric_frame_maps_the_authored_axis_onto_magnetization_2() -> None:
    # Authored along Z (the usual CAD axis): the axial share lands on Magnetization 2, radial is 0, no third component.
    n_tag = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 0, -1))])
    sif = _write_sif(groups=_groups(n_tag), frame=Axisymmetric())

    magnitude = N52().magnetic.magnetization_magnitude(at=OperatingPoint().as_mapping())
    assert magnitude is not None

    m = re.search(r"Magnetization 2 = ([-0-9.eE+]+)", sif)
    assert m and float(m.group(1)) == pytest.approx(-magnitude, rel=1e-5)
    assert "Magnetization 1 = 0" in sif
    assert "Magnetization 3" not in sif


def test_axisymmetric_thermal_sif_runs_in_axi_symmetric() -> None:
    sif = _write_sif(physics=Physics.THERMAL, groups=_thermal_groups(), boundary_groups=_thermal_boundary_groups(), frame=Axisymmetric())
    assert "HeatSolver" in sif
    assert "Coordinate System = Axi Symmetric" in sif


def test_sim_backstops_conditions_edited_after_the_config_checked_them() -> None:
    # In-place domain edits skip the config validators; the Sim re-checks every condition against its frame.
    coil = EntityTag(selector={TagKey.PHASE: "A"}, conditions=[CurrentDensity(value=1 * U.A / U.mm**2)])
    coil_groups: list[PhysicalGroup] = [FakePhysicalGroup(1, "COPPER_PHASEA", Copper(), [coil])]
    with pytest.raises(ValueError, match="CurrentDensity"):
        Sim(coil_groups)

    torque = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 0, 1)), CalculateMagneticForce(calculate_magnetic_torque=True)])
    with pytest.raises(ValueError, match="torque"):
        Sim(_groups(torque), frame=Axisymmetric())


def test_sim_selects_the_axisymmetric_stack_from_the_frame() -> None:
    sim = Sim(_groups(), physics=Physics.MAGNETOSTATICS, frame=Axisymmetric(axis=Axis.Y))
    assert sim.physics is Physics.MAGNETOSTATICS_2D


def test_sim_rejects_a_frame_its_physics_or_stack_cannot_solve() -> None:
    with pytest.raises(ValueError, match="with a 2D frame"):
        Sim(_groups(), physics=Physics.MAGNETOSTATICS_2D)

    with pytest.raises(ValueError, match="Coordinate System 'Cartesian'"):
        Sim(_groups(), physics=Physics.MAGNETOSTATICS, config=physics_config(Physics.MAGNETOSTATICS), frame=Axisymmetric())


def test_axisymmetric_2d_far_field_pins_potential_not_av() -> None:
    # The scalar stack's Dirichlet far field targets its nodal Potential; the WhitneyAV edge keyword must not appear.
    sif = _write_sif(frame=Axisymmetric(axis=Axis.Y), boundary_groups=_boundary_groups())
    assert "Potential = Real 0" in sif
    assert "AIR__EXT_far_field" in sif
    assert "AV {e}" not in sif


# --- symmetry-axis boundaries (2D axisymmetric) ------------------------------


def _axis_boundary_groups() -> list[FakeBoundaryGroup]:
    # A 2D axisymmetric slice: the air's true far-field shell, its symmetry-axis edge, and a magnet-air interface.
    return [
        FakeBoundaryGroup(11, "AIR__EXT", ("AIR",), [101]),
        FakeBoundaryGroup(12, "AIR__AXIS", ("AIR",), [102], on_axis=True),
        FakeBoundaryGroup(13, "AIR__N52_MAG_N", ("AIR", "N52_MAG_N"), [103]),
    ]


def test_axis_group_pins_potential_in_a_symmetry_axis_block() -> None:
    # A_phi = 0 is the exact condition on r=0, emitted as its own <group>_symmetry_axis block targeting the axis group's id.
    sif = _write_sif(frame=Axisymmetric(axis=Axis.Y), boundary_groups=_axis_boundary_groups())
    blocks = _boundary_blocks(sif)

    axis = blocks["AIR__AXIS_symmetry_axis"]
    assert "Potential = Real 0" in axis
    assert "Target Boundaries(1) = 12" in axis


def test_axis_group_is_excluded_from_far_field_naming() -> None:
    # The far-field branch keys on is_external, which an on_axis group no longer satisfies.
    sif = _write_sif(frame=Axisymmetric(axis=Axis.Y), boundary_groups=_axis_boundary_groups())
    blocks = _boundary_blocks(sif)

    assert {name for name in blocks if name.endswith("_far_field")} == {"AIR__EXT_far_field"}
    assert set(blocks) == {"AIR__EXT_far_field", "AIR__AXIS_symmetry_axis"}


def test_symmetry_axis_only_wired_for_axisymmetric_physics() -> None:
    # The 3D mesher never marks on_axis, but even handed an axis group, non-2D magnetostatics attaches neither the axis BC nor the far field to it.
    sif = _write_sif(Physics.MAGNETOSTATICS, boundary_groups=_axis_boundary_groups())
    assert set(_boundary_blocks(sif)) == {"AIR__EXT_far_field"}
    assert "symmetry_axis" not in sif


def test_a_solver_equation_keyword_lands_in_the_equation_block() -> None:
    config = physics_config(Physics.LINEAR_ELASTICITY)
    config.solver(StressSolver).plane_stress = True
    sim = Sim(_beam_groups(), physics=Physics.LINEAR_ELASTICITY, boundary_groups=_beam_boundaries(), config=config)  # pyright: ignore[reportArgumentType]  # structural fake
    sim.generate()
    d = Path(tempfile.mkdtemp())
    sim.write(str(d))
    sif = (d / "case.sif").read_text()

    equation = re.search(r"^Equation 1\n(.*?)^End", sif, flags=re.MULTILINE | re.DOTALL)

    assert equation is not None and "Plane Stress = True" in equation.group(1)
    assert sif.count("Plane Stress") == 1


# --- planar sections (Cartesian 2D) -----------------------------------------


def test_a_cartesian_2d_magnetostatic_sif_magnetizes_in_plane_and_pins_the_far_field_only() -> None:
    sif = _write_sif(frame=Cartesian2D(thickness=10 * U.mm), boundary_groups=_boundary_groups())
    blocks = _boundary_blocks(sif)

    assert "Coordinate System = Cartesian 2D" in sif
    assert "MagnetoDynamics2D" in sif
    assert "Magnetization 1" in sif and "Magnetization 2" in sif and "Magnetization 3" not in sif
    assert set(blocks) == {"AIR__EXT_far_field"}
    assert "Potential = Real 0" in blocks["AIR__EXT_far_field"]
    assert "symmetry_axis" not in sif


def _planar_beam_boundaries() -> list[FakeBoundaryGroup]:
    root = EntityTag(selector={TagKey.NAME: "Root"}, conditions=[FixedDisplacement()])
    tip = EntityTag(selector={TagKey.NAME: "Tip"}, conditions=[SurfaceForce(force=quantity(Vec3(100, 0, 0), "N"))])

    return [
        FakeBoundaryGroup(11, "BEAM__EXT", ("BEAM",), [101]),
        FakeBoundaryGroup(12, "ROOT", ("BEAM",), [102], tags=[root], measure=0.02, named=True),
        FakeBoundaryGroup(13, "TIP", ("BEAM",), [103], tags=[tip], measure=0.02, named=True),
    ]


@pytest.mark.parametrize(("state", "plane_stress"), [(PlaneState.STRESS, "True"), (PlaneState.STRAIN, "False")])
def test_a_cartesian_2d_elasticity_sif_solves_two_dofs_in_its_plane_state(state: PlaneState, plane_stress: str) -> None:
    frame = Cartesian2D(thickness=10 * U.mm, state=state)
    sif = _write_sif(physics=Physics.LINEAR_ELASTICITY, groups=_beam_groups(), boundary_groups=_planar_beam_boundaries(), frame=frame)
    solver = re.search(r"^Solver 1\n(.*?)^End", sif, flags=re.MULTILINE | re.DOTALL)
    equation = re.search(r"^Equation 1\n(.*?)^End", sif, flags=re.MULTILINE | re.DOTALL)
    blocks = _boundary_blocks(sif)

    assert "Coordinate System = Cartesian 2D" in sif
    assert solver is not None and "Variable Dofs = 2" in solver.group(1)
    assert equation is not None and f"Plane Stress = {plane_stress}" in equation.group(1)
    assert "Displacement 1 = 0.0" in blocks["ROOT_fixed_displacement"] and "Displacement 3" not in sif
    assert "Force 1 = 500000.0" in blocks["TIP_surface_force"] and "Force 3" not in sif
