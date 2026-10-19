"""The Cartesian 2D frame: its fields, plane basis, physics forms, vector rules and the planar section it writes."""

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
from femforge.frames import Axis, Cartesian2D, PlaneState
from femforge.geometry import Build123dGeometry
from femforge.geometry.tags import TaggedCompound, TaggedFace, TaggedPart
from femforge.meshing.section_edges import SectionEdges
from femforge.physical.units import U


THICKNESS = 10 * U.mm


def _frame(**fields: object) -> Cartesian2D:
    return Cartesian2D.model_validate({"thickness": THICKNESS, **fields})


def test_a_cartesian_2d_frame_is_a_sectioned_2d_per_metre_solve() -> None:
    frame = _frame()

    assert frame.kind == "cartesian_2d"
    assert frame.label == "Cartesian 2D normal to Z"
    assert frame.model_dim == 2
    assert frame.coordinate_system is CoordinateSystem.CARTESIAN_2D
    assert not frame.coordinate_system.per_radian
    assert frame.rewrites_geometry is True
    assert frame.revolves is False
    assert frame.lumps_torque is False
    assert frame.thickness_m == pytest.approx(0.01)


def test_the_plane_state_picks_plane_stress_or_plane_strain() -> None:
    assert _frame().plane_stress is True
    assert _frame(state=PlaneState.STRAIN).plane_stress is False
    assert _frame(state="plane_strain").state is PlaneState.STRAIN


def test_labels_name_the_normal_without_units() -> None:
    assert _frame(normal=Axis(direction=Direction(-1, 0, 0))).label == "Cartesian 2D normal to -X"
    assert _frame(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1))).label == "Cartesian 2D normal to (0, 0, 1) through (0, 0, 5)"


@pytest.mark.parametrize(
    ("normal", "x_dir", "y_dir"),
    [
        (Direction(0, 0, 1), [1, 0, 0], [0, 1, 0]),
        (Direction(1, 0, 0), [0, 1, 0], [0, 0, 1]),
        (Direction(0, 1, 0), [1, 0, 0], [0, 0, -1]),
        (Direction(0, 0, -1), [1, 0, 0], [0, -1, 0]),
        (Direction(-1, 0, 0), [0, 1, 0], [0, 0, -1]),
        (Direction(0, -1, 0), [1, 0, 0], [0, 0, 1]),
    ],
)
def test_an_unset_x_dir_takes_the_world_axis_least_aligned_with_the_normal(normal: Direction, x_dir: list[float], y_dir: list[float]) -> None:
    plane = _frame(normal=Axis(direction=normal)).plane

    assert plane.x_dir.to_list() == pytest.approx(x_dir)
    assert plane.y_dir.to_list() == pytest.approx(y_dir)
    assert plane.normal == normal
    assert plane.origin == Vec3(0, 0, 0)


def test_an_oblique_normal_gets_a_right_handed_orthonormal_basis_through_its_origin() -> None:
    normal = Axis(origin=Vec3(1, 2, 3), direction=Direction(1, 1, 1))
    plane = _frame(normal=normal).plane

    assert plane.origin == Vec3(1, 2, 3)
    assert plane.x_dir.to_list() == pytest.approx([2 / math.sqrt(6), -1 / math.sqrt(6), -1 / math.sqrt(6)])
    assert plane.x_dir.dot(plane.y_dir) == pytest.approx(0.0, abs=1e-12)
    assert plane.x_dir.cross(plane.y_dir).to_list() == pytest.approx(normal.direction.to_list())


def test_an_x_dir_is_stored_as_its_unit_part_in_the_plane_and_reloads_unchanged() -> None:
    frame = _frame(x_dir=Direction(1, 1, 1))
    dumped = frame.model_dump(mode="json")

    assert frame.x_dir is not None
    assert frame.x_dir.to_list() == pytest.approx([1 / math.sqrt(2), 1 / math.sqrt(2), 0])
    assert frame.plane.x_dir == frame.x_dir
    assert frame.plane.y_dir.to_list() == pytest.approx([-1 / math.sqrt(2), 1 / math.sqrt(2), 0])
    assert Cartesian2D.model_validate(dumped).model_dump(mode="json") == dumped


def test_an_x_dir_along_the_normal_or_zero_is_rejected() -> None:
    with pytest.raises(ValidationError, match="parallel to the plane normal"):
        _ = _frame(x_dir=Direction(0, 0, -2))

    with pytest.raises(ValidationError, match="non-zero vector"):
        _ = _frame(x_dir=[0, 0, 0])


def test_an_unset_x_dir_stays_out_of_the_dump_and_a_failed_normal_skips_its_check() -> None:
    assert "x_dir" not in _frame().model_dump(mode="json")
    assert _frame(x_dir=None).x_dir is None

    with pytest.raises(ValidationError, match="non-zero vector"):
        _ = _frame(normal={"direction": [0, 0, 0]}, x_dir=[1, 0, 0])


