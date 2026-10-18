"""Tagged sections: axisymmetric_cross_section about any axis and cross_section_3D's quarter-wedge cutaway."""

from __future__ import annotations

import math
from typing import cast

import build123d as b3d
import pytest

from femforge.common.vector import Direction, Vec3
from femforge.frames import Axis, Axisymmetric, SolvePlane
from femforge.geometry.section import axisymmetric_cross_section, cross_section_3D
from femforge.geometry.tags import TaggedCompound, TaggedFace, TaggedPart


def _plane(axis: Axis = Axis.Z, radial: Direction | None = None) -> SolvePlane:
    return Axisymmetric(axis=axis, radial=radial).plane


def _y_axis_cylinder(outer_radius: float, length: float, inner_radius: float = 0.0, y0: float = 0.0) -> b3d.Part:
    """A (possibly hollow) cylinder with its axis on Y, spanning y in [y0, y0 + length]."""
    solid = b3d.Cylinder(radius=outer_radius, height=length)
    if inner_radius > 0.0:
        solid -= b3d.Cylinder(radius=inner_radius, height=length * 2)
    return cast("b3d.Part", b3d.Rot(X=-90) * b3d.Pos(0, 0, y0 + length / 2) * solid)


def _faces(section: TaggedCompound) -> list[TaggedFace]:
    children = cast("tuple[object, ...]", section.children)
    return [child for child in children if isinstance(child, TaggedFace)]


def _parts(section: TaggedCompound) -> list[TaggedPart]:
    children = cast("tuple[object, ...]", section.children)
    return [child for child in children if isinstance(child, TaggedPart)]


def _z0_faces(part: TaggedPart) -> list[b3d.Face]:
    return [face for face in part.faces() if abs(face.bounding_box().min.Z) < 1e-9 and abs(face.bounding_box().max.Z) < 1e-9]


def _scene() -> TaggedCompound:
    rod = TaggedPart(_y_axis_cylinder(2.0, 10.0), tags={"Material": "Steel316", "Type": "Rod"})
    rod.color = (0.3, 0.3, 0.3)
    ring = TaggedPart(_y_axis_cylinder(5.0, 4.0, inner_radius=3.0, y0=3.0), tags={"Material": "N42"})
    return TaggedCompound(children=[rod, ring])


def test_section_faces_lie_in_the_axisymmetric_frame() -> None:
    # X = radial (>= 0), Y = along the axis, Z = 0 — the frame the 2D solve requires.
    section = axisymmetric_cross_section(_scene(), _plane(Axis.Y))
    faces = _faces(section)
    assert len(faces) == 2
    for face in faces:
        bb = face.bounding_box()
        assert bb.min.X >= -1e-9
        assert abs(bb.min.Z) < 1e-9 and abs(bb.max.Z) < 1e-9


def test_section_areas_match_the_wall_profiles() -> None:
    # On-axis rod: radius x length; off-axis ring: wall thickness x length — the X >= 0 clamp halves the rod's slice.
    section = axisymmetric_cross_section(_scene(), _plane(Axis.Y))
    areas = sorted(face.area for face in _faces(section))
    assert areas[0] == pytest.approx(2.0 * 4.0)  # ring wall: (5 - 3) x 4
    assert areas[1] == pytest.approx(2.0 * 10.0)  # rod: 2 x 10


def test_section_copies_tags_and_colors() -> None:
    section = axisymmetric_cross_section(_scene(), _plane(Axis.Y))
    by_material = {face.get_tag("Material"): face for face in _faces(section)}
    assert set(by_material) == {"Steel316", "N42"}
    assert by_material["Steel316"].get_tag("Type") == "Rod"
    assert by_material["Steel316"].label == "Material=Steel316, Type=Rod"
    assert by_material["Steel316"].color is not None


def test_section_raises_when_a_solid_misses_the_plane() -> None:
    off_plane = TaggedPart(b3d.Pos(0, 0, 100) * b3d.Cylinder(radius=1.0, height=2.0), tags={"Material": "Air"})
    with pytest.raises(ValueError, match="section missed the solid tagged 'Material=Air'"):
        _ = axisymmetric_cross_section(TaggedCompound(children=[off_plane]), _plane(Axis.Y))


def test_section_requires_at_least_one_solid() -> None:
    with pytest.raises(ValueError, match="at least one TaggedPart"):
        _ = axisymmetric_cross_section(TaggedCompound(children=[]), _plane(Axis.Y))


def _z_axis_scene() -> TaggedCompound:
    """A rod on the Z axis and a ring entirely below Z = 0."""
    rod = TaggedPart(b3d.Cylinder(radius=2.0, height=10.0), tags={"Material": "Steel316"})
    ring = TaggedPart(b3d.Pos(0, 0, -8) * (b3d.Cylinder(radius=5.0, height=4.0) - b3d.Cylinder(radius=3.0, height=8.0)), tags={"Material": "N42"})
    return TaggedCompound(children=[rod, ring])


