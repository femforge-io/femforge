"""Nonlinear (H-B curve) steel through Sim: the curve satisfies the Relative Permeability requirement and lands in the sif as a table. Pure-logic (fake groups, no gmsh)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from femforge.common.tags import TagKey
from femforge.common.vector import Direction
from femforge.elmer.physics import Physics
from femforge.elmer.sim import Sim
from femforge.materials.air import Air
from femforge.materials.neodymium import N42
from femforge.materials.properties import MaterialProperties
from femforge.materials.steel import Steel_1080
from femforge.meshing.groups import PhysicalGroup
from femforge.physical.conditions import Magnetization
from femforge.physical.domain import EntityTag
from femforge.physical.property_functions import Static
from femforge.physical.units import U


class FakePhysicalGroup(PhysicalGroup):
    """A 3D ``PhysicalGroup`` from an id, a name, a material and tags."""

    def __init__(self, gid: int, name: str, material: MaterialProperties, tags: list[EntityTag]) -> None:
        self.dim: int = 3
        self.id: int = gid
        self.name: str = name
        self.material = material
        self.tags: list[EntityTag] = tags
        self.entity_tags: list[int] = [gid]


def _groups(steel: Steel_1080) -> list[PhysicalGroup]:
    n_tag = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 1, 0))])
    return [
        FakePhysicalGroup(1, "N42_MAG_N", N42(), [n_tag]),
        FakePhysicalGroup(2, "STEEL", steel, []),
        FakePhysicalGroup(3, "AIR", Air(), []),
    ]


def _write_sif(steel: Steel_1080, tmp_path: Path) -> str:
    sim = Sim(_groups(steel), physics=Physics.MAGNETOSTATICS)
    sim.generate()  # validate=True: the stack must accept the steel as-is
    sim.write(str(tmp_path))
    return (tmp_path / "case.sif").read_text()


def _steel_material_block(sif: str) -> str:
    m = re.search(r"! High-Carbon Steel \(AISI 1080, annealed\)\nMaterial \d+\n(.*?)\nEnd\n", sif, re.DOTALL)
    assert m, "expected a Material block for the steel body"
    return m.group(1)


def test_nonlinear_steel_validates_and_emits_hb_curve(tmp_path: Path) -> None:
    # H-B Curve satisfies the Relative Permeability requirement (MATERIAL_KEY_EQUIVALENTS), so generate()+validate() passes.
    sif = _write_sif(Steel_1080(), tmp_path)
    block = _steel_material_block(sif)
    assert 'H-B Curve = Variable "dummy"' in block
    assert "Real Monotone Cubic" in block
    assert "End" in block  # the table's own terminator, inside the Material block
    assert "Relative Permeability" not in block


def test_linearized_steel_emits_static_permeability(tmp_path: Path) -> None:
    sif = _write_sif(Steel_1080(rel_permeability=584.0), tmp_path)
    block = _steel_material_block(sif)
    assert "Relative Permeability = 584.0" in block
    assert "H-B Curve" not in block


def test_material_missing_both_permeability_and_curve_fails_validate() -> None:
    # Density alone satisfies neither Relative Permeability nor its H-B Curve equivalent.
    plain = MaterialProperties(name="DenseOnly")
    plain.mechanical.density = Static(value=2000 * U.kg / U.m**3)
    sim = Sim([FakePhysicalGroup(1, "PLAIN", plain, [])], physics=Physics.MAGNETOSTATICS)
    with pytest.raises(ValueError) as exc:
        sim.generate()
    assert "Relative Permeability" in str(exc.value)
    assert "PLAIN" in str(exc.value)


def test_nonlinear_solver_defaults_reach_the_sif(tmp_path: Path) -> None:
    # The nonlinear solve needs MagnetoDynamicsSolver's outer-iteration defaults in the sif.
    sif = _write_sif(Steel_1080(), tmp_path)
    assert "Nonlinear System Max Iterations = 50" in sif
