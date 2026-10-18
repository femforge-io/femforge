"""Tagged cross-sections of tagged 3D solids: the quarter-wedge cutaway, and the axisymmetric and planar sections the 2D solves mesh."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, cast

import build123d as b3d

from femforge.common.vector import Vec3
from femforge.frames.base import SolvePlane
from femforge.geometry.tags import TaggedCompound, TaggedEdge, TaggedFace, TaggedPart


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


def _solve_location(plane: SolvePlane) -> b3d.Location:
    """The placement that carries authored shapes into the solve frame of ``plane``, the plane landing on Z = 0."""
    x_dir: b3d.Vector = b3d.Vector(*plane.x_dir.to_tuple())
    y_dir: b3d.Vector = b3d.Vector(*plane.y_dir.to_tuple())
    solve_frame = b3d.Plane(origin=b3d.Vector(*plane.origin.to_tuple()), x_dir=x_dir, z_dir=x_dir.cross(y_dir))

    return solve_frame.location.inverse()


def _sheet(parts: list[TaggedPart], *, half: bool, margin: float) -> b3d.Sketch:
    """The Z = 0 rectangle reaching ``margin`` past every part, only its X >= 0 half when ``half``."""
    boxes: list[b3d.BoundBox] = [part.bounding_box() for part in parts]
    left: float = 0.0 if half else min(box.min.X for box in boxes) - margin
    right: float = max(max(box.max.X for box in boxes), left) + margin
    bottom: float = min(box.min.Y for box in boxes) - margin
    top: float = max(box.max.Y for box in boxes) + margin

    return cast("b3d.Sketch", b3d.Pos(left, bottom) * b3d.Rectangle(right - left, top - bottom, align=(b3d.Align.MIN, b3d.Align.MIN)))


def _section(compound: TaggedCompound, plane: SolvePlane, *, half: bool, margin: float, kind: str) -> list[TaggedFace | TaggedEdge]:
    """Every tagged solid of ``compound`` sectioned into faces and every marker face into named edges, in the solve frame of ``plane``; ``kind`` names the section in errors."""
    leaves = compound.placed_leaves(_solve_location(plane))
    parts: list[TaggedPart] = [leaf for leaf in leaves if isinstance(leaf, TaggedPart)]

    if not parts:
        raise ValueError(f"{kind}_cross_section needs at least one TaggedPart solid to section.")

    # A common with the sheet keeps coplanar boundary faces of half models, where a section would drop them.
    sheet: b3d.Sketch = _sheet(parts, half=half, margin=margin)
    reach: str = f"the half-plane through {plane.origin} along {plane.y_dir}" if half else f"the plane through {plane.origin} normal to {plane.normal}"

    def common(shape: TaggedPart | TaggedFace) -> b3d.Compound | None:
        intersect = cast("Callable[[b3d.Sketch], b3d.Compound | None]", shape.intersect)

        return intersect(sheet)

    sections: list[TaggedFace | TaggedEdge] = []
    for part in parts:
        section: b3d.Compound | None = common(part)
        section_faces: list[b3d.Face] = list(section.faces()) if section is not None else []

        if not section_faces:
            raise ValueError(f"the {kind} section missed the solid tagged {part.tags_str()!r}: it does not reach {reach}.")

        for face in section_faces:
            tagged = TaggedFace(face, tags=part.current_tags)
            tagged.color = part.color
            sections.append(tagged)

    markers: list[TaggedFace] = [leaf for leaf in leaves if isinstance(leaf, TaggedFace)]
    for marker in markers:
        crossing: b3d.Compound | None = common(marker)

        if crossing is not None and crossing.faces():
            raise ValueError(f"the named face {marker.tags_str()!r} lies in the section plane, so it sections to no edge; name a face that crosses the plane.")

        crossing_edges: list[b3d.Edge] = list(crossing.edges()) if crossing is not None else []

        if not crossing_edges:
            raise ValueError(f"the named face {marker.tags_str()!r} does not cross {reach}, so it names no edge of the section.")

        for edge in crossing_edges:
            tagged_edge = TaggedEdge(edge, tags=marker.current_tags)
            tagged_edge.color = marker.color
            sections.append(tagged_edge)

    return sections


def axisymmetric_cross_section(compound: TaggedCompound, plane: SolvePlane, margin: float = 1.0) -> TaggedCompound:
    """Section every tagged solid on the X >= 0 half of ``plane`` into the solve frame (X = radial >= 0, Y = along the axis, Z = 0), and every marker face into a named edge; a missed solid raises.

    Args:
        compound: The tagged assembly to section.
        plane: The solve plane, from [`Axisymmetric.plane`][femforge.frames.Axisymmetric.plane].
        margin: How far the half-plane reaches past the parts.
    """
    sections: list[TaggedFace | TaggedEdge] = _section(
        compound,
        plane,
        half=True,
        margin=margin,
        kind="axisymmetric",
    )

    for leaf in sections:
        box: b3d.BoundBox = leaf.bounding_box()

        if box.min.X < -_TOLERANCE:
            raise ValueError(f"the section tagged {leaf.tags_str()!r} reaches X = {box.min.X:g} < 0, which Elmer's axisymmetric solve rejects silently.")

    result: TaggedCompound = TaggedCompound(children=sections)
    result.label = f"Cross Section of {compound.label}"

    return result


def planar_cross_section(compound: TaggedCompound, plane: SolvePlane, margin: float = 1.0) -> TaggedCompound:
    """Section every tagged solid on ``plane`` into the solve frame (X = ``plane.x_dir``, Y = ``plane.y_dir``, Z = 0), and every marker face into a named edge; a missed solid raises.

    Args:
        compound: The tagged assembly to section.
        plane: The solve plane, from [`Cartesian2D.plane`][femforge.frames.Cartesian2D.plane].
        margin: How far the plane reaches past the parts.
    """
    sections: list[TaggedFace | TaggedEdge] = _section(
        compound,
        plane,
        half=False,
        margin=margin,
        kind="planar",
    )
    result: TaggedCompound = TaggedCompound(children=sections)
    result.label = f"Planar Section of {compound.label}"

    return result


def export_section(section: TaggedCompound, step: Path) -> None:
    """Write ``section`` to the STEP ``step`` and its named edges to the edge file beside it, which the mesher reads back."""
    from femforge.meshing.section_edges import CurveSpan, SectionEdge, SectionEdges

    def span(edge: TaggedEdge) -> CurveSpan:
        start: b3d.Vector = edge.start_point()
        end: b3d.Vector = edge.end_point()

        return CurveSpan(start=Vec3(start.X, start.Y, start.Z), end=Vec3(end.X, end.Y, end.Z), length=edge.length)

    section.export_step(str(step))
    edges: list[TaggedEdge] = [leaf for leaf in section.placed_leaves() if isinstance(leaf, TaggedEdge)]
    SectionEdges(edges=[SectionEdge(name=edge.tags_str(), span=span(edge)) for edge in edges]).write(step)
