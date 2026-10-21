"""Scene glyphs of what a run's groups received: load arrows, held-face tints, magnet arrows, current markers and value labels, plus the Setup tab's selection, section outline and authored ghost."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol, cast

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from femforge.common.log import log
from femforge.common.utils import title
from femforge.common.vector import Vec3, Vec3Field
from femforge.frames import Frame, default_frame
from femforge.physical.conditions import (
    CalculateMagneticForce,
    CoilDrive,
    CoilTerminal,
    ConditionGlyph,
    ConditionUnion,
    Convection,
    CurrentDensity,
    FixedDisplacement,
    FixedTemperature,
    FixedVoltage,
    HeatFlux,
    HeatSource,
    JouleHeat,
    Magnetization,
    SurfaceForce,
)
from femforge.physical.readout import readout
from femforge.physical.units import Quantity, U, Unit, quantity
from femforge.viz.field_display import FLAT_RATIO, FULL_SWEEP, BodyPaint, BodyStyle, DisplaySpec, SceneActor, feature_outline, run_manifest, warped
from femforge.viz.field_lines import Bounds
from femforge.viz.symmetry import Basis, Expansion, Surface, swept_lines


if TYPE_CHECKING:
    import pyvista as pv
    from vtkmodules.vtkRenderingCore import vtkActor2D

    from femforge.run.conditions import ConditionRecord, ConditionsFile
    from femforge.run.entities import Bodies, Body, Boundaries, Boundary
    from femforge.run.inspect import Run
    from femforge.run.mesh_groups import MeshGroups
    from femforge.viz.field_display import FieldScene


__all__ = [
    "GlyphLabels",
    "GlyphActor",
    "GLYPH_ACTORS",
    "EntityKind",
    "EntityRef",
    "Glyph",
    "GlyphView",
    "GlyphPlacement",
    "GlyphHost",
    "GlyphDraw",
    "GlyphLayer",
    "GroupScene",
    "glyph_label",
    "glyphs_of",
    "glyph_actors",
    "arrow_span",
    "revolve_angles",
    "shown_diagonal",
    "mirrored",
    "clamp_ticks",
    "spread",
    "densified",
    "dashed",
    "circles",
    "plain_spec",
]

type FloatArray = np.typing.NDArray[np.float64]
type IntArray = np.typing.NDArray[np.int64]

ARROW_SHARE: float = 0.25
"""`[-]`: The longest load arrow, as a share of the scene diagonal."""

ARROW_FLOOR: float = 0.06
"""`[-]`: The shortest load arrow, as a share of the scene diagonal."""

TICK_SPACING: float = 0.02
"""`[-]`: The distance between clamp ticks along a held 2D face, as a share of the scene diagonal."""

TICK_LENGTH: float = 0.012
"""`[-]`: The length of a clamp tick, as a share of the scene diagonal."""

PIN_COUNT: int = 16
"""About how many support pins a held 3D face draws, or roller balls a held 2D face."""

PIN_LENGTH: float = 0.03
"""`[-]`: The length of a support pin, as a share of the scene diagonal."""

ROLLER_RADIUS: float = 0.008
"""`[-]`: The radius of a roller, as a share of the scene diagonal."""

MARKER_RADIUS: float = 0.02
"""`[-]`: The radius of an out-of-plane current marker, as a share of the scene diagonal."""

LEADER_SHARE: float = 0.08
"""`[-]`: How far a label chip sits off its face along the outward normal, as a share of the scene diagonal."""

TINT_LIFT: float = 1e-3
"""`[-]`: How far a tint sits off its face toward the viewer, as a share of the scene diagonal, so it never fights the body for depth."""

TINT_OPACITY: float = 0.6
"""`[-]`: The opacity of a face tint."""

SELECTION_OPACITY: float = 0.7
"""`[-]`: The opacity of a selected boundary's accent cells."""

SELECTION_TINT: float = 0.1
"""`[-]`: The opacity of a selected body's accent fill."""

GHOST_OPACITY: float = 0.15
"""`[-]`: The opacity of the authored-body ghost."""

PLANE_OPACITY: float = 0.1
"""`[-]`: The opacity of the section plane drawn under a revolved display."""

REVOLVE_STEP: float = 45.0
"""`[deg]`: The turn between the repeated magnet arrows of a revolved display."""

REVOLVE_COPIES: int = 8
"""The most magnet arrows a revolved display repeats."""

HULL_SEGMENTS: int = 24
"""The steps per turn of the revolved outline an axisymmetric ghost is checked against."""

MAGNET_SHARE: float = 0.5
"""`[-]`: The length of a magnet arrow, as a share of the smallest extent of its body."""

TURN_STEP: float = 0.25
"""`[deg]`: The step a mark lattice turns through to find a flat face's edges."""

HALO_WIDTH: float = 0.012
"""`[-]`: How far the dark halo behind an arrow stands off its surface, as a share of the arrow length."""

TRIAD_SHARE: float = 0.06
"""`[-]`: The length of a solve triad arrow, as a share of the scene diagonal."""

DASH_SHARE: float = 0.01
"""`[-]`: The length of a dash of the prism hull, as a share of the scene diagonal."""

LABEL_FONT_PX: float = 14.0
"""`[px]`: The label font size in CSS pixels, scaled by the window pixels per CSS pixel."""

TINT_LINE_PX: float = 4.0
"""`[px]`: The width of a 2D face tint in CSS pixels."""

MARK_LINE_PX: float = 2.0
"""`[px]`: The width of a tick, pin, roller, marker or leader in CSS pixels."""

SELECTION_LINE_PX: float = 4.0
"""`[px]`: The width of a selected 2D boundary in CSS pixels."""

SILHOUETTE_LINE_PX: float = 3.0
"""`[px]`: The width of a selected body's silhouette in CSS pixels."""

HOVER_LINE_PX: float = 2.0
"""`[px]`: The width of a hovered entity's outline in CSS pixels."""

HOVER_OPACITY: float = 0.4
"""`[-]`: The opacity of a hovered boundary of a revolved section, which shows as a swept surface."""

OUTLINE_LINE_PX: float = 2.0
"""`[px]`: The width of the section outline and the solve triad in CSS pixels."""

LABEL_OPACITY: float = 0.8
"""`[-]`: The opacity of a label chip's dark box."""

CIRCLE_SIDES: int = 24
"""The sides of the polygon a roller or a current marker is drawn as."""

GLYPH_FILL = "#e6e6e6"
"""The neutral fill of arrows and label text."""

GLYPH_HALO = "#1a1a1a"
"""The dark halo of arrows, label boxes, marks and outlines."""

GLYPH_TINT = "#bfbfbf"
"""The neutral tint of a held, thermal or electrode face, pre-blended so a 2D line tint can be opaque."""

GLYPH_ACCENT = "#3b82f6"
"""The selection accent a view without one of its own takes."""

PLAIN_BODY = "#8c8c8c"
"""The grey every body takes under Plain Bodies."""

PLAIN_SPECULAR: float = 0.3
"""`[-]`: The specular strength of a plain body, so its faces read apart under the light."""

GLYPH_PREFIX = "glyph-"
"""The prefix every glyph layer actor name starts with."""

LABELS_SUFFIX = "-labels"
"""What pyvista appends to the name of a point-label actor."""

MIRROR: FloatArray = np.array([-1.0, 1.0, 1.0])
"""The reflection across the axis a mirrored display adds."""


class GlyphLabels(StrEnum):
    """How glyph values are labelled in the scene."""

    SCENE = "scene"
    """Point labels in the scene, the only text actor both renderers draw crisply."""

    NONE = "none"
    """No labels; the shapes alone."""


class GlyphActor(StrEnum):
    """The plotter actor names the glyph layer owns, so a redraw replaces or removes exactly these."""

    ARROWS = "glyph-arrows"
    """Load, magnet and displacement arrows and the filled marks."""

    HALOS = "glyph-halos"
    """The dark hulls behind the arrows."""

    FACES = "glyph-faces"
    """Translucent tints of 3D or revolved faces."""

    LINES = "glyph-lines"
    """Opaque tints of 2D faces."""

    MARKS = "glyph-marks"
    """Clamp ticks, support pins, rollers and current markers."""

    LEADERS = "glyph-leaders"
    """The lines from a face to its label chip."""

    LABELS = "glyph-labels"
    """Value and triad labels."""

    SELECTION = "glyph-selection"
    """The accent silhouette or line of the selected entity."""

    SELECTION_FILL = "glyph-selection-fill"
    """The accent fill of the selected entity."""

    OUTLINE = "glyph-outline"
    """The boundary loops of a 2D section."""

    PLANE = "glyph-plane"
    """The section plane under a revolved display."""

    TRIAD = "glyph-triad"
    """The solve axes of a 2D section."""

    GHOST = "glyph-ghost"
    """The translucent authored bodies a 2D frame sectioned."""

    HULL = "glyph-hull"
    """The ghost's silhouette and the dashed prism or revolved outline it is checked against."""

    HOVER = "glyph-hover:"
    """The prefix of each entity's hidden hover outline, which the browser shows while the entity's row is hovered."""


