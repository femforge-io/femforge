"""Elmer variable names and the unit each carries."""

import pytest

from femforge.elmer.physics import Physics
from femforge.elmer.variables import ElmerVariable, elemental, variable_unit


def test_every_variable_but_the_potential_names_its_unit() -> None:
    assert {member: member.unit for member in ElmerVariable if member.unit is None} == {ElmerVariable.POTENTIAL: None}
    assert ElmerVariable.MAGNETIC_FLUX_DENSITY.unit == "T" and ElmerVariable.JFIX.unit == "A/m²"


@pytest.mark.parametrize(
    ("name", "physics", "unit"),
    [
        ("magnetic flux density", None, "T"),
        ("Magnetic Flux Density e", None, "T"),
        ("nodal force e", None, "N"),
        ("potential", None, None),
        ("potential", Physics.MAGNETOSTATICS_2D, "T·m"),
        ("potential", Physics.ELECTROSTATICS, "V"),
        ("GeometryIds", Physics.MAGNETOSTATICS, None),
    ],
)
def test_a_vtu_array_resolves_to_its_unit(name: str, physics: Physics | None, unit: str | None) -> None:
    assert variable_unit(name, physics) == unit


def test_the_elemental_name_takes_the_suffix_the_unit_lookup_strips() -> None:
    assert elemental(ElmerVariable.MAGNETIC_FLUX_DENSITY) == "Magnetic Flux Density e"
    assert variable_unit(elemental("Joule Heating")) == "W/m³"
