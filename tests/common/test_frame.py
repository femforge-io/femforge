"""The solve frame models: normalized axes, alignment, and short labels."""

import pytest
from pydantic import ValidationError

from femforge.common.frame import Axis, Axisymmetric, Solid
from femforge.common.vector import Direction, Vec3


def test_an_axis_stores_its_direction_normalized() -> None:
    assert Axis(direction=Direction(0, 0, 2)).direction.to_list() == [0, 0, 1]
    assert Axis(direction=Direction(3, 4, 0)).direction.to_list() == pytest.approx([0.6, 0.8, 0])


def test_a_zero_axis_direction_is_rejected() -> None:
    with pytest.raises(ValidationError, match="non-zero vector"):
        _ = Axis.model_validate({"direction": [0, 0, 0]})


def test_along_is_the_signed_cosine_for_parallel_directions_only() -> None:
    assert Axis.Z.along(Vec3(0, 0, -5)) == pytest.approx(-1.0)
    assert Axis.Z.along(Vec3(0, 0, 1)) == pytest.approx(1.0)
    assert Axis.Z.along(Vec3(0.01, 0, 1)) is None


def test_labels_name_coordinate_axes_and_spell_out_the_rest() -> None:
    assert Solid().label == "solid"
    assert Axisymmetric().label == "axisymmetric about Z"
    assert Axisymmetric(axis=Axis(direction=Direction(0, -3, 0))).label == "axisymmetric about -Y"
    assert Axisymmetric(axis=Axis(origin=Vec3(1, 0, 0), direction=Direction(1, 0, 0))).label == "axisymmetric about (1, 0, 0) through (1, 0, 0)"


def test_a_radial_direction_is_stored_as_its_unit_part_across_the_axis() -> None:
    frame = Axisymmetric(axis=Axis.X, radial=Direction(5, 0, 2))
    assert frame.radial is not None
    assert frame.radial.to_list() == pytest.approx([0, 0, 1])
    assert frame.model_dump(mode="json")["radial"] == {"x": 0.0, "y": 0.0, "z": 1.0}


def test_a_radial_direction_along_the_axis_or_zero_is_rejected() -> None:
    with pytest.raises(ValidationError, match="parallel to the symmetry axis"):
        Axisymmetric(axis=Axis.X, radial=Direction(-2, 0, 0))

    with pytest.raises(ValidationError, match="non-zero vector"):
        _ = Axisymmetric.model_validate({"radial": [0, 0, 0]})


def test_an_unset_radial_stays_out_of_the_dump() -> None:
    assert "radial" not in Axisymmetric().model_dump(mode="json")


def test_an_explicit_none_radial_or_a_failed_axis_skips_the_radial_check() -> None:
    assert Axisymmetric(radial=None).radial is None

    with pytest.raises(ValidationError, match="non-zero vector"):
        _ = Axisymmetric.model_validate({"axis": {"direction": [0, 0, 0]}, "radial": [1, 0, 0]})


def test_axes_with_scaled_directions_are_equal_and_hash_equal() -> None:
    oblique = Axis(direction=Direction(1, 1, 0))
    scaled = Axis(direction=Direction(7, 7, 0))

    assert oblique == scaled
    assert hash(oblique) == hash(scaled)
    assert Axisymmetric(axis=oblique) == Axisymmetric(axis=scaled)
    assert hash(Axis.Z) == hash(Axis(direction=Direction(0, 0, 2)))
    assert Axis.Z != Axis(direction=Direction(0, 0, -1))


def test_a_dumped_frame_reloads_to_the_same_dump() -> None:
    frame = Axisymmetric(axis=Axis(origin=Vec3(1, 2, 3), direction=Direction(1, 2, 3)), radial=Direction(0, 3, 1))
    dumped = frame.model_dump(mode="json")

    reloaded = Axisymmetric.model_validate(dumped)

    assert reloaded == frame
    assert reloaded.model_dump(mode="json") == dumped