GLYPH_ACTORS: tuple[GlyphActor, ...] = (
    GlyphActor.ARROWS,
    GlyphActor.HALOS,
    GlyphActor.FACES,
    GlyphActor.LINES,
    GlyphActor.MARKS,
    GlyphActor.LEADERS,
    GlyphActor.LABELS,
)
"""The actors that draw conditions, at most one merged actor each."""


class EntityKind(StrEnum):
    """Whether an entity row names a body group or a boundary group."""

    BODY = "body"
    """A body group."""

    BOUNDARY = "boundary"
    """A boundary group."""


@dataclass(frozen=True)
class EntityRef:
    """A body or boundary group by kind and name, so a body and a named face that share a name stay apart."""

    kind: EntityKind
    """Whether the group is a body or a boundary."""

    name: str
    """The group name, shared by the mesh and the sif."""

    @property
    def id(self) -> str:
        """The ref as one string, such as ``boundary:RIGHTFACE``."""
        return f"{self.kind}:{self.name}"

    @classmethod
    def parse(cls, value: str) -> EntityRef | None:
        """The ref an [`id`][femforge.viz.glyphs.EntityRef.id] names, None for anything else."""
        kind, separator, name = value.partition(":")

        if not separator or not name or kind not in EntityKind:
            return None

        return cls(EntityKind(kind), name)


class Glyph(BaseModel):
    """One condition as the scene draws it, in the solve frame the run was meshed in."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    group: str
    """The group the condition landed on."""

    target: EntityKind
    """Whether the group is a body or a boundary."""

    kind: ConditionGlyph
    """The shape the condition draws as."""

    anchor: Vec3Field
    """`[m]`: Where the glyph sits: the group's centroid."""

    normal: Vec3Field | None = None
    """`[-]`: The outward normal of a boundary; None for a body or an ill-defined normal."""

    vector: Vec3Field | None = None
    """`[-]`: The unit direction of the condition vector the solver received; None for a condition without one."""

    share: float = 1.0
    """`[-]`: The load's size against the largest load of its kind, which sets an arrow's length."""

    label: str | None = None
    """The value label; None draws no label."""

    held: tuple[bool, ...] = ()
    """Whether each solve coordinate is held, for a fixed displacement."""

    extent: float | None = None
    """`L` `[m]`: The arrow length of a magnet."""

    reach: float = 0.0
    """`r` `[m]`: How far a magnet reaches from its centroid along its direction, where a 3D display starts its arrow so the body does not hide it."""

    @property
    def ref(self) -> EntityRef:
        """The group this glyph draws on."""
        return EntityRef(self.target, self.group)


def _bare(value: Quantity, unit: Unit) -> str:
    """``value`` as a readout in ``unit`` without the unit."""
    return readout(value, unit=unit).rpartition(" ")[0]


def glyph_label(condition: ConditionUnion, *, revolved: bool, dropped: Sequence[str] = ()) -> str | None:
    """The short scene label of ``condition``, magnitudes only since the shape shows the direction; None for a condition the shape says enough about.

    Args:
        condition: The authored condition.
        revolved: Whether the run solves a revolved section, whose loads are totals over the turn.
        dropped: The authored fields the frame has no solve coordinate for.
    """
    match condition:
        case SurfaceForce():
            size: Quantity = quantity(condition.force.to(U.N).magnitude.magnitude(), "N")
            text: str = readout(size)

            return f"{text} Total" if revolved else text
        case FixedDisplacement():
            authored: tuple[tuple[str, Quantity | None], ...] = (("x", condition.x), ("y", condition.y), ("z", condition.z))
            axes: list[tuple[str, Quantity | None]] = [(name, value) for name, value in authored if name not in dropped]
            moved: list[float] = [value.m_as("m") for _, value in axes if value is not None]

            if any(moved):
                return readout(quantity(math.hypot(*moved), "m"))

            if all(value is not None for _, value in axes):
                return None

            held: list[str] = [name.upper() for name, value in axes if value is not None]

            return f"{', '.join(held)} Fixed" if held else None
        case CurrentDensity():
            return readout(abs(condition.value), unit=U.A / U.mm**2)
        case FixedTemperature():
            return readout(condition.value, unit=U.K)
        case HeatFlux():
            return f"q {readout(condition.value)}"
        case Convection():
            return f"h {_bare(condition.coefficient, U.W / (U.m**2 * U.K))} · T {readout(condition.ext_temperature, unit=U.K)}"
        case HeatSource():
            return f"q {readout(condition.power_density)}"
        case JouleHeat():
            return "Joule Heat"
        case CoilDrive():
            return f"Coil {readout(condition.current)}"
        case FixedVoltage():
            return readout(condition.value)
        case CoilTerminal():
            return title(condition.role)
        case Magnetization() | CalculateMagneticForce():
            return None


def arrow_span(anchor: Vec3, direction: Vec3, normal: Vec3 | None, share: float, diagonal: float) -> tuple[Vec3, Vec3]:
    """The tail and head of a load arrow, sized by its share of the largest load and kept outside the body.

    Args:
        anchor: The face centroid the arrow starts or ends on.
        direction: The load direction.
        normal: The face's outward normal; None puts the tail on the face.
        share: The load against the largest load.
        diagonal: The scene diagonal the length scales with.
    """
    length: float = min(max(ARROW_SHARE * diagonal * share, ARROW_FLOOR * diagonal), ARROW_SHARE * diagonal)
    step: Vec3 = direction.normalized() * length

    if normal is not None and direction.dot(normal) < 0.0:
        return anchor - step, anchor

    return anchor, anchor + step


def revolve_angles(sweep: float) -> tuple[float, ...]:
    """The angles in degrees a magnet arrow repeats at in a revolved display of ``sweep`` degrees."""
    angles: list[float] = [step * REVOLVE_STEP for step in range(REVOLVE_COPIES)]

    return tuple(angle for angle in angles if angle <= sweep)


def shown_diagonal(bounds: Sequence[float], expansion: Expansion) -> float:
    """`D` `[m]`: The diagonal of the undeformed scene a section's ``bounds`` show as under ``expansion``."""
    x_min, x_max, y_min, y_max, z_min, z_max = (float(value) for value in bounds[:6])
    radius: float = max(abs(x_min), abs(x_max))
    axial: float = y_max - y_min

    match expansion:
        case Expansion.SECTION:
            return math.hypot(x_max - x_min, axial, z_max - z_min)
        case Expansion.MIRROR:
            return math.hypot(2.0 * radius, axial, z_max - z_min)
        case Expansion.REVOLVE:
            return math.hypot(2.0 * radius, 2.0 * radius, axial)


def mirrored(glyph: Glyph) -> Glyph:
    """``glyph`` reflected across the axis of a mirrored section: every radial component negated."""

    def flipped(vector: Vec3 | None) -> Vec3 | None:
        return None if vector is None else Vec3(-vector.x, vector.y, vector.z)

    return glyph.model_copy(update={"anchor": flipped(glyph.anchor), "normal": flipped(glyph.normal), "vector": flipped(glyph.vector)})


def spread(points: FloatArray, spacing: float) -> FloatArray:
    """Rows on an even lattice ``spacing`` apart over ``points`` along their principal axes, so marks cover a face uniformly.

    A flat set keeps the lattice points that land on it; a curved one snaps each to its nearest point.
    """
    unique: FloatArray = np.unique(points, axis=0)

    if len(unique) < 2 or spacing <= 0.0:
        return unique

    center: FloatArray = unique.mean(axis=0)
    offsets: FloatArray = unique - center
    axes: FloatArray = np.linalg.svd(offsets, full_matrices=False)[2]
    local: FloatArray = offsets @ axes.T
    extent: FloatArray = np.ptp(local, axis=0)
    spans: list[bool] = cast("list[bool]", (extent > FLAT_RATIO * extent.max()).tolist())

    if spans == [True, True, False]:
        # Uneven nodes tilt the principal axes off a rectangle's edges; the tightest bounding box in the plane lies along them.
        turns = cast("list[float]", np.radians(np.arange(0.0, 90.0, TURN_STEP)).tolist())
        boxes: list[float] = [float(np.ptp(local[:, :2] @ [math.cos(turn), math.sin(turn)]) * np.ptp(local[:, :2] @ [-math.sin(turn), math.cos(turn)])) for turn in turns]
        turn = turns[int(np.argmin(boxes))]
        axes = np.array([[math.cos(turn), math.sin(turn), 0.0], [-math.sin(turn), math.cos(turn), 0.0], [0.0, 0.0, 1.0]]) @ axes
        local = offsets @ axes.T
        extent = np.ptp(local, axis=0)

    low: FloatArray = local.min(axis=0)
    lows: list[float] = cast("list[float]", low.tolist())
    extents: list[float] = cast("list[float]", extent.tolist())
    rows: list[FloatArray] = []

    for start, span, spanned in zip(lows, extents, spans, strict=True):
        cells: int = max(1, round(span / spacing)) if spanned else 1
        rows.append(start + (np.arange(cells, dtype=np.float64) + 0.5) * span / cells)

    lattice: FloatArray = np.stack(np.meshgrid(*rows, indexing="ij"), axis=-1).reshape(-1, 3)
    gaps: FloatArray = np.linalg.norm(lattice[:, np.newaxis, :] - local[np.newaxis, :, :], axis=2)
    nearest: IntArray = gaps.argmin(axis=1)
    near: np.typing.NDArray[np.bool_] = gaps[np.arange(len(lattice)), nearest] < 0.75 * spacing

    if all(spans):
        return np.unique(unique[nearest[near]], axis=0)

    return center + lattice[near] @ axes


