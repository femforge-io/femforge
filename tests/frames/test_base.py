"""The frame seam: axes, solve planes, the abstract base and the discriminated union."""

import math

import pytest
from pydantic import TypeAdapter, ValidationError

from femforge.common.vector import Direction, Vec3
from femforge.elmer.physics import Physics
from femforge.frames import Axis, Axisymmetric, Cartesian2D, Frame, FrameUnion, Solid, SolvePlane, default_frame, shipped_frames
from femforge.frames.base import in_plane, least_aligned_world_axis
from femforge.physical.units import U


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
    plane = SolvePlane(origin=Vec3(1, 2, 3), x_dir=Direction(2, 0, 0), y_dir=Direction(0, 0, 1))

    assert plane.x_dir.to_list() == [1, 0, 0]
    assert plane.normal == Direction(0, -1, 0)
    assert set(plane.model_dump(mode="json")) == {"origin", "x_dir", "y_dir"}
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

    assert frames.validate_python({"kind": "cartesian_2d", "thickness": "10 mm"}) == Cartesian2D(thickness=10 * U.mm)

    with pytest.raises(ValidationError, match="kind"):
        _ = frames.validate_python({"kind": "sector"})


@pytest.mark.parametrize("frame", [Solid(), Axisymmetric(), Cartesian2D(thickness=10 * U.mm)])
def test_supports_is_whether_solves_has_a_form(frame: Frame) -> None:
    for physics in Physics:
        try:
            _ = frame.solves(physics)
            solvable = True
        except ValueError:
            solvable = False

        assert frame.supports(physics) is solvable


@pytest.mark.parametrize("frame", [Solid(), Axisymmetric(), Cartesian2D(thickness=10 * U.mm)])
def test_a_frame_rewrites_its_geometry_exactly_when_it_has_a_solve_plane(frame: Frame) -> None:
    assert frame.rewrites_geometry is (frame.plane is not None)


def test_the_shipped_frames_are_the_union_members_in_order() -> None:
    assert shipped_frames() == (Solid, Axisymmetric, Cartesian2D)


def test_the_default_frame_of_a_dimension_is_the_first_shipped_frame_that_solves_on_it() -> None:
    assert default_frame(2) == Axisymmetric() and default_frame(3) == Solid()
    with pytest.raises(ValueError, match="no shipped frame solves on 4 dimensions"):
        _ = default_frame(4)


@pytest.mark.parametrize("frame", [Solid(), Axisymmetric(), Cartesian2D(thickness=10 * U.mm)])
def test_the_solve_share_of_a_total_is_what_the_solve_integrated(frame: Frame) -> None:
    integrated = Vec3(0.0, -2.293, 0.0) if frame.revolves else Vec3(-12.58, -2.293, 0.5 if frame.model_dim == 3 else 0.0)
    share = frame.solve_share(frame.total(integrated))

    assert share.to_list() == pytest.approx(integrated.to_list())


@pytest.mark.parametrize(
    ("frame", "authored", "expected"),
    [
        (Solid(), Vec3(1, 2, 3), [1.0, 2.0, 3.0]),
        (Axisymmetric(), Vec3(0, 0, 2 * math.pi), [0.0, 1.0, 0.0]),
        (Cartesian2D(thickness=10 * U.mm), Vec3(100, 50, 0), [10000.0, 5000.0, 0.0]),
    ],
)
def test_the_authored_share_is_the_solve_share_of_the_solve_vector(frame: Frame, authored: Vec3, expected: list[float]) -> None:
    assert frame.authored_share(authored).to_list() == pytest.approx(expected)


def test_an_authored_vector_with_no_form_in_the_frame_has_no_share() -> None:
    with pytest.raises(ValueError, match="part along the plane normal"):
        _ = Cartesian2D(thickness=10 * U.mm).authored_share(Vec3(0, 0, 1))

    with pytest.raises(ValueError, match="not along the symmetry axis"):
        _ = Axisymmetric().authored_share(Vec3(1, 0, 0))


def test_only_the_axisymmetric_frame_revolves_and_only_the_cartesian_2d_frame_poses_a_plane_state() -> None:
    planar = Cartesian2D(thickness=10 * U.mm)

    assert [frame.revolves for frame in (Solid(), Axisymmetric(), planar)] == [False, True, False]
    assert Solid().plane_stress is None and Axisymmetric().plane_stress is None
    assert planar.plane_stress is True


def test_the_least_aligned_world_axis_prefers_x_then_y() -> None:
    assert least_aligned_world_axis(Vec3(0, 0, 1)) == Vec3(1, 0, 0)
    assert least_aligned_world_axis(Vec3(1, 0, 0)) == Vec3(0, 1, 0)
    assert least_aligned_world_axis(Vec3(1, 1, 0)) == Vec3(0, 0, 1)


def test_in_plane_keeps_a_perpendicular_direction_and_projects_the_rest() -> None:
    normal = Direction(0, 0, 1)
    across = Direction(0, 1, 0)

    assert in_plane(across, normal) is across
    assert in_plane(Direction(3, 0, 4), normal) == Direction(1, 0, 0)

    with pytest.raises(ValueError, match="parallel to"):
        _ = in_plane(Direction(0, 0, -2), normal)
