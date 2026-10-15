"""Field display state for a solved run: which array is colored, how it reduces to a scalar, and the color range that follows; pyvista is imported lazily inside the render half."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Callable, Iterable, Mapping, Sequence, cast

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from femforge.common.files import BundleFile
from femforge.common.log import log
from femforge.common.utils import title
from femforge.run.fields import GEOMETRY_IDS_ARRAY, select_array
from femforge.viz.colormaps import register_paraview
from femforge.viz.field_lines import (
    FieldLineOptions,
    StreamlineActor,
    StreamlineActors,
    build_streamlines,
    draw_streamlines,
    integrates_like,
    prepare_streamline_source,
    ramp_alpha,
)


if TYPE_CHECKING:
    import pyvista as pv

    from femforge.run.entities import Bodies
    from femforge.run.inspect import Run


type FloatArray = np.typing.NDArray[np.float64]
type Float32Array = np.typing.NDArray[np.float32]
type IntArray = np.typing.NDArray[np.int64]
type AnyArray = np.typing.NDArray[np.generic]
type Colormapper = Callable[[FloatArray], FloatArray]
type Colorscale = list[list[float | str]]
type TraceDict = dict[str, object]

SCALARS_ARRAY = "display_scalars"
"""The point/cell array every reduction is written to, so one mapper name serves every field and mode."""

FIELD_RENAMES: dict[str, str] = {"Jfix": "Fixed Current Density"}
"""Display-name overrides for solver array names that title-casing alone leaves cryptic."""

FEATURE_ANGLE = 30.0
"""Dihedral angle (degrees) above which a surface edge counts as a feature edge in the black wireframe."""

LINES_SUFFIX = " Lines"
"""Follows the field name in the field lines' scalar bar title, so it never takes a field bar's title (no ``|B|``: VTK's off-screen text renderer crashes on a pipe glyph)."""

POLAR_BLEND = 0.25
"""Fraction of the raw normalized magnitude kept when blending it with the sigmoid ramp in the polar RGBA."""

POLAR_STEEPNESS = 10.0
"""Slope of the sigmoid that turns a normalized polar magnitude into brightness, centered on the mid-range."""


class Association(StrEnum):
    """Whether a VTU array lives on mesh points or cells (pyvista's ``preference``)."""

    POINT = "point"
    """One value per mesh point."""

    CELL = "cell"
    """One value per mesh cell."""

    @property
    def intensitymode(self) -> str:
        """The plotly ``Mesh3d.intensitymode`` name for this association."""
        return "vertex" if self is Association.POINT else "cell"


class DisplayMode(StrEnum):
    """How a field array is reduced to the value that drives the color."""

    VALUE = "value"
    """The value itself."""

    ABSVALUE = "absolute_value"
    """The absolute value."""

    X = "x"
    """The x component."""

    Y = "y"
    """The y component."""

    Z = "z"
    """The z component."""

    MAGNITUDE = "magnitude"
    """The vector magnitude."""

    POLAR_XY = "polar_xy"
    """The in-plane direction as hue, brightened by the in-plane magnitude."""

    @property
    def label(self) -> str:
        """The mode's name as it reads in a picker."""
        return title(self.value)


VECTOR_MODES: frozenset[DisplayMode] = frozenset({DisplayMode.X, DisplayMode.Y, DisplayMode.Z, DisplayMode.MAGNITUDE, DisplayMode.POLAR_XY})
"""The modes that need a 3-component array; the rest accept any width."""


class SceneActor(StrEnum):
    """The plotter actor names a [`FieldScene`][] registers, so a toggle removes exactly one."""

    BODIES = "field-bodies"
    """The colored field over the shown bodies."""

    OUTLINE = "field-outline"
    """The feature-edge outline of the bodies."""

    STYLED = "field-styled"
    """The prefix of a styled body's own actor, followed by the body name."""

    def of(self, body: str) -> str:
        """The actor name of one body under this prefix."""
        return f"{self}:{body}"


@dataclass(frozen=True)
class FieldInfo:
    """One displayable VTU array: where it lives, how wide it is, and which display modes it supports."""

    name: str
    """The array's name in the VTU."""

    association: Association
    """Whether the array lives on points or cells."""

    components: int
    """The number of components per value: 1 for a scalar, 3 for a vector."""

    dtype: str
    """The array's numpy dtype name."""

    @property
    def display_name(self) -> str:
        """The array's name as it reads in a picker, with the [`FIELD_RENAMES`][] overrides applied."""
        label: str = title(self.name)

        return FIELD_RENAMES.get(label, label)

    @property
    def modes(self) -> tuple[DisplayMode, ...]:
        """The display modes this array supports, the default first: the vector set for a 3-component field, the scalar pair otherwise."""
        if self.components == 3:
            return (DisplayMode.MAGNITUDE, DisplayMode.X, DisplayMode.Y, DisplayMode.Z, DisplayMode.POLAR_XY)

        return (DisplayMode.VALUE, DisplayMode.ABSVALUE)


def field_infos(point_arrays: Mapping[str, AnyArray], cell_arrays: Mapping[str, AnyArray]) -> list[FieldInfo]:
    """Every displayable array of a VTU as a [`FieldInfo`][], ``GeometryIds`` excluded, sorted by display name."""
    infos: list[FieldInfo] = []
    for association, arrays in ((Association.POINT, point_arrays), (Association.CELL, cell_arrays)):
        for name, values in arrays.items():
            if name == GEOMETRY_IDS_ARRAY:
                continue

            array: AnyArray = np.asarray(values)
            shape: tuple[int, ...] = array.shape
            components: int = shape[-1] if array.ndim > 1 else 1
            infos.append(FieldInfo(name=name, association=association, components=components, dtype=str(array.dtype)))

    return sorted(infos, key=lambda info: info.display_name)


class BodyPaint(StrEnum):
    """Where a body's color comes from."""

    FIELD = "field"
    """The field colormap, by the displayed value."""

    COLORMAP = "colormap"
    """One color of the body colormap, spread evenly over every body that takes it."""

    SOLID = "solid"
    """The body's own ``color``."""


class BodyStyle(BaseModel):
    """A body's own look: where its color comes from, a solid color when it has one, and its opacity."""

    model_config = ConfigDict(use_attribute_docstrings=True, frozen=True)

    body: str
    """The body's ``entities.json`` name."""

    paint: BodyPaint = BodyPaint.FIELD
    """Where the body's color comes from."""

    color: str | None = None
    """Any color pyvista resolves (a name or a hex triplet); a SOLID body needs one, the others ignore it."""

    @model_validator(mode="after")
    def _solid_has_color(self) -> BodyStyle:
        if self.paint is BodyPaint.SOLID and self.color is None:
            raise ValueError(f"{self.body}: a solid body needs a color")

        return self

    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    """`[-]`: The body's own opacity, multiplied by the display's."""


class DisplaySpec(BaseModel):
    """The complete, serializable state of one field display: which array is colored, how it reduces, and how the colors map."""

    model_config = ConfigDict(use_attribute_docstrings=True, validate_assignment=True)

    field: str = ""
    """Name of the VTU array to color by; empty means "whichever the run offers first"."""

    mode: DisplayMode = DisplayMode.VALUE
    """How the array is reduced to the displayed value."""

    colormap: str = "viridis"
    """Colormap name, resolved through pyvista's ``get_cmap_safe`` (matplotlib and colorcet names both work)."""

    log_scale: bool = False
    """Map the color range logarithmically; only the strictly positive values take part."""

    invert: bool = False
    """Reverse the colormap."""

    clim: tuple[float, float] | None = None
    """`[?]`: Pinned color limits in the displayed value's unit, or None to recompute them from the data on every apply."""

    robust: bool = True
    """Clamp the automatic limits to the 2-98 percentiles so a few outlier cells cannot flatten the picture."""

    hidden_bodies: tuple[str, ...] = ()
    """Body names to hide, by their ``entities.json`` name."""

    outline: bool = True
    """Draw the black feature-edge wireframe over the visible bodies."""

    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    """`[-]`: Multiplies every body's own opacity."""

    opacity_map: tuple[Annotated[float, Field(ge=0.0, le=1.0)], Annotated[float, Field(ge=0.0, le=1.0)]] | None = None
    """`[-]`: The opacity at the low and high color limits, linear in the color mapping between them; None draws ``opacity`` throughout."""

    body_styles: tuple[BodyStyle, ...] = ()
    """Bodies with a color source or an opacity of their own; the rest take the field colormap at full opacity."""

    body_colormap: str = "batlow"
    """The colormap the COLORMAP bodies share, in ``body_styles`` order."""

    def solid(self) -> dict[str, str]:
        """The color of every body not on the field colormap, by body: its own, or its share of the body colormap."""
        spread: list[str] = [style.body for style in self.body_styles if style.paint is BodyPaint.COLORMAP]
        shares: dict[str, str] = dict(zip(spread, spread_colors(self.body_colormap, len(spread)), strict=True)) if spread else {}
        colors: dict[str, str] = {}
        for style in self.body_styles:
            if style.paint is BodyPaint.COLORMAP:
                colors[style.body] = shares[style.body]
            elif style.paint is BodyPaint.SOLID and style.color is not None:
                colors[style.body] = style.color

        return colors

    def body_opacity(self, body: str) -> float:
        """A body's drawn opacity: its own times the overall one."""
        own: float = next((style.opacity for style in self.body_styles if style.body == body), 1.0)
        return own * self.opacity

    def unmatched(self, bodies: Iterable[str]) -> list[str]:
        """The ``hidden_bodies`` and ``body_styles`` names missing from ``bodies``, in spec order."""
        present: set[str] = set(bodies)
        named: list[str] = [*self.hidden_bodies, *(style.body for style in self.body_styles)]
        return [name for name in dict.fromkeys(named) if name not in present]

    streamlines: FieldLineOptions | None = Field(default=None)
    """Options for the field-line overlay, or None to draw none."""

    def fitted(self, fields: Sequence[FieldInfo]) -> DisplaySpec:
        """
        This spec snapped to what a run actually offers: an unset field takes the first available one and a mode the field disallows falls back to its first allowed mode.

        An unknown field name raises [`KeyError`][] naming the available arrays.
        """
        if not fields:
            raise KeyError("no displayable fields")

        by_name: dict[str, FieldInfo] = {info.name: info for info in fields}
        chosen: FieldInfo = fields[0] if not self.field else by_name[select_array(by_name, self.field)]

        allowed: tuple[DisplayMode, ...] = chosen.modes
        mode: DisplayMode = self.mode if self.mode in allowed else allowed[0]
        if chosen.name == self.field and mode is self.mode:
            return self

        return self.model_copy(update={"field": chosen.name, "mode": mode})


def reduce_field(values: AnyArray, mode: DisplayMode) -> FloatArray:
    """
    The scalar a display mode draws from one field array.

    ``VALUE`` passes the array through and ``POLAR_XY`` returns the XY magnitude (the quantity that sets the color range; the angle rides along as hue).
    """
    array: FloatArray = np.asarray(values, dtype=np.float64)
    if mode in VECTOR_MODES and (array.ndim < 2 or array.shape[-1] != 3):
        raise ValueError(f"display mode {mode.value!r} needs 3-component vectors, got shape {array.shape}")

    match mode:
        case DisplayMode.VALUE:
            return array
        case DisplayMode.ABSVALUE:
            return np.abs(array)
        case DisplayMode.X:
            return array[..., 0]
        case DisplayMode.Y:
            return array[..., 1]
        case DisplayMode.Z:
            return array[..., 2]
        case DisplayMode.MAGNITUDE:
            return np.linalg.norm(array, axis=-1)
        case DisplayMode.POLAR_XY:
            return np.linalg.norm(array[..., :2], axis=-1)


def polar_angle(values: AnyArray) -> FloatArray:
    """Each vector's XY direction as a cyclic coordinate in ``[0, 1]`` — ``atan2(y, x)`` mapped off ``[-pi, pi]``."""
    array: FloatArray = np.asarray(values, dtype=np.float64)
    if array.ndim < 2 or array.shape[-1] < 2:
        raise ValueError(f"polar angle needs at least 2 components, got shape {array.shape}")

    radians: FloatArray = np.arctan2(array[..., 1], array[..., 0])
    return (radians + np.pi) / (2.0 * np.pi)


def polar_rgba(values: AnyArray, lo: float, hi: float, cmap: Colormapper) -> Float32Array:
    """Per-vector RGBA for [`DisplayMode.POLAR_XY`][]: hue from the XY angle through a cyclic ``cmap``, brightness and alpha from the sigmoid-scaled XY magnitude.

    Args:
        values: The ``(N, 3)`` vectors.
        lo: The XY magnitude at the dark end.
        hi: The XY magnitude at the bright end.
        cmap: The cyclic colormap the XY angle maps through.
    """
    array: FloatArray = np.asarray(values, dtype=np.float64)
    magnitude: FloatArray = reduce_field(array, DisplayMode.POLAR_XY)

    # A degenerate range would divide by zero; a unit span leaves the magnitudes where they already are.
    span: float = hi - lo if hi > lo else 1.0
    normalized: FloatArray = np.clip((magnitude - lo) / span, 0.0, 1.0)
    sigmoid: FloatArray = 1.0 / (1.0 + np.exp(-POLAR_STEEPNESS * (normalized - 0.5)))
    scaled: FloatArray = POLAR_BLEND * normalized + (1.0 - POLAR_BLEND) * sigmoid

    hue: FloatArray = np.asarray(cmap(polar_angle(array)), dtype=np.float64)
    rgba: Float32Array = np.zeros((array.shape[0], 4), dtype=np.float32)
    rgba[..., 0:3] = np.clip(hue[..., :3] * scaled[..., np.newaxis], 0.0, 1.0)
    rgba[..., 3] = scaled
    return rgba


def color_range(chunks: Iterable[AnyArray], *, log_scale: bool = False, robust: bool = True) -> tuple[float, float]:
    """The ``(lo, hi)`` color limits spanning every scalar chunk's own range, from the finite values only; an empty selection gives ``(0.0, 1.0)``.

    Args:
        chunks: The scalar arrays, one per body.
        log_scale: Keep only the strictly positive values and start at their 1st percentile.
        robust: Clamp each chunk to its 2-98 percentiles, so a small body keeps its range beside a large one.
    """

    def bounds(chunk: AnyArray) -> tuple[float, float] | None:
        values: FloatArray = np.asarray(chunk, dtype=np.float64).ravel()
        finite: FloatArray = values[np.isfinite(values)]
        usable: FloatArray = finite[finite > 0.0] if log_scale else finite
        if usable.size == 0:
            return None

        if log_scale:
            return (float(np.percentile(usable, 1.0)), float(np.max(usable)))

        if robust:
            return (float(np.percentile(usable, 2.0)), float(np.percentile(usable, 98.0)))

        return (float(np.min(usable)), float(np.max(usable)))

    ranges: list[tuple[float, float]] = [found for found in (bounds(chunk) for chunk in chunks) if found is not None]
    if not ranges:
        return (0.0, 1.0)

    lo: float = min(low for low, _ in ranges)
    hi: float = max(high for _, high in ranges)

    def widened(low: float, high: float) -> tuple[float, float]:
        """A single-valued range nudged apart so the colormap has somewhere to map."""
        if high > low:
            return (low, high)

        if log_scale:
            return (low / 2.0, high * 2.0)

        pad: float = abs(high) if high != 0.0 else 1.0
        return (low - pad, high + pad)

    return widened(lo, hi)


def camera_needs_reset(position: Sequence[float], focal: Sequence[float], bounds: Sequence[float] | None, *, half_angle_deg: float = 30.0) -> bool:
    """Whether the camera has lost the model, with the box behind it or outside its view cone; a camera inside the box always keeps its view.

    Args:
        position: The camera position.
        focal: The camera's focal point.
        bounds: The model box as ``(xmin, xmax, ymin, ymax, zmin, zmax)``; missing or non-finite bounds always re-frame.
        half_angle_deg: The half angle of the view cone some box corner must fall inside, in degrees.
    """
    if bounds is None:
        return True

    box: list[float] = [float(value) for value in bounds]
    if not all(math.isfinite(value) for value in box):
        return True

    eye: FloatArray = np.asarray([float(value) for value in position], dtype=np.float64)
    view: FloatArray = np.asarray([float(value) for value in focal], dtype=np.float64) - eye
    length: float = float(np.linalg.norm(view))
    if length == 0.0:
        return True

    xs, ys, zs = box[0:2], box[2:4], box[4:6]
    lower: FloatArray = np.asarray([xs[0], ys[0], zs[0]], dtype=np.float64)
    upper: FloatArray = np.asarray([xs[1], ys[1], zs[1]], dtype=np.float64)
    if bool(np.all((eye >= lower) & (eye <= upper))):
        return False

    corners: FloatArray = np.asarray([(x, y, z) for x in xs for y in ys for z in zs], dtype=np.float64)
    offsets: FloatArray = corners - eye
    along: FloatArray = offsets @ (view / length)
    if float(np.max(along)) <= 0.0:
        return True

    distances: FloatArray = np.linalg.norm(offsets, axis=1)
    cosines: FloatArray = np.divide(along, distances, out=np.zeros_like(along), where=distances > 0.0)
    return not bool(np.any(cosines >= math.cos(math.radians(half_angle_deg))))


def plotly_colorscale(samples: AnyArray, *, invert: bool = False) -> Colorscale:
    """A plotly colorscale from sampled RGB(A) rows: evenly-spaced stops paired with ``"rgb(r,g,b)"`` strings."""
    rows: FloatArray = np.asarray(samples, dtype=np.float64)
    if rows.ndim != 2 or rows.shape[1] not in (3, 4):
        raise ValueError(f"colormap samples must be (N, 3) or (N, 4), got shape {rows.shape}")

    if rows.shape[0] < 2:
        raise ValueError(f"a colorscale needs at least 2 samples, got {rows.shape[0]}")

    ordered: FloatArray = rows[::-1] if invert else rows
    channels: IntArray = np.clip(ordered[:, :3] * 255.0, 0.0, 255.0).round().astype(np.int64)
    triplets: list[list[int]] = cast("list[list[int]]", channels.tolist())

    last: int = len(triplets) - 1
    scale: Colorscale = []
    for index, (red, green, blue) in enumerate(triplets):
        scale.append([index / last, f"rgb({red},{green},{blue})"])

    return scale


def log_colorbar_ticks(lo: float, hi: float) -> tuple[list[float], list[str]]:
    """Decade tick positions spanning a log color range, with their ``1e``-notation labels."""
    if lo <= 0.0 or hi <= 0.0:
        raise ValueError(f"log colorbar ticks need positive bounds, got ({lo}, {hi})")

    first: int = math.floor(math.log10(min(lo, hi)))
    last: int = math.ceil(math.log10(max(lo, hi)))
    exponents: list[int] = list(range(first, last + 1))
    return ([10.0**exponent for exponent in exponents], [f"1e{exponent}" for exponent in exponents])


def log_intensity(values: AnyArray, floor: float) -> FloatArray:
    """``log10`` of the values with everything at or below ``floor`` clamped to it, so a plotly colorscale can map a log range linearly."""
    if floor <= 0.0:
        raise ValueError(f"a log intensity needs a positive floor, got {floor}")

    array: FloatArray = np.asarray(values, dtype=np.float64)
    return np.log10(np.maximum(array, floor))


def nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    """Round-number tick positions inside ``[lo, hi]``: 1, 2 or 5 times a power of ten, spaced to give about ``count`` of them.

    Args:
        lo: The low end of the range.
        hi: The high end of the range.
        count: The rough number of ticks wanted.
    """
    if hi <= lo:
        raise ValueError(f"nice ticks need lo < hi, got ({lo}, {hi})")

    rough: float = (hi - lo) / count
    magnitude: float = 10.0 ** math.floor(math.log10(rough))
    residual: float = rough / magnitude
    factor: float = 1.0 if residual < 1.5 else 2.0 if residual < 3.5 else 5.0 if residual < 7.5 else 10.0
    step: float = factor * magnitude

    first: int = math.ceil(lo / step)
    last: int = math.floor(hi / step)
    return [index * step for index in range(first, last + 1)]


def log_colorbar(lo: float, hi: float) -> TraceDict:
    """The plotly ``colorbar`` for a log-scaled intensity: decade ticks at their ``log10`` positions, or round-number ticks when fewer than two decades fall inside the range."""
    ticks, labels = log_colorbar_ticks(lo, hi)
    inside: list[tuple[float, str]] = [(tick, label) for tick, label in zip(ticks, labels, strict=True) if lo <= tick <= hi]
    if len(inside) < 2:
        inside = [(tick, f"{tick:g}") for tick in nice_ticks(lo, hi) if tick > 0.0]

    return {"tickvals": [math.log10(tick) for tick, _ in inside], "ticktext": [label for _, label in inside]}


def mesh3d_traces(
    points: FloatArray,
    triangles: IntArray,
    intensity: FloatArray | None,
    *,
    name: str,
    color: str | None = None,
    opacity: float = 1.0,
    intensitymode: Association = Association.POINT,
    colorscale: Colorscale | None = None,
    cmin: float | None = None,
    cmax: float | None = None,
    showscale: bool = False,
    hovertemplate: str | None = None,
    customdata: FloatArray | None = None,
    colorbar: TraceDict | None = None,
    visible: bool = True,
) -> list[TraceDict]:
    """One plotly ``Mesh3d`` trace as a plain JSON dict: a body's triangulated surface colored by ``intensity``, or by ``color`` alone.

    Args:
        points: The ``(N, 3)`` vertex coordinates.
        triangles: The ``(M, 3)`` vertex indices of each triangle.
        intensity: The values the colorscale maps, per vertex or per triangle; None colors by ``color`` alone.
        name: The legend name.
        color: A solid color for a body without an intensity.
        opacity: The trace opacity.
        intensitymode: Whether ``intensity`` lives on the vertices or the triangles.
        colorscale: The plotly colorscale ``intensity`` maps through.
        cmin: The intensity at the low end of the colorscale.
        cmax: The intensity at the high end of the colorscale.
        showscale: Draw the colorbar.
        hovertemplate: The plotly hover text template.
        customdata: Per-vertex values the hover template reads.
        colorbar: The plotly colorbar settings.
        visible: Draw the body; a hidden one stays in the legend as ``legendonly``.
    """
    trace: TraceDict = {
        "type": "mesh3d",
        "name": name,
        "x": points[:, 0].tolist(),
        "y": points[:, 1].tolist(),
        "z": points[:, 2].tolist(),
        "i": triangles[:, 0].tolist(),
        "j": triangles[:, 1].tolist(),
        "k": triangles[:, 2].tolist(),
        "intensity": intensity.tolist() if intensity is not None else None,
        "intensitymode": intensitymode.intensitymode,
        "color": color,
        "opacity": opacity,
        "colorscale": colorscale,
        "cmin": cmin,
        "cmax": cmax,
        "showscale": showscale,
        "colorbar": colorbar,
        "flatshading": True,
        "hovertemplate": hovertemplate,
        "customdata": customdata.tolist() if customdata is not None else None,
        "visible": True if visible else "legendonly",
        "showlegend": True,
    }
    return [trace]


def outline_traces(segments: FloatArray, *, name: str = "outline", color: str = "black") -> list[TraceDict]:
    """One plotly ``Scatter3d`` lines trace drawing every ``(N, 2, 3)`` segment, NaN-separated so the segments stay disjoint.

    Args:
        segments: The ``(N, 2, 3)`` line segments.
        name: The trace name.
        color: The line color.
    """
    if segments.size == 0:
        return []

    padded: FloatArray = np.full((segments.shape[0], 3, 3), np.nan, dtype=np.float64)
    padded[:, 0:2, :] = segments
    flat: FloatArray = padded.reshape(-1, 3)

    trace: TraceDict = {
        "type": "scatter3d",
        "mode": "lines",
        "name": name,
        "x": flat[:, 0].tolist(),
        "y": flat[:, 1].tolist(),
        "z": flat[:, 2].tolist(),
        "line": {"color": color, "width": 2},
        "hoverinfo": "skip",
        "showlegend": False,
    }
    return [trace]


def list_fields(grid: pv.DataSet) -> list[FieldInfo]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """Every displayable field of a VTU grid (``GeometryIds`` excluded), sorted by display name."""
    point_arrays: dict[str, AnyArray] = {name: np.asarray(grid.point_data[name]) for name in grid.point_data.keys()}
    cell_arrays: dict[str, AnyArray] = {name: np.asarray(grid.cell_data[name]) for name in grid.cell_data.keys()}
    return field_infos(point_arrays, cell_arrays)


def split_by_body(grid: pv.UnstructuredGrid, bodies: Bodies) -> pv.MultiBlock:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """The grid split into one block per named body, keyed by its ``entities.json`` name via the ``GeometryIds`` cell array."""
    import pyvista as pv

    ids: IntArray = np.asarray(grid.cell_data[GEOMETRY_IDS_ARRAY], dtype=np.int64)
    blocks: pv.MultiBlock = pv.MultiBlock()
    for body in bodies:
        mask = cast("np.typing.NDArray[np.bool_]", ids == body.mesh_id)
        if not bool(np.any(mask)):
            continue

        block = cast("pv.UnstructuredGrid", grid.extract_cells(mask))  # pyright: ignore[reportUnknownMemberType]  # pyvista's extract_cells index parameter is partially untyped
        blocks.append(block, name=body.name)

    return blocks


def feature_outline(blocks: pv.MultiBlock) -> pv.PolyData | None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """The feature and boundary edges of a multiblock's outer surface — the black wireframe drawn over the field, the body outlines on a 2D slice — or None when the surface has none."""
    import pyvista as pv

    if blocks.is_empty:
        return None

    surface: object = blocks.extract_surface(algorithm=None)
    if not isinstance(surface, pv.PolyData) or surface.n_points == 0:
        return None

    edges: pv.PolyData = surface.extract_feature_edges(
        boundary_edges=True,
        feature_edges=True,
        manifold_edges=False,
        non_manifold_edges=False,
        feature_angle=FEATURE_ANGLE,
        clear_data=True,
    )
    if edges.n_points == 0:
        return None

    return edges


def model_dim_of(grid: pv.DataSet) -> int:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """2 for a flat (axisymmetric slice) grid with no Z extent, else 3."""
    bounds: Sequence[float] = grid.bounds
    return 2 if bounds[5] - bounds[4] <= 0.0 else 3


def spread_colors(name: str, count: int) -> list[str]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """``count`` hex colors evenly spread through a colormap, each at the middle of its share so none sits on a dark or light end."""
    samples: list[list[float]] = cast("list[list[float]]", sample_colormap(name, 2 * count + 1).tolist())
    return [f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}" for r, g, b, *_ in samples[1::2]]


def sample_colormap(name: str, count: int = 256) -> FloatArray:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """``count`` evenly-spaced RGBA rows from a named colormap, resolved through pyvista's ``get_cmap_safe``."""
    import pyvista as pv

    register_paraview()
    cmap = pv.get_cmap_safe(name)  # pyright: ignore[reportUnknownMemberType]  # pyvista returns an untyped matplotlib Colormap
    return np.asarray(cmap(np.linspace(0.0, 1.0, count)), dtype=np.float64)


def surface_triangles(block: pv.DataSet) -> tuple[FloatArray, IntArray]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """A block's outer surface as ``(points, triangles)``: an ``(N, 3)`` coordinate array and an ``(M, 3)`` vertex-index array."""
    surface: pv.PolyData = cast("pv.PolyData", cast("pv.PolyData", block.extract_surface(algorithm=None)).triangulate())
    # A triangulated PolyData's `faces` is a flat [3, a, b, c, ...] stream; the leading count column is dropped.
    faces: IntArray = np.asarray(surface.faces, dtype=np.int64).reshape(-1, 4)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # pyvista's faces property is untyped
    return (np.asarray(surface.points, dtype=np.float64), faces[:, 1:])


def outline_segments(edges: pv.PolyData) -> FloatArray:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """The feature edges as an ``(N, 2, 3)`` array of line segments."""
    points: FloatArray = np.asarray(edges.points, dtype=np.float64)
    lines: IntArray = np.asarray(edges.lines, dtype=np.int64).reshape(-1, 3)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # pyvista's lines property is untyped
    return points[lines[:, 1:]]


class FieldScene:
    """
    One run's solved bodies as a recolorable field display.

    It is a [`Renderable`][femforge.viz.scene.Renderable] for the pyvista path and the trace source for the 2D plotly path; both read the same [`DisplaySpec`][].
    """

    def __init__(
        self, *, grid: pv.UnstructuredGrid, blocks: pv.MultiBlock, fields: Sequence[FieldInfo], model_dim: int, run_id: str, bodies: Bodies | None = None
    ) -> None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """Hold one run's solved grid, split into bodies, under a default display.

        Args:
            grid: The solved VTU grid.
            blocks: The grid split into one block per named body.
            fields: The displayable arrays.
            model_dim: 2 for an axisymmetric slice, else 3.
            run_id: The run bundle's name.
            bodies: The named bodies from ``entities.json``; None names none.
        """
        from femforge.run.entities import Bodies

        register_paraview()
        self.grid: pv.UnstructuredGrid = grid
        self.blocks: pv.MultiBlock = blocks
        self.fields: list[FieldInfo] = list(fields)
        self.model_dim: int = model_dim
        self.run_id: str = run_id
        self.bodies: Bodies = bodies if bodies is not None else Bodies([])
        self.spec: DisplaySpec = DisplaySpec().fitted(self.fields) if self.fields else DisplaySpec()
        self.clim: tuple[float, float] = (0.0, 1.0)
        self.data_range: tuple[float, float] = (0.0, 1.0)
        self._actor: pv.Actor | None = None
        self._styled: dict[str, tuple[str, float]] = {}
        """The ``(color, opacity)`` of every solid body actor the plotter holds."""
        self._lines: tuple[StreamlineActors, FieldLineOptions] | None = None
        """The last integrated field lines and the options they were integrated with, restyled without integrating again."""
        self.drawn_lines: FieldLineOptions | None = None
        """The styled options of the lines the plotter holds."""
        self._lines_bar: str | None = None
        """The title of the field lines' scalar bar the plotter holds."""

    @classmethod
    def from_run(cls, run: Run) -> FieldScene:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """Open a run bundle's VTU as a field scene, its bodies split and named from ``entities.json``."""
        import pyvista as pv

        if run.vtu_path is None:
            raise FileNotFoundError(f"{run.path}: no VTU output to display")

        bodies: Bodies = run.bodies
        if not len(bodies):
            raise FileNotFoundError(f"{run.path}: no named bodies ({BundleFile.ENTITIES} missing); re-solve the run so the bundle carries its entities")

        grid: pv.UnstructuredGrid = cast("pv.UnstructuredGrid", pv.read(str(run.vtu_path)))
        blocks: pv.MultiBlock = split_by_body(grid, bodies)
        fields: list[FieldInfo] = list_fields(grid)

        log.bind(run_id=run.path.name, bodies=len(blocks), fields=len(fields)).info("Field scene loaded.")
        return cls(grid=grid, blocks=blocks, fields=fields, model_dim=model_dim_of(grid), run_id=run.path.name, bodies=bodies)

    def field_info(self, name: str) -> FieldInfo:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """The [`FieldInfo`][] for an array name, resolved the same way a field query resolves one."""
        by_name: dict[str, FieldInfo] = {info.name: info for info in self.fields}
        return by_name[select_array(by_name, name)]

    def apply(self, spec: DisplaySpec) -> tuple[float, float]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """Write the spec's reduction into every block's ``display_scalars`` array and return the color limits it produced."""
        import pyvista as pv

        fitted: DisplaySpec = spec.fitted(self.fields)
        info: FieldInfo = self.field_info(fitted.field)

        def raw_values(block: pv.DataSet) -> FloatArray:
            """One block's copy of the displayed array, zero-filled when the block never carried it."""
            arrays = block.point_data if info.association is Association.POINT else block.cell_data
            width: int = block.n_points if info.association is Association.POINT else block.n_cells
            if info.name not in arrays:
                return np.zeros((width, info.components), dtype=np.float64)

            return np.asarray(arrays[info.name], dtype=np.float64)

        names: list[str] = [str(key) for key in self.blocks.keys()]
        sources: list[FloatArray] = [raw_values(cast("pv.DataSet", self.blocks[name])) for name in names]
        reductions: list[AnyArray] = [reduce_field(values, fitted.mode) for values in sources]

        # Every body counts, each through its own range, whatever it is shown as: the scale belongs to the field, not the view.
        self.data_range = color_range(reductions, robust=False)
        clim: tuple[float, float] = fitted.clim if fitted.clim is not None else color_range(reductions, log_scale=fitted.log_scale, robust=fitted.robust)

        if fitted.mode is DisplayMode.POLAR_XY:
            cmap = pv.get_cmap_safe(fitted.colormap)  # pyright: ignore[reportUnknownMemberType]  # pyvista returns an untyped matplotlib Colormap

            def mapper(fractions: FloatArray) -> FloatArray:
                """RGBA rows for cyclic angle fractions, through the spec's colormap."""
                return np.asarray(cmap(fractions), dtype=np.float64)

            reductions = [polar_rgba(values, clim[0], clim[1], mapper) for values in sources]

        for name, scalars in zip(names, reductions, strict=True):
            block: pv.DataSet = cast("pv.DataSet", self.blocks[name])
            arrays = block.point_data if info.association is Association.POINT else block.cell_data
            arrays.set_scalars(scalars, name=SCALARS_ARRAY)  # pyright: ignore[reportUnknownMemberType]  # pyvista's set_scalars carries **kwargs: Unknown

        self.spec = fitted
        self.clim = clim
        return clim

    def render(self, plotter: pv.Plotter) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context; proven by the gated integration test
        """Draw the whole display into ``plotter``: the composite body actor, then everything [`update`][] owns; solid bodies an earlier scene left go first."""
        spec: DisplaySpec = self.spec
        _ = self.apply(spec)
        self._styled = {}
        self.drawn_lines = None

        actors = cast("Mapping[str, object]", plotter.renderer.actors)
        leftovers = [name for name in actors if name.startswith(SceneActor.STYLED.of(""))]
        for name in leftovers:
            _ = plotter.remove_actor(name)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista removes by actor name, and its _Wrapped stub loses `self`

        self._actor = plotter.add_mesh(  # pyright: ignore[reportUnknownMemberType]
            self.blocks,
            scalars=SCALARS_ARRAY,
            show_edges=False,
            show_scalar_bar=False,
            name=str(SceneActor.BODIES),
        )
        # Translucent bodies sort per fragment only under depth peeling; without it a far body can paint over a near one.
        _ = cast("object", plotter.enable_depth_peeling())  # pyright: ignore[reportCallIssue, reportUnknownMemberType]  # pyvista's _Wrapped stub loses `self`
        self._redraw(plotter, spec)

    def update(self, plotter: pv.Plotter, spec: DisplaySpec) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context; proven by the gated integration test
        """Recolor an already-rendered scene in place — the cheap path a panel calls on every knob change; field lines are re-integrated only when their options changed."""
        if self._actor is None:
            self.render(plotter)
            return

        self._redraw(plotter, spec)

    def _redraw(self, plotter: pv.Plotter, spec: DisplaySpec) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context; proven by the gated integration test
        """Everything but the body actor: the mapper, the scalar bar, the outline and the field lines."""
        import pyvista as pv

        assert self._actor is not None, "render() adds the body actor first"

        fitted: DisplaySpec = spec.fitted(self.fields)
        info: FieldInfo = self.field_info(fitted.field)
        clim: tuple[float, float] = self.apply(fitted)

        mapper = self._actor.mapper
        assert isinstance(mapper, pv.CompositePolyDataMapper), f"expected a CompositePolyDataMapper, got {type(mapper)}"

        mapper.color_missing_with_nan = True
        mapper.scalar_visibility = True
        mapper.array_name = SCALARS_ARRAY
        if fitted.mode is DisplayMode.POLAR_XY:
            # The reduction already wrote RGBA, so the mapper must pass it through rather than look it up.
            mapper.color_mode = "direct"
            mapper.scalar_map_mode = info.association.value
            mapper.use_lookup_table_scalar_range = True
            mapper.lookup_table.table_range = (0.0, 1.0)
        else:
            mapper.color_mode = "map"
            mapper.use_lookup_table_scalar_range = False
            mapper.set_scalars(  # pyright: ignore[reportUnknownMemberType]
                scalars_name=SCALARS_ARRAY,
                preference=info.association.value,
                component=None,
                annotations=None,
                rgb=False,
                scalar_bar_args={},
                n_colors=1024,
                nan_color=None,
                above_color=None,
                below_color=None,
                clim=clim,
                cmap=fitted.colormap,
                flip_scalars=fitted.invert,
                log_scale=fitted.log_scale,
            )
            if fitted.opacity_map is not None:
                # The table runs from the low limit to the high one whatever `invert` did to its colors.
                ramp_alpha(mapper.lookup_table, *fitted.opacity_map)

        solid: dict[str, str] = {body: color for body, color in fitted.solid().items() if body not in fitted.hidden_bodies}
        for index, name in enumerate(str(key) for key in self.blocks.keys()):
            mapper.block_attr[index + 1].visible = name not in fitted.hidden_bodies and name not in solid
            mapper.block_attr[index + 1].opacity = fitted.body_opacity(name)

        painted: bool = any(name not in fitted.hidden_bodies and name not in solid for name in (str(key) for key in self.blocks.keys()))
        self._update_scalar_bar(plotter, mapper, info, fitted if painted else None)
        self._update_styled(plotter, {body: (color, fitted.body_opacity(body)) for body, color in solid.items()})
        self._update_outline(plotter, fitted)
        self._update_streamlines(plotter, fitted)

    def _update_styled(self, plotter: pv.Plotter, styled: Mapping[str, tuple[str, float]]) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context
        """Give every solid body an actor of its own in its ``(color, opacity)``, replacing only the ones that changed and removing the ones dropped."""
        for name in [name for name in self._styled if name not in styled]:
            _ = plotter.remove_actor(SceneActor.STYLED.of(name))  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista removes by actor name, and its _Wrapped stub loses `self`
            del self._styled[name]

        for name, style in styled.items():
            if self._styled.get(name) == style or name not in self.blocks.keys():
                continue

            block = cast("pv.DataSet", self.blocks[name])
            surface: pv.PolyData = cast("pv.PolyData", block.extract_surface(algorithm=None))
            surface.clear_data()
            color, opacity = style
            _ = plotter.add_mesh(  # pyright: ignore[reportUnknownMemberType]
                surface,
                color=color,
                opacity=opacity,
                show_scalar_bar=False,
                name=SceneActor.STYLED.of(name),
            )
            self._styled[name] = style

    def shown_blocks(self, hidden: Iterable[str]) -> pv.MultiBlock:  # pragma: no cover - needs pyvista (viz extra)
        """The blocks of every body not in ``hidden``, under their names."""
        import pyvista as pv

        skipped: set[str] = set(hidden)
        shown: pv.MultiBlock = pv.MultiBlock()
        for name in (str(key) for key in self.blocks.keys()):
            if name not in skipped:
                shown.append(self.blocks[name], name=name)

        return shown

    def _update_scalar_bar(
        self, plotter: pv.Plotter, mapper: pv.CompositePolyDataMapper, info: FieldInfo, spec: DisplaySpec | None
    ) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context
        """Re-add the scalar bar under the current field's title (pyvista keys bars by title), or none for a None spec: no visible body takes the colormap."""
        bars: Mapping[str, object] = plotter.scalar_bars  # pyright: ignore[reportAny]  # pyvista's ScalarBars container is untyped
        for name in [name for name in bars.keys() if name != self._lines_bar]:
            plotter.remove_scalar_bar(name, render=False)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista's _Wrapped stub loses `self`

        if spec is None or spec.mode is DisplayMode.POLAR_XY:
            return

        # Upright at the right, inset so the centred title stays on screen: a horizontal bar centres its title over the middle tick label, which a large title font overlaps.
        _ = cast(
            "object",
            plotter.add_scalar_bar(  # pyright: ignore[reportCallIssue, reportUnknownMemberType]  # pyvista's _Wrapped stub loses `self`
                title=info.display_name,
                mapper=mapper,
                vertical=True,
                position_x=0.86,
                position_y=0.2,
                width=0.035,
                height=0.6,
            ),
        )

    def _update_outline(self, plotter: pv.Plotter, spec: DisplaySpec) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context
        """Add or remove the black feature-edge wireframe of the visible bodies, by its own actor name."""
        edges: pv.PolyData | None = feature_outline(self.shown_blocks(spec.hidden_bodies)) if spec.outline else None
        if edges is None:
            _ = plotter.remove_actor(str(SceneActor.OUTLINE))  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista removes by actor name, and its _Wrapped stub loses `self`
            return

        _ = plotter.add_mesh(edges, color="black", line_width=2.0, name=str(SceneActor.OUTLINE))  # pyright: ignore[reportUnknownMemberType]

    def _update_streamlines(self, plotter: pv.Plotter, spec: DisplaySpec) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context
        """Draw the field-line overlay when it or the display it is styled by changed, integrating only new lines, or remove it when the spec turns it off."""
        if spec.streamlines is None:
            for actor in StreamlineActor:
                _ = plotter.remove_actor(str(actor))  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista removes by actor name, and its _Wrapped stub loses `self`
            self._remove_lines_bar(plotter)
            self.drawn_lines = None
            return

        cached: StreamlineActors | None = self.integrated(spec.streamlines)
        if cached is not None and self.line_style(spec.streamlines, cached) == self.drawn_lines:
            return

        self.show_streamlines(plotter, cached or self.integrate_streamlines(spec.streamlines), spec.streamlines)

    def integrated(self, options: FieldLineOptions) -> StreamlineActors | None:
        """The cached field lines when ``options`` only restyle them, None when they need integrating."""
        if self._lines is None or not integrates_like(self._lines[1], options):
            return None

        return self._lines[0]

    def integrate_streamlines(self, options: FieldLineOptions) -> StreamlineActors:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """Seed and integrate the field lines through this scene's grid — the compute half, safe off the UI thread."""
        source, b_name = prepare_streamline_source(self.grid, options)
        actors: StreamlineActors = build_streamlines(source, b_name, options)
        return replace(actors, magnitudes=self.region_magnitudes(b_name, options.region_id))

    def region_magnitudes(self, name: str, region_id: int | None) -> list[FloatArray]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """The magnitudes of array ``name`` per body of a region (every body for None), the chunks a field-line range spans."""

        chunks: list[FloatArray] = []
        for block_name in (str(key) for key in self.blocks.keys()):
            body = self.bodies.get(block_name)
            if region_id is not None and (body is None or body.mesh_id != region_id):
                continue

            block: pv.DataSet = cast("pv.DataSet", self.blocks[block_name])
            arrays = block.cell_data if name in block.cell_data else block.point_data
            if name in arrays:
                chunks.append(reduce_field(np.asarray(arrays[name]), DisplayMode.MAGNITUDE))

        return chunks

    def show_streamlines(
        self, plotter: pv.Plotter, actors: StreamlineActors, options: FieldLineOptions
    ) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context; proven by the gated integration test
        """Draw integrated field lines into ``plotter`` in the display's mapping, record their options as the drawn ones and keep the lines for a restyle.

        Args:
            plotter: The plotter to draw into.
            actors: The integrated field lines.
            options: The options the lines were integrated with.
        """
        import pyvista as pv

        styled: FieldLineOptions = self.line_style(options, actors)
        draw_streamlines(actors, styled, plotter)
        self._lines = (actors, options)
        self.drawn_lines = styled
        self.spec = self.spec.model_copy(update={"streamlines": options})

        # The bar holds the mapper it was made with, and a redraw replaces the lines' actor, so it is made again every time.
        self._remove_lines_bar(plotter)
        drawn: object = plotter.renderer.actors.get(str(StreamlineActor.LINES))
        if not isinstance(drawn, pv.Actor):
            return

        self._lines_bar = self.lines_title(actors.b_name)
        _ = cast(
            "object",
            plotter.add_scalar_bar(  # pyright: ignore[reportCallIssue, reportUnknownMemberType]  # pyvista's _Wrapped stub loses `self`
                title=self._lines_bar,
                mapper=drawn.mapper,
                vertical=True,
                position_x=0.1,
                position_y=0.2,
                width=0.035,
                height=0.6,
            ),
        )

    def lines_pinned(self, vectors: str) -> bool:
        """Whether field lines following ``vectors`` take the display's pinned range: it is pinned and shows that field's magnitude."""
        spec: DisplaySpec = self.spec
        return spec.clim is not None and spec.mode is DisplayMode.MAGNITUDE and spec.field == vectors

    def lines_title(self, b_name: str) -> str:
        """The field lines' scalar bar title: the integrated field's name."""
        return f"{title(b_name)}{LINES_SUFFIX}"

    def _remove_lines_bar(self, plotter: pv.Plotter) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context
        if self._lines_bar is not None and self._lines_bar in cast("Mapping[str, object]", plotter.scalar_bars):
            plotter.remove_scalar_bar(self._lines_bar, render=False)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista's _Wrapped stub loses `self`

        self._lines_bar = None

    def line_style(self, options: FieldLineOptions, actors: StreamlineActors) -> FieldLineOptions:
        """
        The field lines in the display's colormap, inversion, log scale and opacity map; a polar display leaves them as asked.

        Their range is the display's pinned one while it shows their field's magnitude, else the automatic range of that magnitude over their own region, whatever the bodies show.
        """
        spec: DisplaySpec = self.spec
        if spec.mode is DisplayMode.POLAR_XY:
            return options

        return options.model_copy(
            update={
                "colormap": spec.colormap,
                "invert": spec.invert,
                "log_scale": spec.log_scale,
                "opacity_map": spec.opacity_map,
                "clim": spec.clim if self.lines_pinned(actors.b_name) else color_range(actors.magnitudes, log_scale=spec.log_scale, robust=spec.robust),
            }
        )

    def figure(self, spec: DisplaySpec) -> dict[str, object]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """The plotly figure dict for the 2D path: one ``Mesh3d`` per body (hidden ones ``legendonly``) plus the outline, under an orthographic top-down camera.

        ``POLAR_XY`` falls back to the magnitude, a log scale plots ``log10`` with decade ticks, and ``opacity_map`` draws as ``opacity``.
        """
        fitted: DisplaySpec = spec.fitted(self.fields)
        plotted: DisplaySpec = fitted if fitted.mode is not DisplayMode.POLAR_XY else fitted.model_copy(update={"mode": DisplayMode.MAGNITUDE})
        info: FieldInfo = self.field_info(plotted.field)
        lo, hi = self.apply(plotted)

        colorscale: Colorscale = plotly_colorscale(sample_colormap(plotted.colormap), invert=plotted.invert)
        colorbar: TraceDict = {"title": {"text": info.display_name}, **(log_colorbar(lo, hi) if plotted.log_scale else {})}
        cmin, cmax = (math.log10(lo), math.log10(hi)) if plotted.log_scale else (lo, hi)
        hovertemplate: str = f"{info.display_name}: %{{customdata:.4g}}<extra>%{{fullData.name}}</extra>"

        solid: dict[str, str] = plotted.solid()
        traces: list[TraceDict] = []
        scale_shown: bool = False
        for name in (str(key) for key in self.blocks.keys()):
            block: pv.DataSet = cast("pv.DataSet", self.blocks[name])
            surface: pv.PolyData = cast("pv.PolyData", cast("pv.PolyData", block.extract_surface(algorithm=None)).triangulate())
            points, triangles = surface_triangles(surface)
            visible: bool = name not in plotted.hidden_bodies
            color: str | None = solid.get(name)
            if color is not None:
                traces.extend(mesh3d_traces(points, triangles, None, name=name, color=color, opacity=plotted.body_opacity(name), visible=visible))
                continue

            arrays = surface.point_data if info.association is Association.POINT else surface.cell_data
            values: FloatArray = np.asarray(arrays[SCALARS_ARRAY], dtype=np.float64)
            intensity: FloatArray = log_intensity(values, lo) if plotted.log_scale else values

            traces.extend(
                mesh3d_traces(
                    points,
                    triangles,
                    intensity,
                    name=name,
                    opacity=plotted.body_opacity(name),
                    intensitymode=info.association,
                    colorscale=colorscale,
                    cmin=cmin,
                    cmax=cmax,
                    showscale=visible and not scale_shown,
                    hovertemplate=hovertemplate,
                    customdata=values,
                    colorbar=colorbar,
                    visible=visible,
                )
            )
            scale_shown = scale_shown or visible

        edges: pv.PolyData | None = feature_outline(self.shown_blocks(plotted.hidden_bodies)) if plotted.outline else None
        if edges is not None:
            traces.extend(outline_traces(outline_segments(edges)))

        return {"data": traces, "layout": self._layout()}

    def _layout(self) -> dict[str, object]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """The figure layout: an orthographic top-down 3D scene keyed to this run so a redraw keeps the user's camera, with the body legend top-left clear of the colorbar."""
        camera: dict[str, object] = {
            "projection": {"type": "orthographic"},
            "eye": {"x": 0.0, "y": 0.0, "z": 1.6},
            "up": {"x": 0.0, "y": 1.0, "z": 0.0},
        }
        axis: dict[str, object] = {"visible": False, "showspikes": False}
        scene: dict[str, object] = {
            "camera": camera,
            "aspectmode": "data",
            "xaxis": axis,
            "yaxis": axis,
            "zaxis": axis,
        }
        legend: dict[str, object] = {"x": 0.0, "y": 1.0, "xanchor": "left", "yanchor": "top", "bgcolor": "rgba(0,0,0,0)"}
        return {
            "scene": scene,
            "margin": {"l": 0, "r": 0, "t": 0, "b": 0},
            "uirevision": self.run_id,
            "showlegend": True,
            "legend": legend,
        }