def densified(segments: FloatArray, step: float) -> FloatArray:
    """The ``(m, 2, 3)`` segments as points no more than ``step`` apart along each."""
    pieces: list[FloatArray] = [segments.reshape(-1, 3)]

    if step <= 0.0:
        return pieces[0]

    spans: FloatArray = segments[:, 1] - segments[:, 0]
    counts: list[int] = cast("list[int]", np.ceil(np.linalg.norm(spans, axis=1) / step).astype(np.int64).tolist())

    for index, count in enumerate(counts):
        if count > 1:
            steps: FloatArray = np.linspace(0.0, 1.0, count + 1)[1:-1, np.newaxis]
            pieces.append(segments[index : index + 1, 0] + steps * spans[index : index + 1])

    return np.concatenate(pieces)


def clamp_ticks(segments: FloatArray, outward: Vec3, diagonal: float) -> FloatArray:
    """The ``(k, 2, 3)`` parallel 45° clamp ticks every `TICK_SPACING` of the diagonal along a held 2D face, on its outward side.

    Args:
        segments: The ``(m, 2, 3)`` segments of the face, in any order.
        outward: The face's outward normal in the section plane.
        diagonal: The scene diagonal the spacing and length scale with.
    """
    spacing: float = TICK_SPACING * diagonal
    out: FloatArray = np.asarray(outward.normalized().to_list(), dtype=np.float64)
    along_face: FloatArray = np.array([-out[1], out[0], 0.0])
    slant: FloatArray = TICK_LENGTH * diagonal * (out + along_face) / math.sqrt(2.0)
    bases: FloatArray = spread(densified(segments, spacing / 8.0), spacing)

    return np.stack([bases, bases + slant], axis=1)


def dashed(segments: FloatArray, dash: float) -> FloatArray:
    """The ``(m, 2, 3)`` segments broken into dashes of length ``dash`` with gaps as long."""
    origins: FloatArray = segments[:, 0]
    spans: FloatArray = segments[:, 1] - origins
    lengths: list[float] = cast("list[float]", np.linalg.norm(spans, axis=1).tolist())
    pieces: list[FloatArray] = [np.zeros((0, 2, 3), dtype=np.float64)]

    for index, length in enumerate(lengths):
        if length == 0.0:
            continue

        starts: FloatArray = np.arange(0.0, length, 2.0 * dash)
        ends: FloatArray = np.minimum(starts + dash, length)
        unit: FloatArray = spans[index : index + 1] / length
        origin: FloatArray = origins[index : index + 1]
        pieces.append(np.stack([origin + starts[:, np.newaxis] * unit, origin + ends[:, np.newaxis] * unit], axis=1))

    return np.concatenate(pieces)


def circles(centers: FloatArray, radius: float) -> FloatArray:
    """The ``(k * CIRCLE_SIDES, 2, 3)`` sides of a circle of ``radius`` about each of the ``(k, 3)`` ``centers``, in the section's XY plane."""
    angles: FloatArray = np.linspace(0.0, 2.0 * math.pi, CIRCLE_SIDES + 1)
    ring: FloatArray = radius * np.column_stack([np.cos(angles), np.sin(angles), np.zeros_like(angles)])
    rings: FloatArray = centers[:, np.newaxis, :] + ring[np.newaxis]

    return np.stack([rings[:, :-1], rings[:, 1:]], axis=2).reshape(-1, 2, 3)


def _padded(record: ConditionRecord) -> Vec3 | None:
    """The record's solve components zero-padded to three, None when it has none."""
    values: list[float] = [0.0, 0.0, 0.0]

    if not record.solve_components:
        return None

    for component in record.solve_components:
        values[component.index - 1] = component.value

    return Vec3(values[0], values[1], values[2])


def _unit(vector: Vec3 | None) -> Vec3 | None:
    """``vector`` normalized, None for a missing or zero vector."""
    if vector is None or vector.magnitude() == 0.0:
        return None

    return vector.normalized()


def _drive_sign(record: ConditionRecord) -> float:
    """The sign of the first numeric keyword the record emitted, the sign of an out-of-plane drive."""
    numbers: list[float] = [keyword.value for keyword in record.keywords if isinstance(keyword.value, float | int) and not isinstance(keyword.value, bool)]

    return math.copysign(1.0, numbers[0]) if numbers else 1.0


def glyphs_of(conditions: ConditionsFile, bodies: Sequence[Body], boundaries: Sequence[Boundary], frame: Frame) -> list[Glyph]:
    """The glyphs of every drawable condition a run's groups received, joined to their measured centroids and normals.

    Args:
        conditions: What each group received from the sif.
        bodies: The run's bodies, which carry the centroids and bounding boxes.
        boundaries: The run's boundaries, which carry the centroids and outward normals.
        frame: The frame the run solved in.
    """
    found: list[Glyph] = []

    for group in conditions.bodies:
        body: Body | None = next((candidate for candidate in bodies if candidate.name == group.group), None)

        if body is None or body.centroid is None:
            continue

        for record in group.conditions:
            condition: ConditionUnion | None = record.condition

            if condition is None or condition.glyph is None:
                continue

            vector: Vec3 | None = None
            extent: float | None = None
            reach: float = 0.0

            if condition.glyph is ConditionGlyph.MAGNETIZATION and body.bbox is not None:
                spans: tuple[float, float, float] = (body.bbox.high - body.bbox.low).to_tuple()
                sizes: list[float] = [size for size in spans if size > FLAT_RATIO * max(spans)]
                vector = _unit(_padded(record))
                extent = MAGNET_SHARE * min(sizes) if sizes else None
                reach = 0.0 if vector is None else sum(abs(component) * span / 2.0 for component, span in zip(vector.to_tuple(), spans, strict=True))

            if condition.glyph is ConditionGlyph.CURRENT_DENSITY and frame.model_dim == 2:
                vector = Vec3(0.0, 0.0, _drive_sign(record))

            label: str | None = glyph_label(condition, revolved=frame.revolves, dropped=record.dropped)
            found.append(Glyph(group=group.group, target=EntityKind.BODY, kind=condition.glyph, anchor=body.centroid, vector=vector, label=label, extent=extent, reach=reach))

    for group in conditions.boundaries:
        boundary: Boundary | None = next((candidate for candidate in boundaries if candidate.name == group.group), None)

        if boundary is None or boundary.centroid is None:
            continue

        for record in group.conditions:
            condition = record.condition

            if condition is None or condition.glyph is None:
                continue

            solved: Vec3 | None = _padded(record)
            indices: set[int] = {component.index for component in record.solve_components}
            moved: bool = solved is not None and solved.magnitude() > 0.0
            held: tuple[bool, ...] = tuple(index in indices for index in range(1, frame.model_dim + 1)) if condition.glyph is ConditionGlyph.FIXED_DISPLACEMENT else ()
            vector = _unit(solved) if condition.glyph is ConditionGlyph.SURFACE_FORCE or moved else None
            share: float = solved.magnitude() if condition.glyph is ConditionGlyph.SURFACE_FORCE and solved is not None else 1.0
            label = glyph_label(condition, revolved=frame.revolves, dropped=record.dropped)
            glyph: Glyph = Glyph(
                group=group.group,
                target=EntityKind.BOUNDARY,
                kind=condition.glyph,
                anchor=boundary.centroid,
                normal=boundary.normal,
                vector=vector,
                share=share,
                label=label,
                held=held,
            )
            found.append(glyph)

    loads: list[float] = [glyph.share for glyph in found if glyph.kind is ConditionGlyph.SURFACE_FORCE]
    largest: float = max(loads, default=0.0)

    if largest <= 0.0:
        return found

    return [glyph.model_copy(update={"share": glyph.share / largest}) if glyph.kind is ConditionGlyph.SURFACE_FORCE else glyph for glyph in found]


class GlyphView(BaseModel):
    """What the Setup tab asks the scene to draw: which glyphs, their labels and size, and the selection, outline and ghost around them."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    kinds: frozenset[ConditionGlyph] = frozenset(ConditionGlyph)
    """The glyph kinds drawn; empty draws no glyph."""

    hidden: frozenset[str] = frozenset()
    """The entity ids whose glyphs are hidden."""

    plain_bodies: bool = False
    """Paint every body plain grey while the view is shown."""

    labels: GlyphLabels = GlyphLabels.SCENE
    """How the values are labelled."""

    scale: float = Field(default=1.0, gt=0.0)
    """`[-]`: Window pixels per CSS pixel, which text sizes and line widths scale by."""

    selected: str | None = None
    """The entity id highlighted in the accent; None highlights nothing."""

    outline: bool = True
    """Draw a 2D section's boundary loops and solve triad."""

    ghost: bool = False
    """Draw the authored bodies a 2D frame sectioned, beside the prism or revolved outline they are checked against."""

    accent: str = GLYPH_ACCENT
    """The selection color, any color pyvista resolves."""

    hover: str = GLYPH_ACCENT
    """The hover outline color, any color pyvista resolves."""

    ink: str = GLYPH_HALO
    """The color of lines drawn against the background: marks, leaders, the outline, the triad and the hull."""


