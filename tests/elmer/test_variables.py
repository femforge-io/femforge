"""Elmer variable names and the unit each carries."""

import pytest

from femforge.common.utils import title
from femforge.elmer.physics import Physics
from femforge.elmer.variables import ElmerVariable, elemental, variable_of, variable_unit


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
        ("stress", Physics.LINEAR_ELASTICITY, "Pa"),
        ("vonmises", None, "Pa"),
        ("tresca", None, "Pa"),
        ("principal strain", None, "-"),
        ("strain energy density", None, "J/m³"),
    ],
)
def test_a_vtu_array_resolves_to_its_unit(name: str, physics: Physics | None, unit: str | None) -> None:
    assert variable_unit(name, physics) == unit


def test_the_elemental_name_takes_the_suffix_the_unit_lookup_strips() -> None:
    assert elemental(ElmerVariable.MAGNETIC_FLUX_DENSITY) == "Magnetic Flux Density e"
    assert variable_unit(elemental("Joule Heating")) == "W/m³"


def test_a_vtu_array_name_resolves_to_its_variable() -> None:
    assert variable_of("principal stress") is ElmerVariable.PRINCIPAL_STRESS
    assert variable_of("Stress e") is ElmerVariable.STRESS
    assert variable_of("GeometryIds") is None


def test_flows_draw_field_lines_by_default() -> None:
    flows = {member for member in ElmerVariable if member.lines_by_default}

    assert len(ElmerVariable) == 20
    assert flows == {
        ElmerVariable.MAGNETIC_FLUX_DENSITY,
        ElmerVariable.MAGNETIC_FIELD_STRENGTH,
        ElmerVariable.CURRENT_DENSITY,
        ElmerVariable.JFIX,
        ElmerVariable.VOLUME_CURRENT,
        ElmerVariable.ELECTRIC_FIELD,
    }


@pytest.mark.parametrize("member", list(ElmerVariable))
def test_every_variable_names_itself_for_a_viewer(member: ElmerVariable) -> None:
    expected: str = "Voltage Potential" if member is ElmerVariable.POTENTIAL else title(member.value)

    assert member.display_name == expected


def test_display_names_split_the_elmer_spellings() -> None:
    assert ElmerVariable.POTENTIAL.display_name == "Voltage Potential"
    assert ElmerVariable.VON_MISES.display_name == "Von Mises"
    assert ElmerVariable.MAGNETIC_FLUX_DENSITY.display_name == "Magnetic Flux Density"
