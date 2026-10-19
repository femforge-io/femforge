"""Coupled joule-heating sif generation: StatCurrent + Heat solver stack, the JouleHeat Body Force, and cross-physics condition activation."""

from __future__ import annotations

import re

import pytest

from femforge.common.tags import CurrentDir, TagKey
from femforge.elmer.physics import Physics, condition_physics
from femforge.materials.copper import Copper
from femforge.meshing.groups import PhysicalGroup
from femforge.physical.conditions import Convection, FixedVoltage, JouleHeat
from femforge.physical.domain import EntityTag, conditions_for
from femforge.physical.units import U
from tests.elmer.test_sif_smoke import FakeBoundaryGroup, FakePhysicalGroup, _boundary_blocks, _write_sif


def _joule_groups() -> list[PhysicalGroup]:
    entry = EntityTag(selector={TagKey.CURRENT_DIR: CurrentDir.ENTRY}, conditions=[FixedVoltage(value=0.01 * U.V)])
    exit_ = EntityTag(selector={TagKey.CURRENT_DIR: CurrentDir.EXIT}, conditions=[FixedVoltage(value=0 * U.V)])
    bar = EntityTag(selector={"Region": "Bar"}, conditions=[JouleHeat(), Convection(coefficient=15 * U.W / (U.m**2 * U.K), ext_temperature=300 * U.K)])
    return [
        FakePhysicalGroup(1, "COPPER_ENTRY", Copper(), [entry]),
        FakePhysicalGroup(2, "COPPER_BAR", Copper(), [bar]),
        FakePhysicalGroup(3, "COPPER_EXIT", Copper(), [exit_]),
    ]


def _joule_boundary_groups() -> list[FakeBoundaryGroup]:
    return [
        FakeBoundaryGroup(11, "COPPER_ENTRY__EXT", ("COPPER_ENTRY",), [101]),
        FakeBoundaryGroup(12, "COPPER_BAR__COPPER_ENTRY", ("COPPER_BAR", "COPPER_ENTRY"), [102]),
        FakeBoundaryGroup(13, "COPPER_BAR__EXT", ("COPPER_BAR",), [103]),
        FakeBoundaryGroup(14, "COPPER_BAR__COPPER_EXIT", ("COPPER_BAR", "COPPER_EXIT"), [104]),
        FakeBoundaryGroup(15, "COPPER_EXIT__EXT", ("COPPER_EXIT",), [105]),
    ]


def _joule_sif() -> str:
    return _write_sif(physics=Physics.JOULE_HEATING, groups=_joule_groups(), boundary_groups=_joule_boundary_groups())


def test_condition_physics_activates_constituents() -> None:
    assert condition_physics(Physics.JOULE_HEATING) == {Physics.JOULE_HEATING, Physics.CURRENT_CONDUCTION, Physics.THERMAL}
    assert condition_physics(Physics.THERMAL) == {Physics.THERMAL}


def test_conditions_for_accepts_a_physics_set() -> None:
    tags = [_joule_groups()[1].tags[0]]
    from femforge.physical.conditions import ConditionTarget

    # Under the single thermal physics only the Convection resolves; under the coupled set the JouleHeat (body) does too.
    assert len(conditions_for(tags, Physics.THERMAL, ConditionTarget.BOUNDARY)) == 1
    assert len(conditions_for(tags, condition_physics(Physics.JOULE_HEATING), ConditionTarget.BODY)) == 1


def test_joule_heating_sif_has_both_solvers_and_body_force() -> None:
    sif = _joule_sif()
    assert "StatCurrentSolver" in sif
    assert "HeatSolver" in sif
    # The JouleHeat condition became a Body Force block referenced by the bar body.
    m = re.search(r"! COPPER_BAR_body_force\nBody Force (\d+)\n(.*?)\nEnd", sif, re.DOTALL)
    assert m, f"no Body Force block for the bar in:\n{sif}"
    assert "Joule Heat = Logical True" in m.group(2)
    assert re.search(rf"Body Force = {m.group(1)}\s+! COPPER_BAR_body_force", sif), "bar body does not reference its Body Force"
    # The pads carry no body-target condition, so no other Body Force exists.
    assert len(re.findall(r"\nBody Force \d+\n", sif)) == 1


def test_coupled_physics_activates_both_condition_families() -> None:
    blocks = _boundary_blocks(_joule_sif())
    # Electrical electrodes AND thermal convection both fire under the coupled physics.
    assert "COPPER_ENTRY__EXT_fixed_voltage" in blocks
    assert "COPPER_BAR__EXT_convection" in blocks
    # The film is external-only: the bar↔pad interface carries the pad's potential but no convection.
    assert "COPPER_BAR__COPPER_ENTRY_fixed_voltage" in blocks
    assert "Heat Transfer Coefficient" not in blocks["COPPER_BAR__COPPER_ENTRY_fixed_voltage"]
    assert "COPPER_EXIT__EXT_convection" not in blocks


def test_joule_stack_exports_peak_temperature() -> None:
    sif = _joule_sif()
    assert "Variable 3 = Temperature" in sif
    assert "Operator 3 = max" in sif


def test_joule_physics_emits_runtime_sigma_t_law() -> None:
    # Under the coupled physics, Temperature is a runtime variable, so Copper's sigma(T) Expression lands in the
    # Material block as a MATC law over the local field instead of a pre-evaluated float.
    sif = _joule_sif()
    assert "Electric Conductivity = Variable Temperature" in sif
    assert 'Real MATC "' in sif and "tx(0)" in sif


def test_uncoupled_current_conduction_pre_evaluates_sigma() -> None:
    # Without a solved Temperature, the same material must fall back to the operating-point float.
    sif = _write_sif(physics=Physics.CURRENT_CONDUCTION, groups=_joule_groups(), boundary_groups=_joule_boundary_groups())
    assert "Variable Temperature" not in sif
    m = re.search(r"Electric Conductivity = ([-0-9.eE+]+)", sif)
    assert m and float(m.group(1)) == pytest.approx(5.96e7 / (1 + 3.93e-3 * (300 - 293.15)))


def test_standalone_current_conduction_ignores_thermal_conditions() -> None:
    # The same tags under plain current conduction: no convection blocks, no Body Force (JouleHeat is JOULE_HEATING-scoped).
    sif = _write_sif(physics=Physics.CURRENT_CONDUCTION, groups=_joule_groups(), boundary_groups=_joule_boundary_groups())
    assert "convection" not in sif.lower()
    assert "Body Force" not in sif