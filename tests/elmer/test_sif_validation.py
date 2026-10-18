"""Sim.validate() (run inside Sim.generate()) catches solver misconfig before the solve, so a broken config fails in CI rather than at ElmerSolver runtime. Pure-logic: fake groups / material."""

import re
import warnings
from typing import Mapping

import pytest

from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.elmer.physics import Physics
from femforge.elmer.sim import Sim, SolverConfigWarning
from femforge.physical.domain import EntityTag
from femforge.meshing.groups import PhysicalGroup
from femforge.physical.conditions import Convection, FixedDisplacement, FixedTemperature, Magnetization, SurfaceForce
from femforge.materials.air import Air
from femforge.materials.neodymium import N52
from femforge.materials.pcb import FR4
from femforge.materials.properties import MaterialProperties
from femforge.physical.units import Quantity, U, quantity


class FakePhysicalGroup (PhysicalGroup):
    """A 3D ``PhysicalGroup`` from an id, a name, a material and tags."""

    def __init__(self, gid: int, name: str, material : MaterialProperties, tags: list[EntityTag]) -> None:
        self.dim: int = 3
        self.id: int = gid
        self.name: str = name
        self.material = material
        self.tags: list[EntityTag] = tags
        self.entity_tags: list[int] = [gid]


class FakeBoundaryGroup:
    """Mimics meshing.boundary.BoundaryGroup without requiring gmsh (the dim-2 sibling of FakePhysicalGroup)."""

    def __init__(self, gid: int, name: str, bodies: tuple[str, ...], face_tags: list[int]) -> None:
        self.dim: int = 2
        self.id: int = gid
        self.name: str = name
        self.bodies: tuple[str, ...] = bodies
        self.entity_tags: list[int] = face_tags
        self.tags: list[EntityTag] = []
        self.measure: float | None = None
        self.named: bool = False

    @property
    def is_external(self) -> bool:
        return len(self.bodies) == 1


class LeakyMaterial(MaterialProperties):
    """A material whose to_elmer() returns an unstripped pint Quantity, standing in for a buggy material."""

    name: str = "Leaky"
    # is_magnet is an inherited computed property (False here since no remanence); defining it as a field would shadow the parent property and warn.

    def to_elmer(self, *, at: Mapping[str, Quantity], runtime: frozenset[str] = frozenset(), backend: str = "matc") -> dict[str, float | str]:
        # The required key is present (so check 1 passes) but its value leaked a pint Quantity (so check 2 must fire).
        return {"Relative Permeability": 1.05 * U.dimensionless}  # pyright: ignore[reportReturnType]  # deliberately unstripped to exercise check 2


def _magnet_tag(direction: Direction | None = Direction(0, 1, 0)) -> EntityTag:
    return EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=direction)])


def _generate(
    groups: list[PhysicalGroup],
    physics: Physics,
    *,
    validate: bool = True,
    boundary_groups: list[FakeBoundaryGroup] | None = None,
) -> Sim:
    """Build a Sim and run generate(); validation runs inside generate(), not the constructor."""
    sim = Sim(groups, physics=physics, boundary_groups=boundary_groups)  # pyright: ignore[reportArgumentType]  # structural fake boundary groups
    sim.generate(validate=validate)
    return sim


def test_valid_magnetostatics_config_passes_silently() -> None:
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "N52_MAG_N", N52(), [_magnet_tag()]),
        FakePhysicalGroup(2, "AIR", Air(), []),
    ]
    # Construction must not raise (validate=True is the default).
    _generate(groups, Physics.MAGNETOSTATICS)


def test_missing_required_material_property_raises() -> None:
    # A plain material with no magnetic properties emits no "Relative Permeability", which magnetostatics requires on every body.
    plain: MaterialProperties = MaterialProperties(name="Vacuum-ish")
    groups: list[PhysicalGroup] = [FakePhysicalGroup(1, "PLAIN", plain, [])]
    with pytest.raises(ValueError) as exc:
        _generate(groups, Physics.MAGNETOSTATICS)
    assert "Relative Permeability" in str(exc.value)
    assert "PLAIN" in str(exc.value)


def test_leaked_pint_quantity_raises() -> None:
    groups: list[PhysicalGroup] = [FakePhysicalGroup(1, "LEAK", LeakyMaterial(), [])]
    with pytest.raises(ValueError) as exc:
        _generate(groups, Physics.MAGNETOSTATICS)
    assert "Relative Permeability" in str(exc.value)
    assert "Quantity" in str(exc.value)


def test_magnet_without_direction_raises() -> None:
    # N52 is a magnet but this region carries no Magnetization condition.
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "N52_MAG_N", N52(), [EntityTag(selector={TagKey.MAGNETIZATION: "N"})]),
        FakePhysicalGroup(2, "AIR", Air(), []),
    ]
    with pytest.raises(ValueError) as exc:
        _generate(groups, Physics.MAGNETOSTATICS)
    assert "Magnetization" in str(exc.value)
    assert "N52_MAG_N" in str(exc.value)


