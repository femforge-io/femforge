"""Tests for merging the keywords several conditions put in one sif block."""

from __future__ import annotations

import pytest

from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.elmer.combine import Source, merge
from femforge.elmer.physics import Physics
from femforge.frames import Axisymmetric
from femforge.materials.neodymium import N52
from femforge.meshing.groups import PhysicalGroup
from femforge.physical.conditions import Convection, CurrentDensity, FixedDisplacement, FixedTemperature, FixedVoltage, HeatFlux, HeatSource, Magnetization, SurfaceForce
from femforge.physical.domain import EntityTag
from femforge.physical.units import U, quantity
from tests.elmer.test_sif_smoke import FakeBoundaryGroup, FakePhysicalGroup, _write_sif

FLUX = HeatFlux(value=1 * U.W / U.m**2)
FIXED = FixedTemperature(value=300 * U.K)
FILM = Convection(coefficient=10 * U.W / (U.m**2 * U.K), ext_temperature=290 * U.K)


def test_loads_of_one_kind_add_and_a_temperature_dependent_one_adds_as_an_expression() -> None:
    source = HeatSource(power_density=1 * U.W / U.m**3)
    varying = 'Variable Temperature\n    Real MATC "2*(1.0+0.004*(tx(0)-293))"'
    merged = merge("COIL", [Source("heat_source", source, "tag A", {"Volumetric Heat Source": 5.0}), Source("heat_source", source, "tag B", {"Volumetric Heat Source": varying})])

    assert merged.clashes == []
    assert merged.keywords == {"Volumetric Heat Source": 'Variable Temperature\n    Real MATC "5+(2*(1.0+0.004*(tx(0)-293)))"'}
    assert merge("TIP", [Source("heat_flux", FLUX, "tag A", {"Heat Flux": 3.0}), Source("heat_flux", FLUX, "tag B", {"Heat Flux": 4.5})]).keywords == {"Heat Flux": 7.5}


def test_loads_that_do_not_add_and_fixed_values_that_differ_clash_while_equal_ones_merge() -> None:
    odd = merge("COIL", [Source("heat_flux", FLUX, "tag A", {"Heat Flux": "Variable Time\n Real 1"}), Source("heat_flux", FLUX, "tag B", {"Heat Flux": 2.0})])
    differ = merge("TIP", [Source("fixed_temperature", FIXED, "tag A", {"Temperature": 300.0}), Source("fixed_temperature", FIXED, "tag B", {"Temperature": 350.0})])
    agree = merge("TIP", [Source("fixed_temperature", FIXED, "tag A", {"Temperature": 300.0}), Source("fixed_temperature", FIXED, "tag B", {"Temperature": 300.0 + 1e-13})])

    assert odd.clashes == ["COIL: Heat Flux Heat Flux values Variable Time\n Real 1 and 2 cannot be added"]
    assert differ.keywords == {"Temperature": 300.0}, "the first value stays"
    assert differ.clashes == ["TIP: Temperature is 300 from Fixed Temperature (tag A) but 350 from Fixed Temperature (tag B); make them agree or remove one"]
    assert agree.clashes == [] and agree.keywords == {"Temperature": 300.0}


def test_a_second_condition_of_a_kind_that_stands_alone_clashes_once() -> None:
    merged = merge("SHELL", [Source("convection", FILM, "tag A", FILM.to_elmer()), Source("convection", FILM, "tag B", FILM.to_elmer())])

    assert merged.clashes == ["SHELL: two Convection conditions (tag A and tag B); keep one"]


def test_an_automatic_block_holds_a_fixed_value_an_authored_one_must_match() -> None:
    voltage = FixedVoltage(value=5 * U.V)
    merged = merge("AIR__EXT", [Source("fixed_voltage", voltage, "tag A", {"Potential": 5.0}), Source("far_field", None, "automatic", {"Potential": "Real 0"})])

    assert merged.clashes == ["AIR__EXT: Potential is 5 from Fixed Voltage (tag A) but Real 0 from Far Field (automatic); make them agree or remove one"]


def test_a_load_under_a_fixed_value_clashes_but_a_force_along_a_free_component_does_not() -> None:
    force = SurfaceForce(force=quantity(Vec3(0, 100, 0), "N"))
    held_x = FixedDisplacement(y=None, z=None)
    roller = merge("TIP", [Source("fixed_displacement", held_x, "tag A", {"Displacement 1": 0.0}), Source("surface_force", force, "tag B", {"Force 1": 0.0, "Force 2": 5.0, "Force 3": 0.0})])
    pinned = merge("TIP", [Source("fixed_displacement", held_x, "tag A", {"Displacement 2": 0.0}), Source("surface_force", force, "tag B", {"Force 1": 0.0, "Force 2": 5.0})])
    film = merge("TIP", [Source("convection", FILM, "tag A", FILM.to_elmer()), Source("fixed_temperature", FIXED, "tag B", {"Temperature": 300.0})])

    assert roller.clashes == [], "a zero force component along a held axis is no load"
    assert pinned.clashes == ["TIP: Surface Force (tag B) has no effect under the fixed Displacement 2 of Fixed Displacement (tag A); remove one"]
    assert film.clashes == ["TIP: Convection (tag A) has no effect under the fixed Temperature of Fixed Temperature (tag B); remove one"]
    assert FLUX.yields_to({"Heat Flux": 1.0}) == (("Heat Flux", "Temperature"),)


def test_two_forces_on_one_face_add_in_the_sif() -> None:
    pull = EntityTag(selector={TagKey.NAME: "Tip"}, conditions=[SurfaceForce(force=quantity(Vec3(0, 100, 0), "N")), SurfaceForce(force=quantity(Vec3(30, 20, 0), "N"))])
    hold = EntityTag(selector={TagKey.NAME: "Root"}, conditions=[FixedDisplacement()])
    groups: list[PhysicalGroup] = [FakePhysicalGroup(1, "BAR", N52(), [])]
    boundary_groups = [FakeBoundaryGroup(11, "TIP", ("BAR",), [101], tags=[pull], measure=0.01, named=True), FakeBoundaryGroup(12, "ROOT", ("BAR",), [102], tags=[hold], named=True)]
    sif = _write_sif(physics=Physics.LINEAR_ELASTICITY, groups=groups, boundary_groups=boundary_groups)

    assert "Force 1 = 3000.0" in sif and "Force 2 = 12000.0" in sif


def test_two_current_densities_on_one_body_add_and_two_magnetizations_must_agree() -> None:
    split = EntityTag(selector={TagKey.NAME: "Magnet"}, conditions=[Magnetization(direction=Direction(0, 0, 1)), Magnetization(direction=Direction(0, 1, 0))])
    drive = EntityTag(selector={TagKey.NAME: "Coil"}, conditions=[CurrentDensity(value=1e6 * U.A / U.m**2), CurrentDensity(value=5e5 * U.A / U.m**2)])
    coils: list[PhysicalGroup] = [FakePhysicalGroup(1, "COIL", N52(), [drive])]
    magnets: list[PhysicalGroup] = [FakePhysicalGroup(1, "MAGNET", N52(), [split])]
    coil = _write_sif(physics=Physics.MAGNETOSTATICS, groups=coils, validate=False, frame=Axisymmetric())

    assert "Current Density = 1500000.0" in coil
    with pytest.raises(ValueError, match=r"MAGNET: Magnetization directions disagree: \(0, 0, 1\) from tag MAGNET and \(0, 1, 0\) from tag MAGNET; make them agree or remove one"):
        _ = _write_sif(physics=Physics.MAGNETOSTATICS, groups=magnets)