@dataclass(frozen=True)
class GlyphPlacement:
    """How solve-frame geometry lands in the displayed scene: as solved, beside its mirror image, or on the planes of a revolution."""

    expansion: Expansion = Expansion.SECTION
    """How the section is shown."""

    sweep: float = FULL_SWEEP
    """`[deg]`: The turn a revolved display sweeps."""

    basis: Basis | None = None
    """Where the section sits in the authored model; None for a run solved whole."""

    diagonal: float = 1.0
    """`D` `[m]`: The undeformed scene diagonal glyph sizes scale with."""

    flat: bool = False
    """Whether the run solved on a 2D section."""

    @property
    def revolved(self) -> bool:
        """Whether the section is shown revolved into the authored frame."""
        return self.expansion is Expansion.REVOLVE and self.basis is not None

    def placed(self, points: FloatArray, theta: float = 0.0) -> FloatArray:
        """Solve-frame ``(n, 3)`` points on the plane at ``theta`` radians of a revolved display, unchanged otherwise."""
        if not self.revolved or self.basis is None:
            return points

        return np.asarray(self.basis.origin) + self.basis.rotate(points, theta)

    def copies(self, points: FloatArray) -> list[FloatArray]:
        """Solve-frame ``(n, 3)`` points as the display shows them: once, beside their mirror image, or on the 0° plane of a revolution."""
        if self.expansion is Expansion.MIRROR:
            return [points, points * MIRROR]

        return [self.placed(points)]

    def angles(self) -> tuple[float, ...]:
        """The radians a revolved display repeats a body's arrow at; only 0 otherwise."""
        if not self.revolved:
            return (0.0,)

        return tuple(math.radians(angle) for angle in revolve_angles(self.sweep))


def plain_spec(spec: DisplaySpec, bodies: Iterable[str]) -> DisplaySpec:
    """``spec`` with every one of ``bodies`` painted `PLAIN_BODY`, its own opacity kept."""
    styles: list[BodyStyle] = [BodyStyle(body=body, paint=BodyPaint.SOLID, color=PLAIN_BODY, opacity=next((style.opacity for style in spec.body_styles if style.body == body), 1.0)) for body in bodies]

    return spec.model_copy(update={"body_styles": tuple(styles)})


class GlyphHost(Protocol):
    """The scene a glyph layer draws over: its frame, how it is shown and the cells of each group."""

    @property
    def frame(self) -> Frame: ...

    @property
    def basis(self) -> Basis | None: ...

    @property
    def expanded(self) -> tuple[Expansion, float]: ...

    @property
    def warped(self) -> tuple[str, float] | None: ...

    @property
    def section_blocks(self) -> pv.MultiBlock: ...

    @property
    def blocks(self) -> pv.MultiBlock: ...

    @property
    def spec(self) -> DisplaySpec: ...

    def group_cells(self, ref: EntityRef) -> pv.DataSet | None: ...


@dataclass(frozen=True)
class GlyphDraw:
    """One merged glyph actor: its name, its geometry and how it is styled."""

    actor: str
    """The actor name: a `GlyphActor`, or the hover prefix and an entity id."""

    mesh: pv.PolyData
    """The geometry, in the displayed scene's frame."""

    color: str = GLYPH_FILL
    """Any color pyvista resolves."""

    opacity: float = 1.0
    """`[-]`: The actor's opacity."""

    line_width: float | None = None
    """`[px]`: The width of its lines in window pixels; None for surfaces."""

    culling: Literal["front"] | None = None
    """The faces culled, front for an arrow's halo."""

    labels: tuple[str, ...] = ()
    """The text at each point of a label actor; empty for geometry."""

    font_size: int = 0
    """The label font size in window pixels."""

    overlay: bool = False
    """Whether the lines draw over the scene as a 2D overlay at their world positions rather than among the bodies."""


def _segments(dataset: pv.DataSet) -> FloatArray:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
    """The ``(m, 2, 3)`` straight edges of ``dataset``'s lines and surface cells, a quadratic edge split at its mid node."""
    # Edge extraction yields nothing for 1D quadratic cells; the surface filter first turns them into polylines.
    surface = cast("pv.PolyData", dataset.extract_surface(algorithm=None))
    edges = cast("pv.PolyData", surface.extract_all_edges())
    lines: IntArray = np.asarray(edges.lines, dtype=np.int64).reshape(-1, 3)[:, 1:]  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # pyvista types the cell array loosely

    return np.asarray(edges.points, dtype=np.float64)[lines]


def _polylines(segments: FloatArray) -> pv.PolyData:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
    """``(m, 2, 3)`` segments as line polydata."""
    import pyvista as pv

    count: int = len(segments)
    ids: IntArray = np.arange(2 * count, dtype=np.int64).reshape(-1, 2)
    cells: IntArray = np.column_stack([np.full(count, 2, dtype=np.int64), ids]).reshape(-1)

    return pv.PolyData(segments.reshape(-1, 3), lines=cells)


def _overlay(draw: GlyphDraw) -> vtkActor2D:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
    """The lines of ``draw`` as a 2D actor that projects their world points and draws over the scene."""
    import pyvista as pv
    from vtkmodules.vtkRenderingCore import vtkActor2D, vtkCoordinate, vtkPolyDataMapper2D

    world = vtkCoordinate()
    world.SetCoordinateSystemToWorld()
    mapper = vtkPolyDataMapper2D()
    mapper.SetInputData(draw.mesh)  # pyright: ignore[reportUnknownMemberType]  # overloaded twice in the VTK stubs without @overload
    mapper.SetTransformCoordinate(world)
    actor = vtkActor2D()
    actor.SetMapper(mapper)
    red, green, blue = pv.Color(draw.color).float_rgb
    actor.GetProperty().SetColor(red, green, blue)
    actor.GetProperty().SetLineWidth(draw.line_width or 1.0)
    actor.GetProperty().SetOpacity(draw.opacity)

    return actor


def _surface(surface: Surface) -> pv.PolyData:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
    """A swept surface as polydata."""
    import pyvista as pv

    return pv.PolyData(surface.points, faces=surface.faces)


def _merged(meshes: Sequence[pv.PolyData]) -> pv.PolyData:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
    """``meshes`` appended into one polydata."""
    return cast("pv.PolyData", meshes[0].append_polydata(*meshes[1:])) if len(meshes) > 1 else meshes[0]  # pyright: ignore[reportUnknownMemberType]  # pyvista leaves the appended meshes untyped


def _arrow(tail: FloatArray, head: FloatArray, *, halo: bool = False) -> pv.PolyData:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
    """An arrow from ``tail`` to ``head``, or its halo: the arrow pushed out along its normals by `HALO_WIDTH` of its length."""
    import pyvista as pv

    span: FloatArray = head - tail
    start: list[float] = cast("list[float]", tail.tolist())
    direction: list[float] = cast("list[float]", span.tolist())
    arrow: pv.PolyData = pv.Arrow(start=start, direction=direction, scale=float(np.linalg.norm(span)), tip_length=0.3, tip_radius=0.12, shaft_radius=0.04)  # pyright: ignore[reportUnknownMemberType]  # pyvista types the vector parameters loosely

    if not halo:
        return arrow

    shell: pv.PolyData = cast("pv.PolyData", arrow.compute_normals(point_normals=True, cell_normals=False, split_vertices=False, auto_orient_normals=True))
    normals: FloatArray = np.asarray(shell.point_data["Normals"], dtype=np.float64)
    shell.points = np.asarray(shell.points, dtype=np.float64) + HALO_WIDTH * float(np.linalg.norm(span)) * normals

    return shell


