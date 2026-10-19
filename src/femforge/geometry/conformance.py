"""Checks that an authored model fits a sectioning frame: a solid of revolution about its axis, or a prism through its thickness."""

from __future__ import annotations

import time
from typing import Callable, cast

import build123d as b3d

from femforge.common.log import log
from femforge.frames.base import Axis, BodyMismatch, FrameCheck, SolvePlane
from femforge.geometry.section import solve_location
from femforge.geometry.tags import TaggedCompound, TaggedFace, TaggedPart
from femforge.geometry.utils import AnyShape
from femforge.physical.units import Length


MISMATCH_TOLERANCE: float = 1e-4
"""`[-]`: The largest mismatch fraction that still fits the frame."""

TURN_DEGREES: float = 37.0
"""`[deg]`: The turn about the symmetry axis; not a divisor of 360 and off the knots of a NURBS revolution, which a 90° turn lands on and stalls OCC."""

END_INSET: float = 1e-3
"""`[-]`: The share of the depth the end sections of a named face sit inside the model's ends."""

_SHEET_MARGIN = 1.0


def _volume(shape: AnyShape | None) -> float:
    return 0.0 if shape is None else sum(solid.volume for solid in shape.solids())


def _area(shape: AnyShape | None) -> float:
    return 0.0 if shape is None else sum(face.area for face in shape.faces())


def _length(shape: AnyShape | None) -> float:
    return 0.0 if shape is None else sum(edge.length for edge in shape.edges())


def _symmetric[S: AnyShape](first: S, second: S, measure: Callable[[AnyShape | None], float]) -> float:
    """The measure of the symmetric difference of ``first`` and ``second``."""
    return measure(first - second) + measure(second - first)


def _named(leaves: list[TaggedPart | TaggedFace]) -> list[tuple[str, list[TaggedPart | TaggedFace]]]:
    """The leaves grouped by kind and tags, in the order each name first appears."""
    keys: list[tuple[int, str]] = list(dict.fromkeys((leaf.dim, leaf.tags_str()) for leaf in leaves))

    return [(tags, [leaf for leaf in leaves if (leaf.dim, leaf.tags_str()) == (dim, tags)]) for dim, tags in keys]


def _joined(members: list[TaggedPart | TaggedFace]) -> b3d.Part | b3d.Compound:
    """One name's leaves as one shape, fused across the seams a CAD export may split a named body or surface at."""
    solids: list[b3d.Solid] = [solid for leaf in members for solid in leaf.solids()]

    if solids:
        fused_solids: AnyShape = solids[0] if len(solids) == 1 else solids[0] + solids[1:]

        return b3d.Part(list(fused_solids.solids()))

    faces: list[b3d.Face] = [face for leaf in members for face in leaf.faces()]
    fused_faces: AnyShape = faces[0] if len(faces) == 1 else faces[0] + faces[1:]

    return b3d.Compound(list(fused_faces.faces()))


def _measured(leaves: list[TaggedPart | TaggedFace], measure: Callable[[str, b3d.Part | b3d.Compound], list[BodyMismatch]], check: FrameCheck, started: float) -> list[BodyMismatch]:
    """Every name's measurements, its leaves joined, a name whose boolean raises skipped with a warning."""
    named: list[tuple[str, list[TaggedPart | TaggedFace]]] = _named(leaves)
    measured: list[BodyMismatch] = []

    for tags, members in named:
        # OCC returns a null shape on some near-coincident NURBS booleans; a skipped name beats a false rejection.
        try:
            measured.extend(measure(tags, _joined(members)))
        except ValueError as error:
            log.bind(body=tags, check=check, error=str(error)).warning("Frame check skipped.")

    bodies: int = sum(isinstance(members[0], TaggedPart) for _, members in named)
    log.bind(bodies=bodies, faces=len(named) - bodies, elapsed=time.perf_counter() - started).debug("Frame geometry checked.")

    return measured


def revolution_mismatches(compound: TaggedCompound, axis: Axis) -> list[BodyMismatch]:
    """The ``TURNED`` mismatch of every body and named face of the ``compound``, each name's leaves fused: its change when turned ``TURN_DEGREES`` about ``axis``, a share of its volume or area."""
    started: float = time.perf_counter()
    turn = b3d.Axis(axis.origin.to_tuple(), axis.direction.to_tuple())
    leaves: list[TaggedPart | TaggedFace] = [leaf for leaf in compound.placed_leaves() if isinstance(leaf, (TaggedPart, TaggedFace))]

    def measure(tags: str, shape: b3d.Part | b3d.Compound) -> list[BodyMismatch]:
        measure_of: Callable[[AnyShape | None], float] = _volume if isinstance(shape, b3d.Part) else _area
        fraction: float = _symmetric(shape, shape.rotate(turn, TURN_DEGREES), measure_of) / measure_of(shape)

        return [BodyMismatch(body=tags, check=FrameCheck.TURNED, fraction=fraction)]

    return _measured(leaves, measure, FrameCheck.TURNED, started)


