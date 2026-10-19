"""The Frame section rows and the 2D face measures each frame reads."""

import pytest

from femforge.common.vector import Direction, Vec3
from femforge.frames import Axis, Axisymmetric, Cartesian2D, FrameFact, PlaneState, Solid
from femforge.frames.base import axis_word
from femforge.physical.units import quantity


def _read(facts: tuple[FrameFact, ...]) -> list[tuple[str, str]]:
    return [(fact.label, fact.value) for fact in facts]


def test_a_solid_has_no_frame_rows_and_no_face_measure() -> None:
    assert Solid().facts() == ()
    assert Solid().face_measure(0.02, Vec3(0, 0, 0)) is None


def test_a_cartesian_2d_frame_reads_its_plane_thickness_and_state() -> None:
    frame = Cartesian2D(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1)), thickness=quantity(10, "mm"))

    assert _read(frame.facts()) == [("Normal", "(0, 0, 1)"), ("Plane Origin", "(0, 0, 5) mm"), ("Solve X, Y", "+X, +Y"), ("Thickness", "10 mm"), ("Plane State", "Plane Stress")]
    assert frame.face_measure(0.02, Vec3(0.05, 0, 0)) == FrameFact(label="Face Area", value="200 mm² at 10 mm Thickness")


def test_a_plane_strain_frame_off_the_world_axes_reads_its_axes_in_component_form() -> None:
    frame = Cartesian2D(normal=Axis(direction=Direction(1, 1, 0)), thickness=quantity(10, "mm"), state=PlaneState.STRAIN)

    assert _read(frame.facts()) == [
        ("Normal", "(0.707, 0.707, 0)"),
        ("Plane Origin", "(0, 0, 0) mm"),
        ("Solve X, Y", "+Z, (0.707, -0.707, 0)"),
        ("Thickness", "10 mm"),
        ("Plane State", "Plane Strain"),
    ]


def test_an_axisymmetric_frame_reads_its_axis_and_the_revolved_area() -> None:
    frame = Axisymmetric(axis=Axis.Z)
    fact = frame.face_measure(0.0314, Vec3(0.01, 0.0, 0.0))

    assert _read(frame.facts()) == [("Axis", "(0, 0, 1)"), ("Axis Origin", "(0, 0, 0) mm"), ("Radial, Axial", "+X, +Z")]
    assert frame.axis_names == ("Radial", "Axial") and Solid().axis_names == ("X", "Y")
    assert fact is not None and fact.label == "Revolved Area" and fact.value == "2π·r̄·L = 1970 mm²"
    assert frame.face_measure(0.062, Vec3(1e-18, 0.03, 0.0)) is None, "a line on the axis sweeps no surface"


@pytest.mark.parametrize(("direction", "expected"), [(Vec3(0, -1, 0), "-Y"), (Vec3(0, 0, 2), "+Z"), (Vec3(0.6, 0.8, 0), "(0.6, 0.8, 0)")])
def test_an_axis_reads_as_its_signed_world_axis_or_its_components(direction: Vec3, expected: str) -> None:
    assert axis_word(direction) == expected
