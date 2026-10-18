"""Tagged cross-sections of tagged 3D solids: the quarter-wedge cutaway and the axisymmetric r–z section the 2D solve meshes."""

from __future__ import annotations

from typing import Callable, Iterable, cast

import build123d as b3d

from femforge.frames.base import SolvePlane
from femforge.geometry.tags import TaggedCompound, TaggedFace, TaggedPart


_TOLERANCE = 1e-6


def cross_section_3D(
    compound: TaggedCompound,
    planes: Iterable[b3d.Plane] = (b3d.Plane.YZ, b3d.Plane.XY),
    margin: float = 1.0,
) -> TaggedCompound:
    """Cut away the material on the positive side of every plane, copying each solid's tags and color; raises when the planes share no volume or the wedge consumes a solid whole.

    Args:
        compound: The tagged assembly to cut.
        planes: The cutting planes; the default cuts away the X >= 0, Z >= 0 quarter wedge.
        margin: How far the cutting boxes reach past the parts.
    """

    def half_space_box(plane: b3d.Plane, center: b3d.Vector, radius: float, margin: float) -> b3d.Part:
        """A box covering the ball of `radius` around `center` on the positive side of `plane`, its wall exactly on the plane."""
        offset: b3d.Vector = center - plane.origin
        reach: float = max(offset.dot(plane.z_dir) + radius, margin)
        box = b3d.Box(2 * radius, 2 * radius, reach, align=(b3d.Align.CENTER, b3d.Align.CENTER, b3d.Align.MIN))
        return plane.location * b3d.Pos(offset.dot(plane.x_dir), offset.dot(plane.y_dir)) * box

    parts: list[TaggedPart] = [leaf for leaf in compound.placed_leaves() if isinstance(leaf, TaggedPart)]
    if not parts:
        raise ValueError("cross_section_3D needs at least one TaggedPart solid to section.")

    plane_list: list[b3d.Plane] = list(planes)
    if not plane_list:
        raise ValueError("cross_section_3D needs at least one plane to cut with.")

    # Bounds come from the parts, not compound.bounding_box() — a Compound's box can silently omit children.
    boxes: list[b3d.BoundBox] = [part.bounding_box() for part in parts]
    minimum = b3d.Vector(min(box.min.X for box in boxes), min(box.min.Y for box in boxes), min(box.min.Z for box in boxes))
    maximum = b3d.Vector(max(box.max.X for box in boxes), max(box.max.Y for box in boxes), max(box.max.Z for box in boxes))
    center: b3d.Vector = (minimum + maximum) / 2
    radius: float = (maximum - minimum).length / 2 + margin

    half_spaces: list[b3d.Part] = [half_space_box(plane, center, radius, margin) for plane in plane_list]
    wedge: b3d.Part = half_spaces[0]
    for half_space in half_spaces[1:]:
        intersect = cast("Callable[[b3d.Part], b3d.Part | None]", wedge.intersect)
        overlap: b3d.Part | None = intersect(half_space)
        if overlap is None or not overlap.solids():
            raise ValueError("the planes' positive half-spaces share no volume — there is no wedge to cut away.")

        wedge = b3d.Part(overlap.solids())

    sections: list[TaggedPart] = []
    for part in parts:
        cut = cast("Callable[[b3d.Part], b3d.Part]", part.cut)
        section: b3d.Part = cut(wedge)
        if not section.solids():
            raise ValueError(f"the sectioning wedge consumed the solid tagged {part.tags_str()!r} whole.")

        # A list, not section.wrapped: wrapping a bare TopoDS_Solid where a TopoDS_Compound belongs reads volume 0.
        tagged = TaggedPart([section], tags=part.current_tags)
        tagged.color = part.color
        sections.append(tagged)

    result: TaggedCompound = TaggedCompound(children=sections)
    result.label = f"3D Cross Section of {compound.label}"
    return result


def axisymmetric_cross_section(compound: TaggedCompound, plane: SolvePlane, margin: float = 1.0) -> TaggedCompound:
    """Section every tagged solid on the X >= 0 half of ``plane`` into the solve frame (X = radial >= 0, Y = along the axis, Z = 0); a missed solid raises.

    Args:
        compound: The tagged assembly to section.
        plane: The solve plane, from [`Axisymmetric.plane`][femforge.frames.Axisymmetric.plane].
        margin: How far the half-plane reaches past the parts.
    """
    toward: b3d.Vector = b3d.Vector(*plane.radial.to_tuple())
    direction: b3d.Vector = b3d.Vector(*plane.axis.to_tuple())
    solve_frame = b3d.Plane(origin=b3d.Vector(*plane.origin.to_tuple()), x_dir=toward, z_dir=toward.cross(direction))

    leaves = compound.placed_leaves(solve_frame.location.inverse())
    parts: list[TaggedPart] = [leaf for leaf in leaves if isinstance(leaf, TaggedPart)]
    if not parts:
        raise ValueError("axisymmetric_cross_section needs at least one TaggedPart solid to section.")

    # The X >= 0 half of the Z = 0 plane, past every part; a common with it keeps coplanar boundary faces of half models.
    boxes: list[b3d.BoundBox] = [part.bounding_box() for part in parts]
    reach: float = max(max(box.max.X for box in boxes), 0.0) + margin
    bottom: float = min(box.min.Y for box in boxes) - margin
    top: float = max(box.max.Y for box in boxes) + margin
    half_plane = b3d.Pos(0, bottom) * b3d.Rectangle(reach, top - bottom, align=(b3d.Align.MIN, b3d.Align.MIN))

    faces: list[TaggedFace] = []
    for part in parts:
        common = cast("Callable[[b3d.Sketch], b3d.Compound | None]", part.intersect)
        section = common(half_plane)
        section_faces: list[b3d.Face] = list(section.faces()) if section is not None else []
        if not section_faces:
            raise ValueError(f"the axisymmetric section missed the solid tagged {part.tags_str()!r}: it does not reach the half-plane through {plane.origin} along {plane.axis}.")

        for face in section_faces:
            box: b3d.BoundBox = face.bounding_box()
            if box.min.X < -_TOLERANCE:
                raise ValueError(f"the section of the solid tagged {part.tags_str()!r} reaches X = {box.min.X:g} < 0, which Elmer's axisymmetric solve rejects silently.")

            tagged = TaggedFace(face, tags=part.current_tags)
            tagged.color = part.color
            faces.append(tagged)

    result: TaggedCompound = TaggedCompound(children=faces)
    result.label = f"Cross Section of {compound.label}"
    return result