def glyph_actors(
    glyphs: Sequence[Glyph], cells: Mapping[str, pv.DataSet], placement: GlyphPlacement, view: GlyphView
) -> list[GlyphDraw]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
    """The merged glyph actors of the shown glyphs, at most one per `GLYPH_ACTORS` name.

    Args:
        glyphs: The run's glyphs, anchors already moved with the warp.
        cells: The solve-frame cells of each glyph's group by entity id, already displaced with the warp.
        placement: How the solve frame lands in the displayed scene.
        view: Which glyphs to draw, and how large.
    """
    import pyvista as pv

    diagonal: float = placement.diagonal
    lift: float = TINT_LIFT * diagonal
    toward_viewer: FloatArray = np.array([0.0, 0.0, lift])
    arrows: list[pv.PolyData] = []
    halos: list[pv.PolyData] = []
    faces: list[pv.PolyData] = []
    lines: list[FloatArray] = []
    marks: list[FloatArray] = []
    leaders: list[FloatArray] = []
    spots: list[FloatArray] = []
    texts: list[str] = []
    areas: list[float] = []
    shown: list[Glyph] = [glyph for glyph in glyphs if glyph.kind in view.kinds and glyph.ref.id not in view.hidden]

    def point(vector: Vec3) -> FloatArray:
        return np.asarray(vector.to_list(), dtype=np.float64)

    def arrow(tail: FloatArray, head: FloatArray, theta: float = 0.0) -> None:
        ends: FloatArray = np.stack([tail, head])
        copies: list[FloatArray] = [placement.placed(ends, theta)] if placement.revolved else placement.copies(ends)

        for placed in copies:
            tail_at: FloatArray = placed[0:1].reshape(3)
            head_at: FloatArray = placed[1:2].reshape(3)
            arrows.append(_arrow(tail_at, head_at))
            halos.append(_arrow(tail_at, head_at, halo=True))

    def chip(glyph: Glyph, at: FloatArray | None = None) -> None:
        if glyph.label is None or view.labels is GlyphLabels.NONE:
            return

        anchor: FloatArray = point(glyph.anchor)
        spot: FloatArray = anchor if at is None else at

        if at is None and glyph.normal is not None and glyph.target is EntityKind.BOUNDARY:
            spot = anchor + LEADER_SHARE * diagonal * point(glyph.normal)
            leaders.append(np.stack([anchor, spot])[np.newaxis])

        spots.append(spot)
        texts.append(glyph.label)

    def tint(glyph: Glyph) -> FloatArray | None:
        found: pv.DataSet | None = cells.get(glyph.ref.id)

        if found is None or found.n_cells == 0:
            return None

        if not placement.flat:
            surface: pv.PolyData = cast("pv.PolyData", found.extract_surface(algorithm=None))
            surface.clear_data()

            if glyph.normal is not None:
                surface.points = np.asarray(surface.points, dtype=np.float64) + lift * point(glyph.normal)

            faces.append(surface)
            areas.append(float(surface.area))

            return np.asarray(surface.points, dtype=np.float64)

        segments: FloatArray = _segments(found)

        if placement.revolved and placement.basis is not None:
            outward: FloatArray = np.zeros(3) if glyph.normal is None else lift * point(glyph.normal)
            raised: FloatArray = segments.reshape(-1, 3) + outward
            pairs: IntArray = np.arange(len(raised), dtype=np.int64).reshape(-1, 2)
            faces.append(_surface(swept_lines(raised, pairs, placement.basis, sweep=placement.sweep)))
        else:
            lines.append(segments + toward_viewer)

        return segments

    for glyph in shown:
        anchor: FloatArray = point(glyph.anchor)

        match glyph.kind:
            case ConditionGlyph.SURFACE_FORCE:
                if glyph.vector is None:
                    continue

                start, end = arrow_span(glyph.anchor, glyph.vector, glyph.normal, glyph.share, diagonal)
                pulled: bool = glyph.normal is None or glyph.vector.dot(glyph.normal) >= 0.0
                gap: Vec3 = glyph.vector.normalized() * (LEADER_SHARE * diagonal / 4.0)
                arrow(point(start), point(end))
                chip(glyph, point(end + gap if pulled else start - gap))
            case ConditionGlyph.FIXED_DISPLACEMENT:
                held: FloatArray | None = tint(glyph)
                area: float = areas[-1] if areas else 0.0
                outward: Vec3 | None = glyph.normal

                if held is not None and outward is not None and placement.flat:
                    rolled: bool = not all(glyph.held)
                    segments: FloatArray = held.reshape(-1, 2, 3) + toward_viewer

                    if rolled:
                        radius: float = ROLLER_RADIUS * diagonal
                        face_length: float = float(np.linalg.norm(segments[:, 1] - segments[:, 0], axis=1).sum())
                        middles: FloatArray = spread(densified(segments, radius), max(face_length / PIN_COUNT, 3.0 * radius))
                        marks.append(circles(middles + radius * point(outward), radius))
                    else:
                        marks.append(clamp_ticks(segments, outward, diagonal))
                elif held is not None and outward is not None:
                    roots: FloatArray = spread(held, math.sqrt(area / PIN_COUNT))
                    tips: FloatArray = roots + PIN_LENGTH * diagonal * point(outward)
                    marks.append(np.stack([roots, tips], axis=1))

                    if not all(glyph.held):
                        centers: list[list[float]] = cast("list[list[float]]", tips.tolist())
                        arrows.extend(pv.Sphere(radius=ROLLER_RADIUS * diagonal, center=center) for center in centers)  # pyright: ignore[reportUnknownMemberType]  # pyvista types the center parameter loosely

                if glyph.vector is not None:
                    reach: FloatArray = anchor + ARROW_FLOOR * diagonal * point(glyph.vector)
                    marks.append(dashed(np.stack([anchor, reach])[np.newaxis], DASH_SHARE * diagonal / 2.0))

                chip(glyph)
            case ConditionGlyph.MAGNETIZATION:
                if glyph.vector is None or glyph.extent is None:
                    continue

                direction: FloatArray = point(glyph.vector)
                length: float = max(glyph.extent, ARROW_FLOOR * diagonal)
                solid: bool = placement.revolved or not placement.flat
                tail: FloatArray = anchor + direction * glyph.reach if solid else anchor - direction * length / 2.0 + toward_viewer

                for theta in placement.angles():
                    arrow(tail, tail + direction * length, theta)
            case ConditionGlyph.CURRENT_DENSITY:
                radius = MARKER_RADIUS * diagonal

                if glyph.vector is not None:
                    center: FloatArray = anchor + toward_viewer
                    marks.append(circles(center[np.newaxis], radius))

                    if glyph.vector.z > 0.0:
                        marks.extend(circles(center[np.newaxis], radius * scale) for scale in (0.15, 0.25))
                    else:
                        cross: float = radius / math.sqrt(2.0)
                        marks.append(np.array([[center + [-cross, -cross, 0.0], center + [cross, cross, 0.0]], [center + [-cross, cross, 0.0], center + [cross, -cross, 0.0]]]))

                chip(glyph, anchor + np.array([1.5 * radius, 0.0, 0.0]) if glyph.vector is not None else None)
            case ConditionGlyph.THERMAL | ConditionGlyph.VOLTAGE:
                if glyph.target is EntityKind.BOUNDARY:
                    _ = tint(glyph)

                chip(glyph)

    draws: list[GlyphDraw] = []
    width: float = view.scale

    def placed_segments(groups: list[FloatArray]) -> pv.PolyData | None:
        if not groups:
            return None

        joined: FloatArray = np.concatenate(groups)

        if not len(joined):
            return None

        copies: list[FloatArray] = [copy.reshape(-1, 2, 3) for copy in placement.copies(joined.reshape(-1, 3))]

        return _polylines(np.concatenate(copies))

    if arrows:
        draws.append(GlyphDraw(GlyphActor.ARROWS, _merged(arrows), color=GLYPH_FILL))

    if halos:
        draws.append(GlyphDraw(GlyphActor.HALOS, _merged(halos), color=GLYPH_HALO, culling="front"))

    if faces:
        draws.append(GlyphDraw(GlyphActor.FACES, _merged(faces), color=GLYPH_TINT, opacity=TINT_OPACITY))

    for actor, groups, color, px in (
        (GlyphActor.LINES, lines, GLYPH_TINT, TINT_LINE_PX),
        (GlyphActor.MARKS, marks, view.ink, MARK_LINE_PX),
        (GlyphActor.LEADERS, leaders, view.ink, MARK_LINE_PX),
    ):
        mesh: pv.PolyData | None = placed_segments(groups)

        if mesh is not None:
            draws.append(GlyphDraw(actor, mesh, color=color, line_width=px * width))

    if spots:
        located: FloatArray = placement.placed(np.asarray(spots, dtype=np.float64))
        draws.append(GlyphDraw(GlyphActor.LABELS, pv.PolyData(located), labels=tuple(texts), font_size=round(LABEL_FONT_PX * view.scale)))

    return draws


def _mean_shift(cells: pv.DataSet, warp: tuple[str, float] | None) -> FloatArray:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
    """The mean displacement of ``cells`` under ``warp``, zero without one or without the array."""
    if warp is None:
        return np.zeros(3)

    name, scale = warp

    for arrays in (cells.point_data, cells.cell_data):
        if name in arrays:
            values: FloatArray = np.asarray(arrays[name], dtype=np.float64)

            return scale * values.reshape(-1, 3).mean(axis=0) if values.size else np.zeros(3)

    return np.zeros(3)


