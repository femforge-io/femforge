"""STEP-level frame conversion: tagged import, the axisymmetric round trip about any axis, and Build123dGeometry export."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, cast

import build123d as b3d
import pytest

from femforge.common.frame import Axis, Axisymmetric, Solid
from femforge.common.tags import parse_tags
from femforge.common.vector import Direction, Vec3
from femforge.geometry import Build123dGeometry
from femforge.geometry.frame import apply_frame, import_tagged_step
from femforge.geometry.tags import TaggedCompound, TaggedPart


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


def test_a_solid_frame_meshes_the_authored_step(tmp_path: Path) -> None:
    authored = _Rig().build_step(tmp_path / "authored.step")
    assert apply_frame(authored, tmp_path / "out.step", Solid()) == authored
    assert not (tmp_path / "out.step").exists()


def test_an_axisymmetric_round_trip_sections_a_body_below_the_axis_origin(tmp_path: Path) -> None:
    authored = _Rig().build_step(tmp_path / "authored.step")
    out = apply_frame(authored, tmp_path / "solve.step", Axisymmetric())
    assert out == tmp_path / "solve.step"
    _assert_rig_section(out)


def test_an_axisymmetric_round_trip_about_an_offset_x_axis(tmp_path: Path) -> None:
    rig = _Rig(origin=(4.0, -3.0, 7.0), turn_y=90.0)
    authored = rig.build_step(tmp_path / "authored.step")
    frame = Axisymmetric(axis=Axis(origin=Vec3(4, -3, 7), direction=Direction(1, 0, 0)))
    _assert_rig_section(apply_frame(authored, authored, frame))


def test_import_inherits_assembly_names_and_placements(tmp_path: Path) -> None:
    magnet = TaggedPart(b3d.Box(1, 1, 1), tags={"Material": "N42"})
    stack = TaggedCompound(children=[magnet], tags={"Type": "Stack"})
    stack.location = b3d.Location((0, 0, 5))
    TaggedCompound(children=[stack]).export_step(str(tmp_path / "nested.step"), flatten=False)

    parts = [child for child in cast("tuple[object, ...]", import_tagged_step(tmp_path / "nested.step").children) if isinstance(child, TaggedPart)]
    assert [part.current_tags for part in parts] == [{"Type": "Stack", "Material": "N42"}]
    assert parts[0].bounding_box().center().Z == pytest.approx(5.0)
    assert parts[0].volume == pytest.approx(1.0)


def test_an_unnamed_body_raises(tmp_path: Path) -> None:
    TaggedCompound(children=[TaggedPart(b3d.Box(1, 1, 1))]).export_step(str(tmp_path / "bare.step"))
    with pytest.raises(ValueError, match="no key=value name"):
        _ = apply_frame(tmp_path / "bare.step", tmp_path / "out.step", Axisymmetric())


class _NestedRig(Build123dGeometry):
    """The rig inside a located, tagged sub-assembly."""

    def model(self) -> TaggedCompound:
        inner = _Rig().model()
        inner.set_tag("Group", "A")
        inner.location = b3d.Location((0, 0, 20))
        return TaggedCompound(children=[inner])


def test_build_step_carries_compound_locations_and_tags_into_the_section(tmp_path: Path) -> None:
    geometry = _NestedRig()
    out = apply_frame(geometry.build_step(tmp_path / "nested.step"), tmp_path / "solve.step", Axisymmetric())

    read_step = cast("Callable[[Path], b3d.Compound]", b3d.import_step)
    bodies = {body.label: body.bounding_box() for body in cast("tuple[b3d.Shell, ...]", read_step(out).children)}
    assert set(bodies) == {"Group=A,_Material=Steel316,_Name=Rod", "Group=A,_Material=N42"}
    rod = bodies["Group=A,_Material=Steel316,_Name=Rod"]
    ring = bodies["Group=A,_Material=N42"]
    assert (rod.min.Y, rod.max.Y, ring.min.Y, ring.max.Y) == pytest.approx((15.0, 25.0, 10.0, 14.0), abs=1e-6)


def test_an_already_2d_step_raises(tmp_path: Path) -> None:
    section = apply_frame(_Rig().build_step(tmp_path / "authored.step"), tmp_path / "solve.step", Axisymmetric())
    with pytest.raises(ValueError, match="already-2D STEP"):
        _ = apply_frame(section, tmp_path / "twice.step", Axisymmetric())
