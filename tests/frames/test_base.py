"""The frame seam: axes, solve planes, the abstract base and the discriminated union."""

import pytest
from pydantic import TypeAdapter, ValidationError

from femforge.common.vector import Direction, Vec3
from femforge.elmer.physics import Physics
from femforge.frames import Axis, Axisymmetric, Frame, FrameUnion, Solid, SolvePlane, default_frame, shipped_frames


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


def test_axis_labels_name_coordinate_axes_and_spell_out_the_rest() -> None:
    assert Axis.Z.label == "Z"
    assert Axis(direction=Direction(0, -3, 0)).label == "-Y"
    assert Axis(origin=Vec3(1, 0, 0), direction=Direction(1, 0, 0)).label == "(1, 0, 0) through (1, 0, 0)"


def test_axes_with_scaled_directions_are_equal_and_hash_equal() -> None:
    oblique = Axis(direction=Direction(1, 1, 0))
    scaled = Axis(direction=Direction(7, 7, 0))

    assert oblique == scaled
    assert hash(oblique) == hash(scaled)
    assert hash(Axis.Z) == hash(Axis(direction=Direction(0, 0, 2)))
    assert Axis.Z != Axis(direction=Direction(0, 0, -1))


def test_a_solve_plane_is_frozen_and_round_trips() -> None:
    plane = SolvePlane(origin=Vec3(1, 2, 3), radial=Direction(2, 0, 0), axis=Direction(0, 0, 1))

    assert plane.radial.to_list() == [1, 0, 0]
    assert SolvePlane.model_validate(plane.model_dump(mode="json")) == plane

    with pytest.raises(ValidationError, match="frozen"):
        plane.origin = Vec3(0, 0, 0)


def test_the_base_frame_is_abstract() -> None:
    with pytest.raises(TypeError, match="abstract"):
        _ = Frame(kind="base")  # pyright: ignore[reportAbstractUsage]  # deliberately instantiating the abstract base


def test_the_union_loads_each_frame_by_its_kind() -> None:
    frames: TypeAdapter[FrameUnion] = TypeAdapter(FrameUnion)

    assert isinstance(frames.validate_python({"kind": "solid"}), Solid)
    assert frames.validate_python({"kind": "axisymmetric", "axis": {"direction": [0, 1, 0]}}) == Axisymmetric(axis=Axis.Y)

    with pytest.raises(ValidationError, match="kind"):
        _ = frames.validate_python({"kind": "sector"})


@pytest.mark.parametrize("frame", [Solid(), Axisymmetric()])
def test_supports_is_whether_solves_has_a_form(frame: Frame) -> None:
    for physics in Physics:
        try:
            _ = frame.solves(physics)
            solvable = True
        except ValueError:
            solvable = False

        assert frame.supports(physics) is solvable


@pytest.mark.parametrize("frame", [Solid(), Axisymmetric()])
def test_a_frame_rewrites_its_geometry_exactly_when_it_has_a_solve_plane(frame: Frame) -> None:
    assert frame.rewrites_geometry is (frame.plane is not None)


def test_the_shipped_frames_are_the_union_members_in_order() -> None:
    assert shipped_frames() == (Solid, Axisymmetric)


def test_the_default_frame_of_a_dimension_is_the_first_shipped_frame_that_solves_on_it() -> None:
    assert default_frame(2) == Axisymmetric() and default_frame(3) == Solid()
    with pytest.raises(ValueError, match="no shipped frame solves on 4 dimensions"):
        _ = default_frame(4)
