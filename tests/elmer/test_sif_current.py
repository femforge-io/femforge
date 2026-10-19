"""Current-conduction (StatCurrentSolve) sif generation: solver stack, electrode BCs, and the SaveScalars current measurement."""

from __future__ import annotations

import re

import pytest

from femforge.common.tags import CurrentDir, TagKey
from femforge.elmer.config import SaveScalarsSolver, StatCurrentSolver, physics_config
from femforge.elmer.physics import Physics
from femforge.materials.copper import Copper
from femforge.materials.properties import ElmerMaterialKeyword
from femforge.meshing.groups import PhysicalGroup
from femforge.physical.conditions import FixedVoltage
from femforge.physical.domain import EntityTag
from femforge.physical.units import U
from tests.elmer.test_sif_smoke import FakeBoundaryGroup, FakePhysicalGroup, _boundary_blocks, _write_sif


def _current_groups() -> list[PhysicalGroup]:
    """A copper bar between two copper terminal pads, one FixedVoltage electrode each (the dedicated-pad approach)."""
    entry = EntityTag(selector={TagKey.CURRENT_DIR: CurrentDir.ENTRY}, conditions=[FixedVoltage(value=0.001 * U.V)])
    exit_ = EntityTag(selector={TagKey.CURRENT_DIR: CurrentDir.EXIT}, conditions=[FixedVoltage(value=0 * U.V)])
    return [
        FakePhysicalGroup(1, "COPPER_ENTRY", Copper(), [entry]),
        FakePhysicalGroup(2, "COPPER_BAR", Copper(), []),
        FakePhysicalGroup(3, "COPPER_EXIT", Copper(), [exit_]),
    ]


def _current_boundary_groups() -> list[FakeBoundaryGroup]:
    return [
        FakeBoundaryGroup(11, "COPPER_ENTRY__EXT", ("COPPER_ENTRY",), [101]),
        FakeBoundaryGroup(12, "COPPER_BAR__COPPER_ENTRY", ("COPPER_BAR", "COPPER_ENTRY"), [102]),
        FakeBoundaryGroup(13, "COPPER_BAR__EXT", ("COPPER_BAR",), [103]),
        FakeBoundaryGroup(14, "COPPER_BAR__COPPER_EXIT", ("COPPER_BAR", "COPPER_EXIT"), [104]),
        FakeBoundaryGroup(15, "COPPER_EXIT__EXT", ("COPPER_EXIT",), [105]),
    ]


def _current_sif() -> str:
    return _write_sif(physics=Physics.CURRENT_CONDUCTION, groups=_current_groups(), boundary_groups=_current_boundary_groups())


def test_current_conduction_sif_generates_solver_stack() -> None:
    sif = _current_sif()
    assert "StatCurrentSolver" in sif
    assert "SaveScalars" in sif
    assert "Electric Conductivity" in sif  # the copper conductivity reaches the material block
    assert "Calculate Volume Current = True" in sif
    assert "Calculate Joule Heating = True" in sif
    assert "AV {e}" not in sif  # the far field is magnetostatics-only


def test_save_scalars_solver_measures_potential_flux() -> None:
    sif = _current_sif()
    assert 'Procedure = "SaveData" "SaveScalars"' in sif
    assert "Variable 1 = Potential" in sif
    assert "Operator 1 = diffusive flux" in sif
    assert "Coefficient 1 = Electric Conductivity" in sif


def test_fixed_voltage_reaches_every_pad_surface_and_nothing_else() -> None:
    sif = _current_sif()
    blocks = _boundary_blocks(sif)
    # Each pad's potential lands on its shell and its bar interface; the bar's own shell is a natural (insulated) boundary.
    assert set(blocks) == {
        "COPPER_ENTRY__EXT_fixed_voltage",
        "COPPER_BAR__COPPER_ENTRY_fixed_voltage",
        "COPPER_BAR__COPPER_EXIT_fixed_voltage",
        "COPPER_EXIT__EXT_fixed_voltage",
    }

    entry = blocks["COPPER_ENTRY__EXT_fixed_voltage"]
    assert "Target Boundaries(1) = 11" in entry
    m = re.search(r"Potential = ([-0-9.eE+]+)", entry)
    assert m and float(m.group(1)) == pytest.approx(1e-3)
    # Every electrode is flagged for the SaveScalars boundary integral (the current measurement).
    assert all("Save Scalars = Logical True" in block for block in blocks.values())

    exit_ = blocks["COPPER_EXIT__EXT_fixed_voltage"]
    m = re.search(r"Potential = ([-0-9.eE+]+)", exit_)
    assert m and float(m.group(1)) == pytest.approx(0.0)


def test_current_conduction_selectable_by_string() -> None:
    assert _write_sif(physics="current_conduction", groups=_current_groups(), boundary_groups=_current_boundary_groups())


def test_stat_current_solver_requires_electric_conductivity() -> None:
    assert StatCurrentSolver.required_material_keys == frozenset({ElmerMaterialKeyword.ELECTRIC_CONDUCTIVITY})
    stack = physics_config(Physics.CURRENT_CONDUCTION)
    assert isinstance(stack.solver(StatCurrentSolver), StatCurrentSolver)
    assert isinstance(stack.solver(SaveScalarsSolver), SaveScalarsSolver)


def _mixed_groups() -> list[PhysicalGroup]:
    """Copper conductors embedded in FR4 + air dielectrics."""
    from femforge.materials.air import Air
    from femforge.materials.pcb import FR4

    return _current_groups() + [
        FakePhysicalGroup(4, "FR4", FR4(), []),
        FakePhysicalGroup(5, "AIR", Air(), []),
    ]


def test_dielectrics_get_an_equation_without_stat_current() -> None:
    # Solving Potential over the dielectrics puts ~21 decades of sigma contrast into one matrix (singular for a
    # direct factorization, a stall for CG) — non-conductor bodies must carry a second equation without it.
    sif = _write_sif(physics=Physics.CURRENT_CONDUCTION, groups=_mixed_groups(), boundary_groups=_current_boundary_groups())
    assert "Dielectric Equation" in sif

    body_blocks = {m.group(1): m.group(0) for m in re.finditer(r"Body \d+\n(?:.*\n)*?  Name = \"(\w+)\"\n(?:.*\n)*?End", sif)}
    equations = {name: re.search(r"Equation = (\d+)", block).group(1) for name, block in body_blocks.items()}  # pyright: ignore[reportOptionalMemberAccess]
    assert equations["FR4"] == equations["AIR"], "both dielectrics share the dielectric equation"
    assert equations["COPPER_BAR"] == equations["COPPER_ENTRY"] == equations["COPPER_EXIT"]
    assert equations["FR4"] != equations["COPPER_BAR"], "dielectrics and conductors must not share an equation"


def test_copper_only_case_keeps_the_single_equation() -> None:
    sif = _current_sif()
    assert "Dielectric Equation" not in sif


def test_magnetostatics_is_untouched_by_conductor_restriction() -> None:
    # No conductor_only solver in the magnetostatics stack: every body shares the main equation as before.
    from tests.elmer.test_sif_smoke import _boundary_groups, _groups

    sif = _write_sif(physics=Physics.MAGNETOSTATICS, groups=_groups(), boundary_groups=_boundary_groups())
    assert "Dielectric Equation" not in sif


def test_stat_current_solver_defaults_to_direct_umfpack() -> None:
    emitted = StatCurrentSolver().to_elmer()
    assert emitted["Linear System Solver"] == "Direct"
    assert emitted["Linear System Direct Method"] == "umfpack"
    assert StatCurrentSolver.conductor_only is True