def _profiles(section: TaggedCompound) -> dict[str | None, tuple[float, float, float, float, float]]:
    """Each face's (area, min X, max X, min Y, max Y) by material."""
    profiles: dict[str | None, tuple[float, float, float, float, float]] = {}
    for face in _faces(section):
        box = face.bounding_box()
        assert abs(box.min.Z) < 1e-9 and abs(box.max.Z) < 1e-9
        profiles[face.get_tag("Material")] = (face.area, box.min.X, box.max.X, box.min.Y, box.max.Y)

    return profiles


def test_the_default_z_axis_maps_axial_position_onto_y_below_the_origin_too() -> None:
    profiles = _profiles(axisymmetric_cross_section(_z_axis_scene(), _plane()))
    assert profiles["Steel316"] == pytest.approx((20.0, 0.0, 2.0, -5.0, 5.0), abs=1e-9)
    assert profiles["N42"] == pytest.approx((8.0, 3.0, 5.0, -10.0, -6.0), abs=1e-9)


def test_an_offset_x_axis_sections_about_its_own_origin() -> None:
    # The Z scene turned onto X (Rot Y=90 sends +Z to +X) and moved off the world origin.
    origin = Vec3(4, -3, 7)
    placement = b3d.Pos(origin.x, origin.y, origin.z) * b3d.Rot(Y=90)
    leaves = [TaggedPart([*part.moved(placement).solids()], tags=part.current_tags) for part in _parts(_z_axis_scene())]
    section = axisymmetric_cross_section(TaggedCompound(children=leaves), _plane(Axis(origin=origin, direction=Direction(1, 0, 0))))
    profiles = _profiles(section)
    assert profiles["Steel316"] == pytest.approx((20.0, 0.0, 2.0, -5.0, 5.0), abs=1e-9)
    assert profiles["N42"] == pytest.approx((8.0, 3.0, 5.0, -10.0, -6.0), abs=1e-9)


def test_compound_locations_reach_their_leaves() -> None:
    # Rot * compound only relocates the compound; the section must still see the rod along X.
    nested = TaggedCompound(children=[TaggedCompound(children=[_parts(_z_axis_scene())[0]])])
    turned = cast("TaggedCompound", b3d.Rot(Y=90) * nested)
    profiles = _profiles(axisymmetric_cross_section(turned, _plane(Axis.X)))
    assert profiles["Steel316"] == pytest.approx((20.0, 0.0, 2.0, -5.0, 5.0), abs=1e-9)


def test_cutaway_removes_a_quarter_of_each_solid() -> None:
    cutaway = cross_section_3D(_scene())
    parts = _parts(cutaway)
    assert len(parts) == 2
    volumes = sorted(part.volume for part in parts)
    assert volumes == pytest.approx([0.75 * math.pi * 2.0**2 * 10.0, 0.75 * math.pi * (5.0**2 - 3.0**2) * 4.0])


def test_cutaway_exposes_the_section_wall_at_z0() -> None:
    # The wedge's Z = 0 wall is the axisymmetric section: same X >= 0 clamp, same wall profiles.
    cutaway = cross_section_3D(_scene())
    wall_faces = [face for part in _parts(cutaway) for face in _z0_faces(part)]
    assert sorted(face.area for face in wall_faces) == pytest.approx([2.0 * 4.0, 2.0 * 10.0])
    for face in wall_faces:
        assert face.bounding_box().min.X >= -1e-9


def test_cutaway_copies_tags_and_colors() -> None:
    cutaway = cross_section_3D(_scene())
    by_material = {part.get_tag("Material"): part for part in _parts(cutaway)}
    assert set(by_material) == {"Steel316", "N42"}
    assert by_material["Steel316"].get_tag("Type") == "Rod"
    assert by_material["Steel316"].label == "Material=Steel316, Type=Rod"
    assert by_material["Steel316"].color is not None


def test_cutaway_raises_when_the_wedge_consumes_a_solid() -> None:
    inside_wedge = TaggedPart(b3d.Pos(5, 0, 5) * b3d.Cylinder(radius=1.0, height=2.0), tags={"Material": "Air"})
    with pytest.raises(ValueError, match="consumed the solid tagged 'Material=Air' whole"):
        _ = cross_section_3D(TaggedCompound(children=[inside_wedge]))


def test_cutaway_with_a_custom_plane_takes_a_half_section() -> None:
    half = cross_section_3D(_scene(), planes=(b3d.Plane.XY,))
    volumes = sorted(part.volume for part in _parts(half))
    assert volumes == pytest.approx([0.5 * math.pi * 2.0**2 * 10.0, 0.5 * math.pi * (5.0**2 - 3.0**2) * 4.0])


def test_cutaway_requires_at_least_one_plane() -> None:
    with pytest.raises(ValueError, match="at least one plane"):
        _ = cross_section_3D(_scene(), planes=())


def test_cutaway_raises_when_the_planes_share_no_volume() -> None:
    above = b3d.Plane((0, 0, 1))
    below = b3d.Plane((0, 0, -1), z_dir=(0, 0, -1))
    with pytest.raises(ValueError, match="share no volume"):
        _ = cross_section_3D(_scene(), planes=(above, below))