def test_magnet_with_zero_direction_raises() -> None:
    # A Magnetization condition is present but its direction is the zero vector, which would emit a zero field — still a misconfiguration.
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "N52_MAG_N", N52(), [_magnet_tag(direction=None)]),
        FakePhysicalGroup(2, "AIR", Air(), []),
    ]
    with pytest.raises(ValueError):
        _generate(groups, Physics.MAGNETOSTATICS)


def test_validate_false_suppresses_magnet_direction_error() -> None:
    # The escape hatch: validate=False skips the checks and the wiring falls back to its in-sif marker rather than raising.
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "N52_MAG_N", N52(), [EntityTag(selector={TagKey.MAGNETIZATION: "N"})]),
        FakePhysicalGroup(2, "AIR", Air(), []),
    ]
    _generate(groups, Physics.MAGNETOSTATICS, validate=False)


def test_all_problems_reported_together() -> None:
    # Two distinct misconfigurations in one config must both appear in the single raised error (problems are accumulated, not short-circuited).
    plain: MaterialProperties = MaterialProperties(name="Vacuum-ish")
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "PLAIN", plain, []),
        FakePhysicalGroup(2, "N52_MAG_N", N52(), [EntityTag(selector={TagKey.MAGNETIZATION: "N"})]),
    ]
    with pytest.raises(ValueError) as exc:
        _generate(groups, Physics.MAGNETOSTATICS)
    message: str = str(exc.value)
    assert "PLAIN" in message
    assert "N52_MAG_N" in message


# --- check 4 (boundary coverage) ----------------------------------


def test_thermal_without_boundary_conditions_warns() -> None:
    # Thermal physics with no boundary conditions is ill-posed, so a SolverConfigWarning fires; materials still satisfy check 1 (FR4/Air emit Heat Conductivity), so it's not a hard error.
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "FR4", FR4(), []),
        FakePhysicalGroup(2, "AIR", Air(), []),
    ]
    with pytest.warns(SolverConfigWarning, match="no boundary conditions"):
        _generate(groups, Physics.THERMAL)


def test_thermal_with_boundary_conditions_does_not_warn() -> None:
    # When a region carries a thermal boundary condition that resolves onto a surface group, coverage is satisfied and no warning fires.
    hot: EntityTag = EntityTag(selector={"Region": "Board"}, conditions=[FixedTemperature(value=350 * U.K)])
    cool: EntityTag = EntityTag(selector={"Region": "Ambient"}, conditions=[Convection(coefficient=12 * U.W / (U.m**2 * U.K), ext_temperature=300 * U.K)])
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "FR4", FR4(), [hot]),
        FakePhysicalGroup(2, "AIR", Air(), [cool]),
    ]
    boundary_groups: list[FakeBoundaryGroup] = [
        FakeBoundaryGroup(11, "AIR__EXT", ("AIR",), [101]),
        FakeBoundaryGroup(12, "AIR__FR4", ("AIR", "FR4"), [102]),
    ]
    with warnings.catch_warnings():
        warnings.simplefilter("error", SolverConfigWarning)  # any SolverConfigWarning becomes a test failure
        _generate(groups, Physics.THERMAL, boundary_groups=boundary_groups)


def test_magnetostatics_emits_no_solver_warning() -> None:
    # Magnetostatics is excluded from the coverage warning (its far field is auto-emitted), and the real solver presets are keyword-clean, so a valid config raises no SolverConfigWarning.
    groups: list[PhysicalGroup] = [
        FakePhysicalGroup(1, "N52_MAG_N", N52(), [_magnet_tag()]),
        FakePhysicalGroup(2, "AIR", Air(), []),
    ]
    with warnings.catch_warnings():
        warnings.simplefilter("error", SolverConfigWarning)
        _generate(groups, Physics.MAGNETOSTATICS)


# --- linear elasticity ------------------------------------------------------


def test_air_under_elasticity_misses_the_elastic_constants() -> None:
    groups: list[PhysicalGroup] = [FakePhysicalGroup(1, "AIR", Air(), [])]

    with pytest.warns(SolverConfigWarning), pytest.raises(ValueError, match=re.escape("missing ['Poisson Ratio', 'Youngs Modulus']")):
        _generate(groups, Physics.LINEAR_ELASTICITY)


def test_a_load_without_a_fixed_face_warns_of_rigid_body_modes() -> None:
    tip = FakeBoundaryGroup(12, "TIP", ("FR4",), [102])
    tip.tags = [EntityTag(selector={TagKey.NAME: "Tip"}, conditions=[SurfaceForce(force=quantity(Vec3(1, 0, 0), "N"))])]
    tip.measure = 1e-4

    with pytest.warns(SolverConfigWarning, match="rigid-body motion"):
        _generate([FakePhysicalGroup(1, "FR4", FR4(), [])], Physics.LINEAR_ELASTICITY, boundary_groups=[tip])


def test_a_fixed_face_silences_the_rigid_body_warning() -> None:
    clamp = EntityTag(selector={TagKey.MATERIAL: "FR4"}, conditions=[FixedDisplacement()])

    with warnings.catch_warnings():
        warnings.simplefilter("error", SolverConfigWarning)
        _generate([FakePhysicalGroup(1, "FR4", FR4(), [clamp])], Physics.LINEAR_ELASTICITY, boundary_groups=[FakeBoundaryGroup(11, "FR4__EXT", ("FR4",), [101])])
