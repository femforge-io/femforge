"""The frame checks: solids and faces of revolution about an axis, and prisms through a thickness."""

from __future__ import annotations

import math
from typing import NoReturn, Protocol, cast

import build123d as b3d
import pytest
from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert  # pyright: ignore[reportAttributeAccessIssue, reportUnknownVariableType]  # OCP ships no stubs

from femforge.common.log import log
from femforge.common.vector import Direction, Vec3
from femforge.frames import Axis, BodyMismatch, Cartesian2D, FrameCheck, SolvePlane
from femforge.geometry.conformance import MISMATCH_TOLERANCE, prism_mismatches, revolution_mismatches
from femforge.geometry.tags import TaggedCompound, TaggedFace, TaggedPart
from femforge.geometry.utils import Shape
from femforge.physical.units import U
from tests.geometry.flexures import notch_flexure, toroidal_flexure


CYLINDER = b3d.Cylinder(10, 30)
BOX = b3d.Box(100, 20, 10, align=(b3d.Align.MIN, b3d.Align.MIN, b3d.Align.MIN))
MID_DEPTH = Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1))


class _Conversion(Protocol):
    def Shape(self) -> object: ...


def _failing(measured: list[BodyMismatch]) -> list[tuple[str, FrameCheck, float]]:
    return [(measurement.body, measurement.check, measurement.fraction) for measurement in measured if measurement.fraction > MISMATCH_TOLERANCE]


def _model(*shapes: b3d.Part, faces: dict[str, b3d.Face] | None = None) -> TaggedCompound:
    parts = [TaggedPart([shape], name=f"Body{index}") for index, shape in enumerate(shapes)]
    named = [TaggedFace(face, name=name) for name, face in (faces or {}).items()]

    return TaggedCompound(children=[*parts, *named])


def _nurbs(part: b3d.Part) -> b3d.Part:
    """``part`` with every surface turned into an exact NURBS, as third-party CAD exports write them."""
    conversion = cast("_Conversion", BRepBuilderAPI_NurbsConvert(part.wrapped, True))
    converted = b3d.Compound(conversion.Shape())

    return b3d.Part(list(converted.solids()))


def _plane(thickness: float = 10.0, normal: Axis = MID_DEPTH) -> tuple[SolvePlane, Cartesian2D]:
    frame = Cartesian2D(normal=normal, thickness=thickness * U.mm)

    return frame.plane, frame


def _prism(compound: TaggedCompound, thickness: float = 10.0, normal: Axis = MID_DEPTH) -> list[BodyMismatch]:
    plane, frame = _plane(thickness, normal)

    return prism_mismatches(compound, plane, frame.thickness)


def test_a_cylinder_about_its_axis_passes_turned_or_not() -> None:
    assert _failing(revolution_mismatches(_model(CYLINDER), Axis.Z)) == []
    assert _failing(revolution_mismatches(_model(CYLINDER.rotate(b3d.Axis.Z, 37)), Axis.Z)) == []


def test_every_body_and_face_is_measured_passing_ones_too() -> None:
    side = CYLINDER.faces().filter_by(b3d.GeomType.CYLINDER)[0]
    measured = revolution_mismatches(_model(CYLINDER, faces={"Side": side}), Axis.Z)

    assert [(measurement.body, measurement.check) for measurement in measured] == [("Name=Body0", FrameCheck.TURNED), ("Name=Side", FrameCheck.TURNED)]
    assert _failing(measured) == []


@pytest.mark.parametrize(
    ("shape", "fraction"),
    [
        pytest.param(CYLINDER - b3d.Pos(5, 0, 0) * b3d.Cylinder(1, 40), 2.02e-2, id="off-axis-hole"),
        pytest.param(b3d.Pos(0.01, 0, 0) * CYLINDER, 8.08e-4, id="cylinder-0.01-mm-off-the-axis"),
        pytest.param(b3d.Box(20, 20, 30), 0.3336, id="box"),
    ],
)
def test_a_body_that_changes_when_turned_fails_by_its_changed_share(shape: b3d.Part, fraction: float) -> None:
    failing = _failing(revolution_mismatches(_model(shape), Axis.Z))

    assert failing == [("Name=Body0", FrameCheck.TURNED, pytest.approx(fraction, rel=1e-2))]


def test_a_half_side_face_fails_while_its_cylinder_passes() -> None:
    side = CYLINDER.faces().filter_by(b3d.GeomType.CYLINDER)[0]
    half = cast("b3d.Part", side & b3d.Box(40, 40, 40, align=(b3d.Align.MIN, b3d.Align.CENTER, b3d.Align.CENTER))).faces()[0]

    failing = _failing(revolution_mismatches(_model(CYLINDER, faces={"HalfSide": half}), Axis.Z))

    assert failing == [("Name=HalfSide", FrameCheck.TURNED, pytest.approx(0.411, rel=1e-2))]


def test_the_toroidal_flexure_revolves_about_x_and_the_notch_flexure_does_not() -> None:
    assert _failing(revolution_mismatches(_model(toroidal_flexure()), Axis.X)) == []

    failing = _failing(revolution_mismatches(_model(notch_flexure()), Axis.X))

    assert failing == [("Name=Body0", FrameCheck.TURNED, pytest.approx(0.710, rel=1e-2))]