def test_the_thickness_is_a_required_positive_length() -> None:
    with pytest.raises(ValidationError, match="thickness"):
        _ = Cartesian2D.model_validate({})

    with pytest.raises(ValidationError):
        _ = Cartesian2D(thickness=10 * U.s)

    with pytest.raises(ValidationError, match="must be positive"):
        _ = Cartesian2D(thickness=0 * U.mm)


def test_a_dumped_frame_reloads_equal() -> None:
    frame = _frame(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1)), state=PlaneState.STRAIN)

    assert Cartesian2D.model_validate(frame.model_dump(mode="json")) == frame


def test_magnetostatics_heat_conduction_and_elasticity_have_a_form_and_nothing_else_does() -> None:
    frame = _frame()
    forms = {
        Physics.MAGNETOSTATICS: Physics.MAGNETOSTATICS_2D,
        Physics.MAGNETOSTATICS_2D: Physics.MAGNETOSTATICS_2D,
        Physics.THERMAL: Physics.THERMAL,
        Physics.LINEAR_ELASTICITY: Physics.LINEAR_ELASTICITY,
    }

    for physics in Physics:
        if physics in forms:
            assert frame.solves(physics) is forms[physics]
            continue

        assert not frame.supports(physics)

        with pytest.raises(ValueError, match="no Cartesian 2D form"):
            _ = frame.solves(physics)


def test_an_in_plane_vector_solves_as_its_plane_components_and_an_off_plane_one_raises() -> None:
    assert _frame().solve_vector(Vec3(3, -4, 0)) == pytest.approx((3.0, -4.0))
    assert _frame(normal=Axis.X).solve_vector(Vec3(0, 3, 4)) == pytest.approx((3.0, 4.0))
    assert _frame(normal=Axis.Y).solve_vector(Vec3(1, 0, 2)) == pytest.approx((1.0, -2.0))

    with pytest.raises(ValueError, match="part along the plane normal Z"):
        _ = _frame().solve_vector(Vec3(1, 0, 1))


def test_the_total_scales_by_the_thickness_and_the_solve_share_divides_it_back() -> None:
    frame = _frame()
    total = frame.total(Vec3(-12.0, 3.0, 0.7))

    assert total.to_list() == pytest.approx([-0.12, 0.03, 0.0])
    assert frame.solve_share(total).to_list() == pytest.approx([-12.0, 3.0, 0.0])
    assert _frame(normal=Axis.X).authored_share(Vec3(0, 0.2, 0.1)).to_list() == pytest.approx([20.0, 10.0, 0.0])


def test_along_projects_onto_an_authored_axis_and_none_raises() -> None:
    force = Vec3(3, 4, 0)

    assert _frame().along(force, Axis.X) == pytest.approx(3.0)
    assert _frame().along(force, Axis(direction=Direction(0, -1, 0))) == pytest.approx(-4.0)
    assert _frame().along(force, Axis(direction=Direction(1, 1, 0))) == pytest.approx(7 / math.sqrt(2))
    assert _frame(normal=Axis.X).along(force, Axis.Z) == pytest.approx(4.0)
    assert _frame().along(force, Axis.Z) == 0.0

    with pytest.raises(ValueError, match="no axis of its own"):
        _ = _frame().along(force, None)


class _Prism(Build123dGeometry):
    """A 100 x 20 x 10 steel prism from the origin, with its end faces named Left and Right."""

    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    named: tuple[str, ...] = ("Left", "Right")

    def model(self) -> TaggedCompound:
        placement = b3d.Pos(*self.origin)
        box = placement * b3d.Box(100, 20, 10, align=(b3d.Align.MIN, b3d.Align.MIN, b3d.Align.MIN))
        prism = TaggedPart([box], tags={"Material": "Steel316"}, name="Prism")
        prism.color = b3d.Color(0.3, 0.3, 0.3)
        ends = box.faces().sort_by(b3d.Axis.X)
        flats = box.faces().sort_by(b3d.Axis.Z)
        faces = {"Left": ends[0], "Right": ends[-1], "Bottom": flats[0], "Top": flats[-1]}
        markers = [TaggedFace(faces[name], name=name) for name in self.named]

        return TaggedCompound(children=[prism, *markers])


class _NestedPrism(Build123dGeometry):
    """The prism inside a located, tagged sub-assembly."""

    def model(self) -> TaggedCompound:
        inner = _Prism().model()
        inner.set_tag("Group", "A")
        inner.location = b3d.Location((0, 0, 20))
        return TaggedCompound(children=[inner])


def _read(path: Path) -> dict[str, b3d.BoundBox]:
    read_step = cast("Callable[[Path], b3d.Compound]", b3d.import_step)
    children = cast("tuple[b3d.Compound, ...]", read_step(path).children)

    return {str(child.label): child.bounding_box() for child in children}


