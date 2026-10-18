"""The axisymmetric frame: its fields, solve plane, physics forms, vector rules and the STEP section it writes."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, cast

import build123d as b3d
import pytest
from pydantic import ValidationError

from femforge.common.tags import parse_tags
from femforge.common.vector import Direction, Vec3
from femforge.elmer.config import CoordinateSystem
from femforge.elmer.physics import Physics
from femforge.frames import Axis, Axisymmetric
from femforge.geometry import Build123dGeometry
from femforge.geometry.tags import TaggedCompound, TaggedPart


def test_an_axisymmetric_frame_is_a_sectioned_2d_per_radian_solve() -> None:
    frame = Axisymmetric()

    assert frame.model_dim == 2
    assert frame.coordinate_system is CoordinateSystem.AXI_SYMMETRIC
    assert frame.coordinate_system.per_radian
    assert frame.rewrites_geometry is True
    assert frame.lumps_torque is False
    assert frame.revolves is True


def test_labels_name_the_symmetry_axis() -> None:
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


def test_frames_about_scaled_axes_are_equal() -> None:
    assert Axisymmetric(axis=Axis(direction=Direction(1, 1, 0))) == Axisymmetric(axis=Axis(direction=Direction(7, 7, 0)))


def test_a_dumped_frame_reloads_to_the_same_dump() -> None:
    frame = Axisymmetric(axis=Axis(origin=Vec3(1, 2, 3), direction=Direction(1, 2, 3)), radial=Direction(0, 3, 1))
    dumped = frame.model_dump(mode="json")

    reloaded = Axisymmetric.model_validate(dumped)

    assert reloaded == frame
    assert reloaded.model_dump(mode="json") == dumped


@pytest.mark.parametrize(
    ("axis", "radial"),
    [
        (Axis.Z, [1, 0, 0]),
        (Axis.Y, [1, 0, 0]),
        (Axis.X, [0, 1, 0]),
        (Axis(direction=Direction(1, 1, 0)), [0, 0, 1]),
        (Axis(direction=Direction(1, 0, 1)), [0, 1, 0]),
    ],
)
def test_an_unset_radial_faces_the_world_axis_least_aligned_with_the_axis_x_first(axis: Axis, radial: list[float]) -> None:
    plane = Axisymmetric(axis=axis).plane

    assert plane.x_dir.to_list() == pytest.approx(radial)
    assert plane.y_dir == axis.direction
    assert plane.origin == Vec3(0, 0, 0)
    assert plane.normal.to_list() == pytest.approx(Vec3(*radial).cross(axis.direction).to_list())


def test_the_plane_takes_the_axis_origin_and_the_stored_radial() -> None:
    frame = Axisymmetric(axis=Axis(origin=Vec3(10, 0, 0), direction=Direction(0, 0, 1)), radial=Direction(0, 1, 1))
    plane = frame.plane

    assert plane.origin == Vec3(10, 0, 0)
    assert plane.x_dir.to_list() == pytest.approx([0, 1, 0])
    assert plane.y_dir.to_list() == [0, 0, 1]
    assert plane.normal.to_list() == pytest.approx([1, 0, 0])


def test_magnetostatics_and_heat_conduction_have_a_form_and_nothing_else_does() -> None:
    frame = Axisymmetric()

    assert frame.solves(Physics.MAGNETOSTATICS) is Physics.MAGNETOSTATICS_2D
    assert frame.solves(Physics.MAGNETOSTATICS_2D) is Physics.MAGNETOSTATICS_2D
    assert frame.solves(Physics.THERMAL) is Physics.THERMAL

    for physics in set(Physics) - {Physics.MAGNETOSTATICS, Physics.MAGNETOSTATICS_2D, Physics.THERMAL}:
        assert not frame.supports(physics)

        with pytest.raises(ValueError, match="no axisymmetric form"):
            _ = frame.solves(physics)


def test_a_vector_along_the_axis_solves_as_its_signed_axial_length() -> None:
    frame = Axisymmetric(axis=Axis(direction=Direction(0, -0.6, -0.8)))

    assert frame.solve_vector(Vec3(0, -6, -8)) == pytest.approx((0.0, 10.0))
    assert frame.solve_vector(Vec3(0, 3, 4)) == pytest.approx((0.0, -5.0))

    with pytest.raises(ValueError, match="not along the symmetry axis"):
        _ = Axisymmetric().solve_vector(Vec3(1, 0, 1))


def test_the_total_keeps_only_the_axial_component_times_tau() -> None:
    total = Axisymmetric().total(Vec3(-12.58, -2.293, 0.5))

    assert total.x == 0.0
    assert total.z == 0.0
    assert total.y == pytest.approx(-2.293 * math.tau)


def test_along_reads_the_axial_component_signed_by_the_given_axis() -> None:
    frame = Axisymmetric(axis=Axis.Y)
    force = Vec3(0, -3, 0)

    assert frame.along(force, None) == -3
    assert frame.along(force, Axis(direction=Direction(0, -2, 0))) == pytest.approx(3.0)

    with pytest.raises(ValueError, match="only along its symmetry axis"):
        _ = frame.along(force, Axis.X)


class _Rig(Build123dGeometry):
    """A rod on the Z axis and an uncolored ring below Z = 0, placed by ``origin`` and ``turn_y``."""

    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    turn_y: float = 0.0

    def model(self) -> TaggedCompound:
        placement = b3d.Pos(*self.origin) * b3d.Rot(Y=self.turn_y)
        rod = TaggedPart(placement * b3d.Cylinder(radius=2.0, height=10.0), tags={"Material": "Steel316"}, name="Rod")
        rod.color = b3d.Color(0.3, 0.3, 0.3)
        ring_shape = b3d.Pos(0, 0, -8) * (b3d.Cylinder(radius=5.0, height=4.0) - b3d.Cylinder(radius=3.0, height=8.0))
        ring = TaggedPart(placement * ring_shape, tags={"Material": "N42"})
        return TaggedCompound(children=[rod, ring])


class _NestedRig(Build123dGeometry):
    """The rig inside a located, tagged sub-assembly."""

    def model(self) -> TaggedCompound:
        inner = _Rig().model()
        inner.set_tag("Group", "A")
        inner.location = b3d.Location((0, 0, 20))
        return TaggedCompound(children=[inner])


def _assert_rig_section(path: Path) -> None:
    """The section STEP holds both rig bodies flat at Z = 0, by name, with their wall profiles and the rod's color."""
    read_step = cast("Callable[[Path], b3d.Compound]", b3d.import_step)
    bodies = {body.label: body for body in cast("tuple[b3d.Shell, ...]", read_step(path).children)}
    assert {label: parse_tags(label) for label in bodies} == {
        "Material=Steel316,_Name=Rod": {"Material": "Steel316", "Name": "Rod"},
        "Material=N42": {"Material": "N42"},
    }

    rod = bodies["Material=Steel316,_Name=Rod"]
    ring = bodies["Material=N42"]
    rod_box = rod.bounding_box()
    ring_box = ring.bounding_box()
    assert rod.color is not None
    assert (rod.area, rod_box.min.X, rod_box.max.X, rod_box.min.Y, rod_box.max.Y) == pytest.approx((20.0, 0.0, 2.0, -5.0, 5.0), abs=1e-6)
    assert (ring.area, ring_box.min.X, ring_box.max.X, ring_box.min.Y, ring_box.max.Y) == pytest.approx((8.0, 3.0, 5.0, -10.0, -6.0), abs=1e-6)
    for box in (rod_box, ring_box):
        assert abs(box.min.Z) < 1e-9 and abs(box.max.Z) < 1e-9


