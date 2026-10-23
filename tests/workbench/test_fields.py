"""Tests for conditions and frame facts as read-only fields."""

from enum import StrEnum

from femforge.common.vector import Vec3
from femforge.physical.conditions import FixedDisplacement, SurfaceForce
from femforge.physical.units import quantity
from femforge_workbench.panels.fields import automatic_card, components, condition_card, fact_field, views


class Mode(StrEnum):
    PLANE_STRESS = "plane_stress"


def test_a_vector_quantity_reads_as_magnitude_and_direction() -> None:
    magnitude, direction = views("Force", quantity(Vec3(3, 4, 0), "N"))

    assert magnitude == {"label": "Magnitude", "kind": "quantity", "value": "5", "unit": "N"}
    assert direction == {"label": "Direction", "kind": "vector", "values": ["0.6", "0.8", "0"], "axes": ["X", "Y", "Z"]}


def test_each_plain_value_reads_in_the_control_that_would_author_it() -> None:
    assert views("Pressure", quantity(2.5, "MPa")) == [{"label": "Pressure", "kind": "quantity", "value": "2.5", "unit": "MPa"}]
    assert views("Offset", Vec3(1, 0.5, 0)) == [{"label": "Offset", "kind": "vector", "values": ["1", "0.5", "0"], "axes": ["X", "Y", "Z"]}]
    assert views("Enabled", True) == [{"label": "Enabled", "kind": "choice", "value": "On"}]
    assert views("Enabled", False) == [{"label": "Enabled", "kind": "choice", "value": "Off"}]
    assert views("Mode", Mode.PLANE_STRESS) == [{"label": "Mode", "kind": "choice", "value": "Plane Stress"}]
    assert views("Turns", 12) == [{"label": "Turns", "kind": "text", "value": "12"}]
    assert views("Name", "coil") == [{"label": "Name", "kind": "text", "value": "coil"}]


def test_an_unset_or_unknown_value_shows_no_field() -> None:
    assert views("Missing", None) == []
    assert views("Shape", object()) == []


def test_a_clamp_reads_as_one_vector_with_a_free_axis_and_the_dropped_axis_muted() -> None:
    card = condition_card(FixedDisplacement(y=None), icon="anchor", dropped=("z",), dropped_tip=lambda axis: f"No {axis} in 2D")

    assert card == {
        "title": "Fixed Displacement",
        "icon": "anchor",
        "fields": [{"label": "Displacement", "kind": "vector", "values": ["0", None, "0"], "axes": ["X", "Y", "Z"], "unit": "mm", "dropped": [2], "tip": "No z in 2D"}],
    }


def test_a_force_mutes_its_dropped_direction_component() -> None:
    card = condition_card(SurfaceForce(force=quantity(Vec3(0, 120, 0), "N")), icon=None, dropped=("z",), dropped_tip=lambda axis: f"No {axis}")

    assert "icon" not in card
    direction = next(field for field in card["fields"] if field["label"] == "Direction")
    assert direction.get("dropped") == [2]
    assert direction.get("tip") == "No z"


def test_a_condition_femforge_set_names_its_source() -> None:
    assert automatic_card("fixed_potential", "Set by femforge") == {"title": "Fixed Potential", "fields": [{"label": "Source", "kind": "text", "value": "Set by femforge"}]}


def test_a_frame_fact_reads_as_a_vector_a_quantity_or_text() -> None:
    assert fact_field("Normal", "(0, 0, 1)") == {"label": "Normal", "kind": "vector", "values": ["0", "0", "1"], "axes": ["X", "Y", "Z"]}
    assert fact_field("Origin", "(0, 0, 5) mm") == {"label": "Origin", "kind": "vector", "values": ["0", "0", "5"], "axes": ["X", "Y", "Z"], "unit": "mm"}
    assert fact_field("Thickness", "10 mm") == {"label": "Thickness", "kind": "quantity", "value": "10", "unit": "mm"}
    assert fact_field("Solve X, Y", "+X, +Y") == {"label": "Solve X, Y", "kind": "text", "value": "+X, +Y"}
    assert fact_field("Plane State", "Plane Stress", "No stress through the thickness.").get("tip") == "No stress through the thickness."


def test_components_split_a_bare_list_too() -> None:
    assert components("1, 2") == ["1", "2"]