def _rod_section(rod: b3d.Part, axis: Axis) -> tuple[float, float, float]:
    """The single rod face's (area, min X, max X) after sectioning about ``axis``."""
    faces = _faces(axisymmetric_cross_section(TaggedCompound(children=[TaggedPart(rod, tags={"Material": "Steel316"})]), _plane(axis)))
    assert len(faces) == 1
    box = faces[0].bounding_box()
    return faces[0].area, box.min.X, box.max.X


@pytest.mark.parametrize(
    "keep",
    [
        pytest.param(b3d.Pos(0, 5, 0) * b3d.Box(10, 10, 20), id="half-y-positive"),
        pytest.param(b3d.Pos(0, -5, 0) * b3d.Box(10, 10, 20), id="half-y-negative"),
        pytest.param(b3d.Pos(5, 0, 0) * b3d.Box(10, 10, 20), id="half-x-positive"),
        pytest.param(b3d.Pos(5, 5, 0) * b3d.Box(10, 10, 20), id="quarter-x-y-positive"),
        pytest.param(b3d.Pos(5, -5, 0) * b3d.Box(10, 10, 20), id="quarter-x-positive-y-negative"),
    ],
)
def test_half_and_quarter_models_section_into_x_at_least_zero(keep: b3d.Part) -> None:
    rod = cast("b3d.Part", b3d.Cylinder(radius=2.0, height=10.0) & keep)
    assert _rod_section(rod, Axis.Z) == pytest.approx((20.0, 0.0, 2.0), abs=1e-9)


def test_a_model_off_the_half_plane_is_missed() -> None:
    quarter = cast("b3d.Part", b3d.Cylinder(radius=2.0, height=10.0) & (b3d.Pos(-5, 5, 0) * b3d.Box(10, 10, 20)))
    with pytest.raises(ValueError, match="section missed"):
        _ = _rod_section(quarter, Axis.Z)


@pytest.mark.parametrize(
    ("direction", "origin"),
    [
        pytest.param(Direction(0, 0, -1), Vec3(0, 0, 0), id="minus-z"),
        pytest.param(Direction(2, 4, 6), Vec3(1, -2, 3), id="oblique-offset"),
        pytest.param(Direction(-1, -2, -3), Vec3(1, -2, 3), id="oblique-antiparallel"),
        pytest.param(Direction(1, 1, 1), Vec3(0, 0, 0), id="diagonal"),
    ],
)
def test_any_axis_direction_sections_the_wall_profiles_at_x_at_least_zero(direction: Direction, origin: Vec3) -> None:
    unit = b3d.Vector(direction.x, direction.y, direction.z).normalized()
    placement = b3d.Location(b3d.Plane(origin=(origin.x, origin.y, origin.z), z_dir=unit))
    leaves = [TaggedPart([*part.moved(placement).solids()], tags=part.current_tags) for part in _parts(_z_axis_scene())]
    profiles = _profiles(axisymmetric_cross_section(TaggedCompound(children=leaves), _plane(Axis(origin=origin, direction=direction))))
    rod_area, rod_min_x, rod_max_x, rod_min_y, rod_max_y = profiles["Steel316"]
    ring_area, ring_min_x, ring_max_x, ring_min_y, ring_max_y = profiles["N42"]
    assert (rod_area, rod_min_x, rod_max_x, rod_max_y - rod_min_y) == pytest.approx((20.0, 0.0, 2.0, 10.0), abs=1e-9)
    assert (ring_area, ring_min_x, ring_max_x, ring_max_y - ring_min_y) == pytest.approx((8.0, 3.0, 5.0, 4.0), abs=1e-9)
    # The ring sits 6..10 from the rod's centre, on whichever side the axis direction points away from.
    assert abs((ring_min_y + ring_max_y) / 2) == pytest.approx(8.0, abs=1e-9)


def test_a_section_leaking_below_x_zero_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    rectangle = b3d.Rectangle

    def centred(width: float, height: float, align: object = None) -> b3d.Rectangle:
        _ = align
        return rectangle(width, height)

    monkeypatch.setattr(b3d, "Rectangle", centred)
    with pytest.raises(ValueError, match="reaches X = .* < 0"):
        _ = _rod_section(cast("b3d.Part", b3d.Cylinder(radius=2.0, height=10.0)), Axis.Z)


def test_cutaway_requires_at_least_one_solid() -> None:
    with pytest.raises(ValueError, match="at least one TaggedPart"):
        _ = cross_section_3D(TaggedCompound(children=[]))


def test_a_radial_direction_picks_the_half_plane_the_default_misses() -> None:
    # A half model drawn on +Z of an X axis: the default half-plane faces +Y and misses it.
    half_model = TaggedCompound(children=[TaggedPart(b3d.Pos(0, 0, 2) * b3d.Box(4, 1, 2), tags={"Material": "Steel316"})])

    with pytest.raises(ValueError, match="missed the solid"):
        _ = axisymmetric_cross_section(half_model, _plane(Axis.X))

    section = axisymmetric_cross_section(half_model, _plane(Axis.X, Direction(0, 0, 1)))
    box = section.bounding_box()
    assert section.area == pytest.approx(8.0)
    assert (box.min.X, box.max.X) == pytest.approx((1.0, 3.0))