@dataclass
class GlyphLayer:
    """A run's glyphs and the scene decorations the Setup tab asks for, drawn over a host scene as named actors."""

    glyphs: list[Glyph]
    """The drawable conditions, in the solve frame."""

    bodies: Sequence[Body] = ()
    """The run's bodies."""

    boundaries: Sequence[Boundary] = ()
    """The run's boundaries."""

    frame: Frame = field(default_factory=lambda: default_frame(3))
    """The frame the run solved in."""

    mesh_groups: MeshGroups | None = None
    """The group cells from the Elmer mesh, for groups the host has no cells of; None when the mesh is gone."""

    authored: pv.PolyData | None = None
    """The authored bodies a 2D frame sectioned, in the solve frame; None when the run wrote none."""

    run_id: str = ""
    """The run bundle's name, for the log."""

    host: GlyphHost | None = None
    """The scene the layer last drew over."""

    drawn: tuple[object, ...] | None = None
    """What the drawn actors were built for: the view, the expansion, the warp and the hidden bodies."""

    names: frozenset[str] = frozenset()
    """The actor names the plotter holds from the last draw."""

    label_spots: FloatArray = field(default_factory=lambda: np.zeros((0, 3)))
    """`[m]`: The ``(n, 3)`` points the last draw labelled, in the displayed scene's frame."""

    label_texts: tuple[str, ...] = ()
    """The text at each of ``label_spots``."""

    hover_actors: dict[str, pv.Actor] = field(default_factory=dict)
    """The hidden hover outline of each entity id the last draw outlined."""

    @classmethod
    def from_run(cls, run: Run, scene: FieldScene | None = None) -> GlyphLayer:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """The glyphs of ``run`` from its conditions sidecar and entities, in the frame of ``scene`` when given, else of its manifest."""
        manifest = run_manifest(run) if scene is None else None
        frame: Frame = scene.frame if scene is not None else manifest.config.frame if manifest is not None else default_frame(3)
        conditions: ConditionsFile | None = run.conditions()
        bodies: list[Body] = list(run.bodies)
        boundaries: list[Boundary] = list(run.boundaries)
        glyphs: list[Glyph] = glyphs_of(conditions, bodies, boundaries, frame) if conditions is not None else []

        try:
            authored: pv.PolyData | None = run.authored_geometry()
        except (FileNotFoundError, ValueError):
            authored = None

        log.bind(run_id=run.path.name, glyphs=len(glyphs), conditions=conditions is not None, ghost=authored is not None).info("Glyph layer loaded.")

        return cls(glyphs, bodies=bodies, boundaries=boundaries, frame=frame, mesh_groups=run.mesh_groups(), authored=authored, run_id=run.path.name)

    def group_cells(self, ref: EntityRef, host: GlyphHost | None = None) -> pv.DataSet | None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """The solve-frame cells of ``ref`` from ``host`` (else the last host), else from the Elmer mesh; None when neither has any."""
        source: GlyphHost | None = host if host is not None else self.host
        found: pv.DataSet | None = source.group_cells(ref) if source is not None else None

        if found is not None and found.n_cells:
            return found

        groups: MeshGroups | None = self.mesh_groups

        if groups is None:
            return None

        match ref.kind:
            case EntityKind.BODY:
                body: Body | None = next((candidate for candidate in self.bodies if candidate.name == ref.name), None)
                mesh: pv.DataSet | None = groups.body(body) if body is not None else None
            case EntityKind.BOUNDARY:
                boundary: Boundary | None = next((candidate for candidate in self.boundaries if candidate.name == ref.name), None)
                mesh = groups.boundary(boundary) if boundary is not None else None

        if mesh is None or not mesh.n_cells:
            return None

        if self.frame.model_dim == 2:
            points: FloatArray = np.asarray(mesh.points, dtype=np.float64)
            points[:, 2] = 0.0
            mesh.points = points

        return mesh

    def has(self, ref: EntityRef) -> bool:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """Whether ``ref`` has cells to highlight."""
        return self.group_cells(ref) is not None

    def placement(self, host: GlyphHost) -> GlyphPlacement:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """How ``host`` shows the solve frame."""
        expansion, sweep = host.expanded

        return GlyphPlacement(
            expansion=expansion,
            sweep=sweep,
            basis=host.basis,
            diagonal=shown_diagonal(tuple(host.section_blocks.bounds), expansion),
            flat=host.frame.model_dim == 2,
        )

    def bounds_of(self, ref: EntityRef) -> Bounds | None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """The bounds of ``ref`` as the last host shows it, its cells moved by their mean warp as the glyph anchors are, from its measured box when it has no cells there; None when it has neither."""
        host: GlyphHost | None = self.host

        if host is not None and ref.kind is EntityKind.BODY and ref.name in host.blocks.keys():
            return cast("Bounds", tuple(cast("pv.DataSet", host.blocks[ref.name]).bounds))

        cells: pv.DataSet | None = self.group_cells(ref)

        if cells is not None and host is not None:
            moved: FloatArray = np.asarray(cells.points, dtype=np.float64) + _mean_shift(cells, host.warped)
            points: FloatArray = np.concatenate(self.placement(host).copies(moved))
            low: FloatArray = points.min(axis=0)
            high: FloatArray = points.max(axis=0)

            return cast("Bounds", (low[0], high[0], low[1], high[1], low[2], high[2]))

        candidates: Sequence[Body | Boundary] = self.bodies if ref.kind is EntityKind.BODY else self.boundaries
        entity: Body | Boundary | None = next((candidate for candidate in candidates if candidate.name == ref.name), None)

        if entity is None or entity.bbox is None:
            return None

        box = entity.bbox

        return (box.low.x, box.high.x, box.low.y, box.high.y, box.low.z, box.high.z)

    def update(self, plotter: pv.Plotter, host: GlyphHost, view: GlyphView | None) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context; proven by the gated glyph tests
        """Draw ``view`` over ``host`` as named actors, rebuilt only when the view, the expansion, the warp or the hidden bodies changed.

        Args:
            plotter: The plotter the host scene is drawn in.
            host: The scene the glyphs sit on.
            view: What to draw; None removes every glyph actor.
        """
        self.host = host
        key: tuple[object, ...] = (view, host.expanded, host.warped, host.spec.hidden_bodies)
        present: set[str] = set(cast("Mapping[str, object]", plotter.renderer.actors).keys())

        if key == self.drawn and self.names <= present:
            return

        for name in sorted(name for name in present if name.startswith(GLYPH_PREFIX)):
            _ = plotter.remove_actor(name, render=False)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista removes by actor name, and its _Wrapped stub loses `self`

        self.drawn = key
        self.names = frozenset()
        self.label_spots = np.zeros((0, 3))
        self.label_texts = ()
        self.hover_actors = {}

        if view is None:
            return

        built: list[GlyphDraw] = [*self.glyph_draws(host, view), *self.decorations(host, view)]
        draws: list[GlyphDraw] = [draw for draw in built if draw.mesh.n_points]
        labelled: list[GlyphDraw] = [draw for draw in draws if draw.labels]
        merged: list[GlyphDraw] = [draw for draw in draws if not draw.labels]

        if labelled:
            import pyvista as pv

            spots: FloatArray = np.concatenate([np.asarray(draw.mesh.points, dtype=np.float64) for draw in labelled])
            texts: tuple[str, ...] = tuple(text for draw in labelled for text in draw.labels)
            merged.append(GlyphDraw(GlyphActor.LABELS, pv.PolyData(spots), labels=texts, font_size=labelled[0].font_size))
            self.label_spots = spots
            self.label_texts = texts

        for draw in merged:
            if draw.overlay:
                _ = plotter.add_actor(_overlay(draw), name=str(draw.actor), reset_camera=False, render=False)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista adds any prop by name, and its _Wrapped stub loses `self`
                continue

            if draw.labels:
                _ = plotter.add_point_labels(  # pyright: ignore[reportUnknownMemberType]
                    np.asarray(draw.mesh.points, dtype=np.float64),
                    list(draw.labels),
                    font_size=draw.font_size,
                    text_color=GLYPH_FILL,
                    shape_color=GLYPH_HALO,
                    shape_opacity=LABEL_OPACITY,
                    show_points=False,
                    always_visible=True,
                    reset_camera=False,
                    render=False,
                    name=str(draw.actor),
                )
                continue

            _ = plotter.add_mesh(  # pyright: ignore[reportUnknownMemberType]
                draw.mesh,
                color=draw.color,
                opacity=draw.opacity,
                line_width=draw.line_width,
                culling=draw.culling,
                show_scalar_bar=False,
                reset_camera=False,
                render=False,
                name=str(draw.actor),
            )

        for ref in self.refs(host):
            hover: GlyphDraw | None = self.hover_draw(ref, host, view)
            if hover is None or not hover.mesh.n_points:
                continue

            actor = plotter.add_mesh(  # pyright: ignore[reportUnknownMemberType]
                hover.mesh,
                color=hover.color,
                opacity=hover.opacity,
                line_width=hover.line_width,
                show_scalar_bar=False,
                reset_camera=False,
                render=False,
                name=hover.actor,
            )
            actor.SetVisibility(False)
            self.hover_actors[ref.id] = actor

        hovers: list[str] = [f"{GlyphActor.HOVER}{ref_id}" for ref_id in self.hover_actors]
        self.names = frozenset([*(f"{draw.actor}{LABELS_SUFFIX}" if draw.labels else str(draw.actor) for draw in merged), *hovers])
        log.bind(run_id=self.run_id, actors=sorted(self.names), glyphs=len(self.glyphs)).debug("Glyphs drawn.")

    def refs(self, host: GlyphHost) -> list[EntityRef]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """Every body ``host`` draws and every boundary of the run."""
        bodies: list[EntityRef] = [EntityRef(kind=EntityKind.BODY, name=name) for name in host.blocks.keys()]

        return [*bodies, *(EntityRef(kind=EntityKind.BOUNDARY, name=boundary.name) for boundary in self.boundaries)]

    def hover_draw(self, ref: EntityRef, host: GlyphHost, view: GlyphView) -> GlyphDraw | None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """The outline ``ref`` shows while its row is hovered: a body's feature edges, a boundary's lines or rim; None when it has no cells.

        Args:
            ref: The body or boundary to outline.
            host: The scene the outline sits on, whose warp it follows.
            view: The view whose hover color and pixel scale it takes.
        """
        import pyvista as pv

        placement: GlyphPlacement = self.placement(host)
        lift: float = 2.0 * TINT_LIFT * placement.diagonal
        name: str = f"{GlyphActor.HOVER}{ref.id}"
        width: float = HOVER_LINE_PX * view.scale

        if ref.kind is EntityKind.BODY:
            if ref.name not in host.blocks.keys():
                return None

            edges: pv.PolyData | None = feature_outline(pv.MultiBlock([cast("pv.DataSet", host.blocks[ref.name])]))
            if edges is None:
                return None

            if placement.flat and not placement.revolved:
                edges.points = np.asarray(edges.points, dtype=np.float64) + np.array([0.0, 0.0, lift])

            return GlyphDraw(name, edges, color=view.hover, line_width=width)

        cells: pv.DataSet | None = self.group_cells(ref, host)
        if cells is None:
            return None

        if host.warped is not None:
            field, scale = host.warped
            # A boundary without a condition comes from the plain mesh, so it takes the solved displacement from the bodies around it.
            carrying: pv.DataSet = cells if field in cells.point_data else cast("pv.DataSet", cells.sample(host.section_blocks.combine()))
            cells = warped(carrying, field, scale) if field in carrying.point_data else cells

        boundary: Boundary | None = next((candidate for candidate in self.boundaries if candidate.name == ref.name), None)
        normal: FloatArray = np.zeros(3) if boundary is None or boundary.normal is None else np.asarray(boundary.normal.to_list(), dtype=np.float64)

        if placement.flat and placement.revolved and placement.basis is not None:
            raised: FloatArray = _segments(cells).reshape(-1, 3) + lift * normal
            pairs: IntArray = np.arange(len(raised), dtype=np.int64).reshape(-1, 2)

            return GlyphDraw(name, _surface(swept_lines(raised, pairs, placement.basis, sweep=placement.sweep)), color=view.hover, opacity=HOVER_OPACITY)

        if placement.flat:
            segments: FloatArray = _segments(cells) + np.array([0.0, 0.0, lift])
            copies: list[FloatArray] = [copy.reshape(-1, 2, 3) for copy in placement.copies(segments.reshape(-1, 3))]

            return GlyphDraw(name, _polylines(np.concatenate(copies)), color=view.hover, line_width=width)

        # A group's boundary elements come with mixed windings, which the feature filter reads as folds between neighbours; a 3D rim stays on the surface, where VTK draws lines over coincident faces.
        surface: pv.PolyData = cast("pv.PolyData", cells.extract_surface(algorithm=None))
        faced: bool = surface.GetNumberOfPolys() > 0
        wound: pv.PolyData = cast("pv.PolyData", surface.compute_normals(cell_normals=True, point_normals=False, consistent_normals=True, split_vertices=False)) if faced else surface
        rim: pv.PolyData | None = feature_outline(pv.MultiBlock([wound]))

        return GlyphDraw(name, rim, color=view.hover, line_width=width) if rim is not None else None

    def glyph_draws(self, host: GlyphHost, view: GlyphView) -> list[GlyphDraw]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """The condition glyphs over ``host``, their anchors and cells moved with its warp."""
        placement: GlyphPlacement = self.placement(host)
        shown: list[Glyph] = [glyph for glyph in self.glyphs if glyph.kind in view.kinds and glyph.ref.id not in view.hidden]
        cells: dict[str, pv.DataSet] = {}
        moved: list[Glyph] = []

        for glyph in shown:
            found: pv.DataSet | None = self.group_cells(glyph.ref, host)

            if found is None:
                moved.append(glyph)
                continue

            shift_x, shift_y, shift_z = cast("list[float]", _mean_shift(found, host.warped).tolist())
            anchor: Vec3 = glyph.anchor + Vec3(shift_x, shift_y, shift_z)
            moved.append(glyph.model_copy(update={"anchor": anchor}))
            warp: tuple[str, float] | None = host.warped
            displaced: bool = warp is not None and (warp[0] in found.point_data or warp[0] in found.cell_data)
            cells[glyph.ref.id] = warped(found, *warp) if warp is not None and displaced else found

        return glyph_actors(moved, cells, placement, view)

    def decorations(self, host: GlyphHost, view: GlyphView) -> list[GlyphDraw]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """The selection highlight, the section outline with its triad and plane, and the authored ghost with its hull."""
        import pyvista as pv

        placement: GlyphPlacement = self.placement(host)
        diagonal: float = placement.diagonal
        lift: FloatArray = np.array([0.0, 0.0, TINT_LIFT * diagonal])
        draws: list[GlyphDraw] = []
        plane = self.frame.plane

        def segments_mesh(segments: FloatArray) -> pv.PolyData:
            copies: list[FloatArray] = [copy.reshape(-1, 2, 3) for copy in placement.copies(segments.reshape(-1, 3))]

            return _polylines(np.concatenate(copies))

        ref: EntityRef | None = EntityRef.parse(view.selected) if view.selected else None

        if ref is not None and ref.kind is EntityKind.BODY and ref.name in host.blocks.keys():
            block = cast("pv.DataSet", host.blocks[ref.name])
            edges: pv.PolyData | None = feature_outline(pv.MultiBlock([block]))
            raise_by: FloatArray = 2.0 * lift if placement.flat and not placement.revolved else np.zeros(3)

            if edges is not None:
                edges.points = np.asarray(edges.points, dtype=np.float64) + raise_by
                draws.append(GlyphDraw(GlyphActor.SELECTION, edges, color=view.accent, line_width=SILHOUETTE_LINE_PX * view.scale))

            if ref.name not in host.spec.hidden_bodies:
                fill: pv.PolyData = cast("pv.PolyData", block.extract_surface(algorithm=None))
                fill.clear_data()
                fill.points = np.asarray(fill.points, dtype=np.float64) + raise_by
                draws.append(GlyphDraw(GlyphActor.SELECTION_FILL, fill, color=view.accent, opacity=SELECTION_TINT))
        elif ref is not None and ref.kind is EntityKind.BOUNDARY:
            cells: pv.DataSet | None = self.group_cells(ref, host)
            boundary: Boundary | None = next((candidate for candidate in self.boundaries if candidate.name == ref.name), None)
            normal: FloatArray = np.zeros(3) if boundary is None or boundary.normal is None else np.asarray(boundary.normal.to_list(), dtype=np.float64)

            if cells is not None and host.warped is not None and host.warped[0] in cells.point_data:
                cells = warped(cells, *host.warped)

            if cells is not None and placement.flat and placement.revolved and placement.basis is not None:
                raised: FloatArray = _segments(cells).reshape(-1, 3) + 2.0 * TINT_LIFT * diagonal * normal
                pairs: IntArray = np.arange(len(raised), dtype=np.int64).reshape(-1, 2)
                draws.append(GlyphDraw(GlyphActor.SELECTION_FILL, _surface(swept_lines(raised, pairs, placement.basis, sweep=placement.sweep)), color=view.accent, opacity=SELECTION_OPACITY))
            elif cells is not None and placement.flat:
                draws.append(GlyphDraw(GlyphActor.SELECTION, segments_mesh(_segments(cells) + 2.0 * lift), color=view.accent, line_width=SELECTION_LINE_PX * view.scale))
            elif cells is not None:
                surface: pv.PolyData = cast("pv.PolyData", cells.extract_surface(algorithm=None))
                surface.clear_data()
                surface.points = np.asarray(surface.points, dtype=np.float64) + 2.0 * TINT_LIFT * diagonal * normal
                draws.append(GlyphDraw(GlyphActor.SELECTION_FILL, surface, color=view.accent, opacity=SELECTION_OPACITY))

        if plane is not None and view.outline:
            loops: pv.PolyData | None = feature_outline(self.deformed_section(host))

            # VTK.wasm lets a revolved display's translucent bodies hide or stipple the lines behind and along them, so there the outline and triad draw on top in both renderers.
            def stroked(actor: GlyphActor, lines: pv.PolyData) -> GlyphDraw:
                return GlyphDraw(actor, lines, color=view.ink, line_width=OUTLINE_LINE_PX * view.scale, overlay=placement.revolved)

            if loops is not None:
                draws.append(stroked(GlyphActor.OUTLINE, segments_mesh(_segments(loops) + lift)))

            bounds: Sequence[float] = tuple(host.section_blocks.bounds)
            reach: float = TRIAD_SHARE * diagonal
            gap: float = reach / 2.0
            # Labels run right from their point, so the triad sits wholly below the section to keep its Y label off the body.
            corner: FloatArray = np.array([bounds[0], bounds[2] - gap - reach, 0.0])
            axes: FloatArray = np.array([[corner, corner + [reach, 0.0, 0.0]], [corner, corner + [0.0, reach, 0.0]]])
            draws.append(stroked(GlyphActor.TRIAD, _polylines(np.concatenate(placement.copies(axes.reshape(-1, 3))[:1]).reshape(-1, 2, 3))))

            if view.labels is GlyphLabels.SCENE:
                across, up = self.frame.axis_names
                tips: FloatArray = placement.placed(axes[:, 1])
                draws.append(GlyphDraw(GlyphActor.LABELS, pv.PolyData(tips), labels=(across, up), font_size=round(LABEL_FONT_PX * view.scale)))

            if placement.revolved:
                square: FloatArray = np.array([[bounds[0], bounds[2], 0.0], [bounds[1], bounds[2], 0.0], [bounds[1], bounds[3], 0.0], [bounds[0], bounds[3], 0.0]])
                quad: pv.PolyData = pv.PolyData(placement.placed(square), faces=np.array([4, 0, 1, 2, 3]))
                draws.append(GlyphDraw(GlyphActor.PLANE, quad, color=GLYPH_TINT, opacity=PLANE_OPACITY))

        if plane is not None and view.ghost and self.authored is not None:
            ghost: pv.PolyData = self.authored.copy()
            ghost.clear_data()
            ghost.points = placement.placed(np.asarray(ghost.points, dtype=np.float64))
            draws.append(GlyphDraw(GlyphActor.GHOST, ghost, color=GLYPH_TINT, opacity=GHOST_OPACITY))
            silhouette: pv.PolyData = ghost.extract_feature_edges(boundary_edges=True, feature_edges=True, manifold_edges=False, non_manifold_edges=False, feature_angle=30.0)
            hull: FloatArray = self.hull(host, placement)
            pieces: list[FloatArray] = [_segments(silhouette)] if silhouette.n_cells else []
            pieces.append(dashed(hull, DASH_SHARE * diagonal))
            draws.append(GlyphDraw(GlyphActor.HULL, _polylines(np.concatenate(pieces)), color=view.ink, line_width=MARK_LINE_PX * view.scale))

        return draws

    def deformed_section(self, host: GlyphHost) -> pv.MultiBlock:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """The section blocks of ``host`` displaced by its warp, so an outline follows the body the scene shows; as solved without a warp."""
        import pyvista as pv

        warp: tuple[str, float] | None = host.warped

        if warp is None:
            return host.section_blocks

        moved: pv.MultiBlock = pv.MultiBlock()

        for name in (str(key) for key in host.section_blocks.keys()):
            block: pv.DataSet = cast("pv.DataSet", host.section_blocks[name])
            displaced: bool = warp[0] in block.point_data or warp[0] in block.cell_data
            moved.append(warped(block, *warp) if displaced else block, name=name)

        return moved

    def hull(self, host: GlyphHost, placement: GlyphPlacement) -> FloatArray:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """The ``(m, 2, 3)`` edges the ghost is checked against: the section extruded through the prism depth, or revolved about the axis."""

        loops: pv.PolyData | None = feature_outline(host.section_blocks)

        if loops is None:
            return np.zeros((0, 2, 3))

        if self.frame.revolves:
            basis: Basis = placement.basis if placement.revolved and placement.basis is not None else Basis.SOLVE
            points: FloatArray = np.asarray(loops.points, dtype=np.float64)
            pairs: IntArray = np.asarray(loops.lines, dtype=np.int64).reshape(-1, 3)[:, 1:]  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # pyvista types the cell array loosely
            swept: pv.PolyData = _surface(swept_lines(points, pairs, basis, segments=HULL_SEGMENTS))
            rings: pv.PolyData = swept.extract_feature_edges(boundary_edges=True, feature_edges=True, manifold_edges=False, non_manifold_edges=False, feature_angle=30.0)
            outline: FloatArray = points[pairs].reshape(-1, 3)
            meridians: list[FloatArray] = [(np.asarray(basis.origin) + basis.rotate(outline, math.radians(angle))).reshape(-1, 2, 3) for angle in revolve_angles(FULL_SWEEP)]

            return np.concatenate([_segments(rings), *meridians])

        # A Cartesian 2D frame totals a per-metre integral over the prism depth, so a unit X integral totals to the depth.
        depth: float = self.frame.total(Vec3(1.0, 0.0, 0.0)).x
        surface: pv.PolyData = cast("pv.PolyData", host.section_blocks.extract_surface(algorithm=None))
        surface.clear_data()
        surface.points = np.asarray(surface.points, dtype=np.float64) - [0.0, 0.0, depth / 2.0]
        prism = cast("pv.PolyData", surface.extrude((0.0, 0.0, depth), capping=True))  # pyright: ignore[reportUnknownMemberType]  # pyvista leaves the extrusion untyped
        edges: pv.PolyData = prism.extract_feature_edges(boundary_edges=True, feature_edges=True, manifold_edges=False, non_manifold_edges=False, feature_angle=30.0)

        return _segments(edges)