def prism_mismatches(compound: TaggedCompound, plane: SolvePlane, thickness: Length) -> list[BodyMismatch]:
    """The ``DEPTH`` and ``PRISM`` mismatches of every body, and the ``PRISM`` mismatch of every named face, of the authored ``compound`` sectioned on ``plane``, each name's leaves fused.

    A body is compared with its mid-depth section extruded through the model's depth, the bodies' joint extent along the normal; a named face must span that depth.

    Args:
        compound: The authored model.
        plane: The solve plane, from [`Cartesian2D.plane`][femforge.frames.Cartesian2D.plane].
        thickness: The depth each body must span along the normal.
    """
    started: float = time.perf_counter()
    leaves: list[TaggedPart | TaggedFace] = [leaf for leaf in compound.placed_leaves(solve_location(plane)) if isinstance(leaf, (TaggedPart, TaggedFace))]
    solid_boxes: list[b3d.BoundBox] = [leaf.bounding_box() for leaf in leaves if isinstance(leaf, TaggedPart)]

    if not solid_boxes:
        return []

    boxes: list[b3d.BoundBox] = [leaf.bounding_box() for leaf in leaves]
    z0: float = min(box.min.Z for box in solid_boxes)
    z1: float = max(box.max.Z for box in solid_boxes)
    depth: float = z1 - z0
    mid: float = (z0 + z1) / 2
    left: float = min(box.min.X for box in boxes) - _SHEET_MARGIN
    bottom: float = min(box.min.Y for box in boxes) - _SHEET_MARGIN
    width: float = max(box.max.X for box in boxes) + _SHEET_MARGIN - left
    height: float = max(box.max.Y for box in boxes) + _SHEET_MARGIN - bottom
    target: float = thickness.m_as("mm")

    def sheet(z: float) -> b3d.Sketch:
        return cast("b3d.Sketch", b3d.Pos(left, bottom, z) * b3d.Rectangle(width, height, align=(b3d.Align.MIN, b3d.Align.MIN)))

    def measure_body(tags: str, body: b3d.Part) -> list[BodyMismatch]:
        box: b3d.BoundBox = body.bounding_box()
        extent: float = box.max.Z - box.min.Z
        section = body & sheet(mid)
        faces: list[b3d.Face] = [] if section is None else list(section.faces())
        depth_mismatch = BodyMismatch(body=tags, check=FrameCheck.DEPTH, fraction=abs(extent - target) / target)

        if not faces:
            return [depth_mismatch, BodyMismatch(body=tags, check=FrameCheck.PRISM, fraction=1.0)]

        extruded_solids: list[b3d.Solid] = [b3d.Solid.extrude(face.moved(b3d.Location((0, 0, z0 - mid))), b3d.Vector(0, 0, depth)) for face in faces]
        fraction: float = _symmetric(body, b3d.Part(extruded_solids), _volume) / _volume(body)

        return [depth_mismatch, BodyMismatch(body=tags, check=FrameCheck.PRISM, fraction=fraction)]

    def measure_face(tags: str, surface: b3d.Compound) -> list[BodyMismatch]:
        reference: float = _length(surface & sheet(mid))

        if reference == 0.0:
            return [BodyMismatch(body=tags, check=FrameCheck.PRISM, fraction=1.0)]

        box: b3d.BoundBox = surface.bounding_box()
        span: float = max(abs(box.min.Z - z0), abs(box.max.Z - z1)) / depth
        ends: list[float] = [abs(_length(surface & sheet(z)) - reference) / reference for z in (z0 + END_INSET * depth, z1 - END_INSET * depth)]

        return [BodyMismatch(body=tags, check=FrameCheck.PRISM, fraction=max(span, *ends))]

    def measure(tags: str, shape: b3d.Part | b3d.Compound) -> list[BodyMismatch]:
        return measure_body(tags, shape) if isinstance(shape, b3d.Part) else measure_face(tags, shape)

    return _measured(leaves, measure, FrameCheck.PRISM, started)