def test_a_nurbs_sphere_passes_about_a_tilted_offset_axis() -> None:
    tilt = math.radians(37.0)
    direction = (math.sin(tilt) * math.cos(0.3), math.sin(tilt) * math.sin(0.3), math.cos(tilt))
    origin = (3.3, -1.7, 12.9)
    sphere = b3d.Plane(origin=origin, z_dir=direction).location * _nurbs(b3d.Sphere(7))

    assert _failing(revolution_mismatches(_model(sphere), Axis(origin=Vec3(*origin), direction=Direction(*direction)))) == []


def test_a_box_with_named_end_faces_is_a_prism_through_its_thickness() -> None:
    ends = BOX.faces().sort_by(b3d.Axis.X)
    measured = _prism(_model(BOX, faces={"Left": ends[0], "Right": ends[-1]}))

    assert [(measurement.body, measurement.check) for measurement in measured] == [
        ("Name=Body0", FrameCheck.DEPTH),
        ("Name=Body0", FrameCheck.PRISM),
        ("Name=Left", FrameCheck.PRISM),
        ("Name=Right", FrameCheck.PRISM),
    ]
    assert _failing(measured) == []


def test_a_thickness_other_than_the_depth_fails_the_depth_check() -> None:
    ends = BOX.faces().sort_by(b3d.Axis.X)
    failing = _failing(_prism(_model(BOX, faces={"Left": ends[0], "Right": ends[-1]}), thickness=20.0))

    assert failing == [("Name=Body0", FrameCheck.DEPTH, pytest.approx(0.5))]


def test_a_named_end_face_is_no_prism() -> None:
    bottom = BOX.faces().sort_by(b3d.Axis.Z)[0]

    assert _failing(_prism(_model(BOX, faces={"Bottom": bottom}))) == [("Name=Bottom", FrameCheck.PRISM, 1.0)]


def test_a_tapered_extrusion_fails_by_its_taper() -> None:
    wedge = cast("b3d.Part", b3d.extrude(b3d.Rectangle(100, 20), 10, taper=5))

    assert _failing(_prism(_model(wedge))) == [("Name=Body0", FrameCheck.PRISM, pytest.approx(2.73e-2, rel=1e-2))]


@pytest.mark.parametrize("depth", [5.0, 3.0], ids=["reaching-mid-depth", "below-mid-depth"])
def test_a_body_short_of_the_model_depth_fails_the_prism_check(depth: float) -> None:
    short = b3d.Pos(0, 30, 0) * b3d.Box(100, 20, depth, align=(b3d.Align.MIN, b3d.Align.MIN, b3d.Align.MIN))
    failing = _failing(_prism(_model(BOX, short)))

    assert ("Name=Body1", FrameCheck.PRISM, 1.0) in failing
    assert {body for body, _, _ in failing} == {"Name=Body1"}


def test_the_notch_flexure_is_a_prism_and_the_toroidal_flexure_is_not() -> None:
    assert _failing(_prism(_model(notch_flexure()))) == []

    failing = {check: fraction for _, check, fraction in _failing(_prism(_model(toroidal_flexure())))}

    assert failing == {FrameCheck.DEPTH: pytest.approx(1.0), FrameCheck.PRISM: pytest.approx(0.499, rel=1e-2)}


@pytest.mark.parametrize("nurbs", [False, True], ids=["exact", "nurbs"])
def test_a_plate_with_a_named_hole_passes_under_a_tilted_offset_normal(nurbs: bool) -> None:
    tilt = math.radians(37.0)
    direction = (math.sin(tilt), 0.0, math.cos(tilt))
    origin = (1.1, 2.2, 3.3)
    plate = cast("b3d.Part", b3d.extrude(cast("b3d.Sketch", b3d.Rectangle(100, 20) - b3d.Circle(4)), 5, both=True))
    placed = b3d.Plane(origin=origin, z_dir=direction).location * (_nurbs(plate) if nurbs else plate)
    hole = next(face for face in placed.faces() if 200 < face.area < 300)

    assert _failing(_prism(_model(placed, faces={"Hole": hole}), normal=Axis(origin=Vec3(*origin), direction=Direction(*direction)))) == []


def test_a_model_without_a_solid_measures_nothing() -> None:
    assert _prism(TaggedCompound(children=[TaggedFace(BOX.faces()[0], name="Lone")])) == []


def test_a_boolean_that_raises_skips_its_leaf_with_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> NoReturn:
        raise ValueError("null shape")

    monkeypatch.setattr(Shape, "cut", refuse)
    warnings: list[str] = []
    sink_id: int = log.add(warnings.append, level="WARNING", format="{message}")

    try:
        turned = revolution_mismatches(_model(CYLINDER), Axis.Z)
        prism = _prism(_model(BOX))
    finally:
        log.remove(sink_id)

    assert turned == []
    assert prism == []
    assert [message.strip() for message in warnings] == ["Frame check skipped.", "Frame check skipped."]