class GroupScene:
    """A run without fields drawn from its Elmer mesh: every body plain grey, with its glyph layer over them."""

    def __init__(
        self, groups: MeshGroups, *, bodies: Bodies, boundaries: Boundaries, frame: Frame, run_id: str
    ) -> None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """Split the mesh into one block per named body.

        Args:
            groups: The run's Elmer mesh by group.
            bodies: The run's bodies.
            boundaries: The run's boundaries.
            frame: The frame the run solved in.
            run_id: The run bundle's name.
        """
        import pyvista as pv

        self.groups: MeshGroups = groups
        self.bodies: Bodies = bodies
        self.boundaries: Boundaries = boundaries
        self.frame: Frame = frame
        self.run_id: str = run_id
        self.basis: Basis | None = None
        self.expanded: tuple[Expansion, float] = (Expansion.SECTION, FULL_SWEEP)
        self.warped: tuple[str, float] | None = None
        self.spec: DisplaySpec = DisplaySpec()
        self.glyphs: GlyphLayer | None = None
        self.glyph_view: GlyphView | None = None
        blocks: pv.MultiBlock = pv.MultiBlock()

        for body in bodies:
            cells: pv.UnstructuredGrid = groups.body(body)

            if not cells.n_cells:
                continue

            if frame.model_dim == 2:
                points: FloatArray = np.asarray(cells.points, dtype=np.float64)
                points[:, 2] = 0.0
                cells.points = points

            blocks.append(cells, name=body.name)

        self.section_blocks: pv.MultiBlock = blocks
        self.blocks: pv.MultiBlock = blocks

    @classmethod
    def from_run(cls, run: Run) -> GroupScene | None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """The mesh-only scene of ``run`` with its glyph layer; None when its Elmer mesh is gone."""
        groups: MeshGroups | None = run.mesh_groups()

        if groups is None:
            return None

        layer: GlyphLayer = GlyphLayer.from_run(run)
        scene: GroupScene = cls(groups, bodies=run.bodies, boundaries=run.boundaries, frame=layer.frame, run_id=run.path.name)
        scene.glyphs = layer

        return scene

    def group_cells(self, ref: EntityRef) -> pv.DataSet | None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated glyph tests
        """The cells of ``ref`` from the Elmer mesh."""
        if ref.kind is EntityKind.BODY:
            return cast("pv.DataSet", self.section_blocks[ref.name]) if ref.name in self.section_blocks.keys() else None

        boundary: Boundary | None = self.boundaries.get(ref.name)

        if boundary is None:
            return None

        cells: pv.UnstructuredGrid = self.groups.boundary(boundary)

        if self.frame.model_dim == 2:
            points: FloatArray = np.asarray(cells.points, dtype=np.float64)
            points[:, 2] = 0.0
            cells.points = points

        return cells

    def render(self, plotter: pv.Plotter) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context; proven by the gated glyph tests
        """Draw every body plain grey and shaded, a solid run's feature edges in black over them, then the glyph layer."""
        for name in (str(key) for key in self.blocks.keys()):
            surface: pv.PolyData = cast("pv.PolyData", cast("pv.DataSet", self.blocks[name]).extract_surface(algorithm=None))
            surface.clear_data()
            _ = plotter.add_mesh(  # pyright: ignore[reportUnknownMemberType]
                surface,
                color=PLAIN_BODY,
                smooth_shading=True,
                split_sharp_edges=True,
                specular=PLAIN_SPECULAR,
                show_scalar_bar=False,
                reset_camera=False,
                name=SceneActor.STYLED.of(name),
            )

        edges: pv.PolyData | None = feature_outline(self.blocks) if self.frame.model_dim == 3 else None

        if edges is not None:
            _ = plotter.add_mesh(edges, color="black", line_width=OUTLINE_LINE_PX, reset_camera=False, name=str(SceneActor.OUTLINE))  # pyright: ignore[reportUnknownMemberType]

        if self.glyphs is not None:
            self.glyphs.update(plotter, self, self.glyph_view)