def test_prepare_geometry_sections_a_body_below_the_axis_origin(tmp_path: Path) -> None:
    authored = _Rig().build_step(tmp_path / "authored.step")
    out = Axisymmetric().prepare_geometry(authored, tmp_path / "solve.step")

    assert out == tmp_path / "solve.step"
    _assert_rig_section(out)


def test_prepare_geometry_sections_about_an_offset_x_axis(tmp_path: Path) -> None:
    authored = _Rig(origin=(4.0, -3.0, 7.0), turn_y=90.0).build_step(tmp_path / "authored.step")
    frame = Axisymmetric(axis=Axis(origin=Vec3(4, -3, 7), direction=Direction(1, 0, 0)))

    _assert_rig_section(frame.prepare_geometry(authored, authored))


def test_prepare_geometry_carries_compound_locations_and_tags_into_the_section(tmp_path: Path) -> None:
    out = Axisymmetric().prepare_geometry(_NestedRig().build_step(tmp_path / "nested.step"), tmp_path / "solve.step")

    read_step = cast("Callable[[Path], b3d.Compound]", b3d.import_step)
    bodies = {body.label: body.bounding_box() for body in cast("tuple[b3d.Shell, ...]", read_step(out).children)}
    assert set(bodies) == {"Group=A,_Material=Steel316,_Name=Rod", "Group=A,_Material=N42"}
    rod = bodies["Group=A,_Material=Steel316,_Name=Rod"]
    ring = bodies["Group=A,_Material=N42"]
    assert (rod.min.Y, rod.max.Y, ring.min.Y, ring.max.Y) == pytest.approx((15.0, 25.0, 10.0, 14.0), abs=1e-6)


def test_prepare_geometry_rejects_an_already_2d_step(tmp_path: Path) -> None:
    section = Axisymmetric().prepare_geometry(_Rig().build_step(tmp_path / "authored.step"), tmp_path / "solve.step")

    with pytest.raises(ValueError, match="already-2D STEP"):
        _ = Axisymmetric().prepare_geometry(section, tmp_path / "twice.step")