def _assert_prism_section(path: Path, *, offset: tuple[float, float] = (0.0, 0.0), group: str = "") -> None:
    """The section STEP holds the prism face flat at Z = 0 and the Left and Right edges, which its edge file names."""
    boxes = _read(path)
    body = f"{group}Material=Steel316,_Name=Prism"

    assert set(boxes) == {body, f"{group}Name=Left", f"{group}Name=Right"}
    assert parse_tags(body)["Name"] == "Prism"

    face = boxes[body]
    left = boxes[f"{group}Name=Left"]
    right = boxes[f"{group}Name=Right"]
    x0, y0 = offset

    assert (face.min.X, face.max.X, face.min.Y, face.max.Y) == pytest.approx((x0, x0 + 100.0, y0, y0 + 20.0), abs=1e-6)
    assert (left.min.X, left.max.X, left.min.Y, left.max.Y) == pytest.approx((x0, x0, y0, y0 + 20.0), abs=1e-6)
    assert (right.min.X, right.max.X) == pytest.approx((x0 + 100.0, x0 + 100.0), abs=1e-6)

    for box in (face, left, right):
        assert abs(box.min.Z) < 1e-9 and abs(box.max.Z) < 1e-9

    edges = SectionEdges.load(path)

    assert edges is not None
    assert [(edge.name, edge.span.length) for edge in edges.edges] == [(f"{group.replace(',_', ', ')}Name=Left", pytest.approx(20.0)), (f"{group.replace(',_', ', ')}Name=Right", pytest.approx(20.0))]


def test_prepare_geometry_sections_the_prism_and_its_named_faces_into_named_edges(tmp_path: Path) -> None:
    authored = _Prism().build_step(tmp_path / "authored.step")
    frame = _frame(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1)))

    out = frame.prepare_geometry(authored, tmp_path / "geometry.step")

    assert out == tmp_path / "geometry.step"
    assert (tmp_path / "geometry.boundaries.json").is_file()
    _assert_prism_section(out)


def test_prepare_geometry_sections_about_an_offset_origin(tmp_path: Path) -> None:
    authored = _Prism(origin=(4.0, -3.0, 7.0)).build_step(tmp_path / "authored.step")
    frame = _frame(normal=Axis(origin=Vec3(10, 2, 12), direction=Direction(0, 0, 1)))

    _assert_prism_section(frame.prepare_geometry(authored, tmp_path / "solve.step"), offset=(-6.0, -5.0))


def test_prepare_geometry_carries_compound_locations_and_tags_into_the_section(tmp_path: Path) -> None:
    authored = _NestedPrism().build_step(tmp_path / "nested.step")
    frame = _frame(normal=Axis(origin=Vec3(0, 0, 25), direction=Direction(0, 0, 1)))

    _assert_prism_section(frame.prepare_geometry(authored, tmp_path / "solve.step"), group="Group=A,_")


def test_a_plane_on_the_bottom_face_still_sections_the_prism_and_its_end_faces(tmp_path: Path) -> None:
    authored = _Prism().build_step(tmp_path / "authored.step")

    _assert_prism_section(_frame().prepare_geometry(authored, tmp_path / "solve.step"))


def test_a_named_face_in_the_section_plane_raises(tmp_path: Path) -> None:
    authored = _Prism(named=("Bottom",)).build_step(tmp_path / "authored.step")

    with pytest.raises(ValueError, match="'Name=Bottom' lies in the section plane"):
        _ = _frame().prepare_geometry(authored, tmp_path / "solve.step")


def test_a_named_face_parallel_to_the_plane_raises(tmp_path: Path) -> None:
    authored = _Prism(named=("Top",)).build_step(tmp_path / "authored.step")
    frame = _frame(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1)))

    with pytest.raises(ValueError, match="'Name=Top' does not cross the plane through"):
        _ = frame.prepare_geometry(authored, tmp_path / "solve.step")


def test_a_solid_off_the_plane_raises(tmp_path: Path) -> None:
    authored = _Prism(named=()).build_step(tmp_path / "authored.step")
    frame = _frame(normal=Axis(origin=Vec3(0, 0, 50), direction=Direction(0, 0, 1)))

    with pytest.raises(ValueError, match="planar section missed the solid tagged"):
        _ = frame.prepare_geometry(authored, tmp_path / "solve.step")


def test_a_section_without_named_faces_writes_no_edge_file_and_removes_a_stale_one(tmp_path: Path) -> None:
    authored = _Prism(named=()).build_step(tmp_path / "authored.step")
    stale = tmp_path / "solve.boundaries.json"
    _ = stale.write_bytes(b"{}")

    _ = _frame(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1))).prepare_geometry(authored, tmp_path / "solve.step")

    assert not stale.exists()
    assert SectionEdges.load(tmp_path / "solve.step") is None


def test_prepare_geometry_rejects_an_already_2d_step(tmp_path: Path) -> None:
    frame = _frame(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1)))
    section = frame.prepare_geometry(_Prism().build_step(tmp_path / "authored.step"), tmp_path / "solve.step")

    with pytest.raises(ValueError, match="already-2D STEP"):
        _ = frame.prepare_geometry(section, tmp_path / "twice.step")
