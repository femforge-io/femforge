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
from femforge.elmer.physics import Physics
from femforge.elmer.variables import ELEMENTAL_SUFFIX, ElmerVariable, elemental, variable_of, variable_unit
from femforge.frames import Frame, SolvePlane, default_frame
from femforge.physical.conditions import SurfaceForce
from femforge.physical.units import U
from femforge.run.fields import GEOMETRY_IDS_ARRAY, select_array
from femforge.run.mechanical import VOIGT_COMPONENTS
from femforge.viz.colormaps import register_paraview
from femforge.viz.field_lines import (
    Bounds,
    FieldLineOptions,
    StreamlineActor,
    StreamlineActors,
    build_streamlines,
    build_swept_streamlines,
    draw_streamlines,
    integrates_like,
    prepare_streamline_source,
    ramp_alpha,
    seed_points,
    select_flux_density_array,
)
from femforge.viz.symmetry import Basis, Expansion, mirror, revolve


if TYPE_CHECKING:
    import pyvista as pv

    from femforge.config import SimulationConfig
    from femforge.run.entities import Bodies
    from femforge.run.inspect import Run
    from femforge.run.manifest import RunManifest


type FloatArray = np.typing.NDArray[np.float64]
type Float32Array = np.typing.NDArray[np.float32]
type IntArray = np.typing.NDArray[np.int64]
type AnyArray = np.typing.NDArray[np.generic]
type Colormapper = Callable[[FloatArray], FloatArray]

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

FLAT_RATIO = 1e-9
"""The Z extent, as a share of the largest extent, below which a grid is a flat section."""

BAR_LABELS = 5
"""Tick labels on a color bar."""

BAR_FORMAT = "%.3g"
BAR_TITLE_EXTRA = 2
"""Points the color bar's title is larger than its labels."""

FULL_SWEEP = 360.0
"""`[deg]`: A revolution that closes the solid."""

MM = 1e-3
"""Metres per millimetre: the authored frame is in millimetres, the solved mesh in metres."""

AXIS_SLACK = 1e-9
"""`[rad]`: How far past a cut face a seed may fold and still count as inside the sweep."""

WARP_SHARE = 0.1
"""`[-]`: The share of the section's bounds diagonal the automatic scale moves the largest displacement."""

DOMINANT_SHARE = 0.9
"""`[-]`: The share of the summed load one solve component must carry for a run to open on that component."""

OPACITY_MAP: tuple[float, float] = (0.15, 1.0)
"""`[-]`: The opacity at the low and high color limits a display maps by default."""


class Association(StrEnum):
    """Whether a VTU array lives on mesh points or cells (pyvista's ``preference``)."""

    POINT = "point"
    """One value per mesh point."""

    CELL = "cell"
    """One value per mesh cell."""


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

    MAX_PRINCIPAL = "max_principal"
    """The largest of the three principal values."""

    MID_PRINCIPAL = "mid_principal"
    """The middle one of the three principal values."""

    MIN_PRINCIPAL = "min_principal"
    """The smallest of the three principal values."""

    @property
    def label(self) -> str:
        """The mode's name as it reads in a picker."""
        return title(self.value)


AXIS_MODES: tuple[DisplayMode, ...] = (DisplayMode.X, DisplayMode.Y, DisplayMode.Z)
"""The component modes in solve-axis order."""

VECTOR_MODES: frozenset[DisplayMode] = frozenset({DisplayMode.X, DisplayMode.Y, DisplayMode.Z, DisplayMode.MAGNITUDE, DisplayMode.POLAR_XY})
"""The modes that need a 3-component array; the rest accept any width."""

PRINCIPAL_MODES: tuple[DisplayMode, ...] = (DisplayMode.MAX_PRINCIPAL, DisplayMode.MID_PRINCIPAL, DisplayMode.MIN_PRINCIPAL)
"""The modes of a principal-value field, the default first; each needs three values per row."""

ROTATION_FREE_MODES: frozenset[DisplayMode] = frozenset({DisplayMode.VALUE, DisplayMode.ABSVALUE, DisplayMode.MAGNITUDE, *PRINCIPAL_MODES})
"""The modes whose value a mirrored or revolved copy keeps; the others turn with it."""


class FieldKind(StrEnum):
    """What one row of a field array holds, which sets its display modes and whether an expansion turns it."""

    SCALAR = "scalar"
    """One value."""

    VECTOR = "vector"
    """A 3-component vector, turned with a mirrored or revolved copy."""

    PRINCIPAL = "principal"
    """Three principal values, which no rotation changes."""

    TENSOR = "tensor"
    """A Voigt tensor, whole or as one of the ``stress_xx`` columns Elmer splits it into, or any other width; not displayed."""


PRINCIPAL_VARIABLES: frozenset[ElmerVariable] = frozenset({ElmerVariable.PRINCIPAL_STRESS, ElmerVariable.PRINCIPAL_STRAIN})
"""The 3-component variables that hold principal values rather than a vector."""

TENSOR_VARIABLES: frozenset[ElmerVariable] = frozenset({ElmerVariable.STRESS, ElmerVariable.STRAIN})
"""The Voigt tensor variables, whose split ``<name>_xx`` columns are tensor parts rather than scalars."""


def field_kind(name: str, components: int) -> FieldKind:
    """The kind of a VTU array by its name and its component count."""

    def tensor_column() -> bool:
        bare: str = name.lower().removesuffix(ELEMENTAL_SUFFIX)
        variable, _, component = bare.rpartition("_")

        return component in VOIGT_COMPONENTS and variable_of(variable) in TENSOR_VARIABLES

    if components == 3:
        return FieldKind.PRINCIPAL if variable_of(name) in PRINCIPAL_VARIABLES else FieldKind.VECTOR

    if components != 1 or tensor_column():
        return FieldKind.TENSOR

    return FieldKind.SCALAR


class Legend(StrEnum):
    """Whether and how the color bar of the shown field is drawn."""

    HIDDEN = "hidden"
    """No color bar; the controls show the colormap and its range."""

    VERTICAL = "vertical"
    """Upright at the right of the view."""

    HORIZONTAL = "horizontal"
    """Along the bottom of the view."""


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

    unit: str | None = None
    """The unit the values are in, None when unknown."""

    @property
    def display_name(self) -> str:
        """The array's name as it reads in a picker: the variable's own spelling where one names it, with the [`FIELD_RENAMES`][] overrides applied."""
        variable: ElmerVariable | None = variable_of(self.name)
        spelled: str = self.name

        if variable is not None:
            spelled = elemental(variable) if self.name.lower().endswith(ELEMENTAL_SUFFIX) else str(variable)

        label: str = title(spelled)

        return FIELD_RENAMES.get(label, label)

    @property
    def title(self) -> str:
        """The display name with the unit in brackets, as a color bar is titled."""
        return titled(self.display_name, self.unit)

    @property
    def kind(self) -> FieldKind:
        """What one row of the array holds."""
        return field_kind(self.name, self.components)

    @property
    def modes(self) -> tuple[DisplayMode, ...]:
        """The display modes this array supports, the default first; a tensor supports none."""
        match self.kind:
            case FieldKind.SCALAR:
                return (DisplayMode.VALUE, DisplayMode.ABSVALUE)
            case FieldKind.VECTOR:
                return (DisplayMode.MAGNITUDE, DisplayMode.X, DisplayMode.Y, DisplayMode.Z, DisplayMode.POLAR_XY)
            case FieldKind.PRINCIPAL:
                return PRINCIPAL_MODES
            case FieldKind.TENSOR:
                return ()


def titled(name: str, unit: str | None) -> str:
    """``name`` with ``unit`` in brackets after it, or alone when there is none."""
    return f"{name} [{unit}]" if unit is not None else name


def field_infos(point_arrays: Mapping[str, AnyArray], cell_arrays: Mapping[str, AnyArray], physics: Physics | None = None) -> list[FieldInfo]:
    """Every displayable array of a VTU as a [`FieldInfo`][], sorted by display name; ``GeometryIds`` and tensors are left out.

    Args:
        point_arrays: The VTU's point arrays by name.
        cell_arrays: The VTU's cell arrays by name.
        physics: The physics the run solved, which names the unit of its potential; None leaves it unitless.
    """
    infos: list[FieldInfo] = []
    for association, arrays in ((Association.POINT, point_arrays), (Association.CELL, cell_arrays)):
        for name, values in arrays.items():
            if name == GEOMETRY_IDS_ARRAY:
                continue

            array: AnyArray = np.asarray(values)
            shape: tuple[int, ...] = array.shape
            components: int = shape[-1] if array.ndim > 1 else 1

            if field_kind(name, components) is FieldKind.TENSOR:
                continue

            infos.append(FieldInfo(name=name, association=association, components=components, dtype=str(array.dtype), unit=variable_unit(name, physics)))

    return sorted(infos, key=lambda info: info.display_name)


def line_vectors(fields: Sequence[FieldInfo], shown: str) -> str | None:
    """The vector field the field lines follow: the shown one when it is a vector, else the flux density, else the first vector field; None for a run with none.

    Args:
        fields: The run's displayable fields, in picker order.
        shown: The name of the array the display colors by.
    """
    vectors: list[str] = [info.name for info in fields if info.kind is FieldKind.VECTOR]
    if shown in vectors:
        return shown

    if not vectors:
        return None

    try:
        return select_flux_density_array(vectors)
    except KeyError:
        return vectors[0]


def load_mode(config: SimulationConfig) -> DisplayMode:
    """The solve component the run's surface forces share, Magnitude when none carries `DOMINANT_SHARE` of their sum."""
    total: list[float] = [0.0, 0.0, 0.0]
    for tag in config.domain.tags:
        forces: list[SurfaceForce] = [condition for condition in tag.conditions if isinstance(condition, SurfaceForce)]
        for force in forces:
            solved: tuple[float, ...] = config.frame.solve_vector(force.force.to(U.N).magnitude)
            for index, component in enumerate(solved):
                total[index] += component

    size: float = math.hypot(*total)
    if size == 0.0:
        return DisplayMode.MAGNITUDE

    axis: int = max(range(len(total)), key=lambda index: abs(total[index]))

    return AXIS_MODES[axis] if abs(total[axis]) >= DOMINANT_SHARE * size else DisplayMode.MAGNITUDE


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
    """Name of the VTU array to color by; empty opens the viewer on each run's [`default_display`][femforge.viz.default_display], and `fitted` takes the first array."""

    mode: DisplayMode = DisplayMode.VALUE
    """How the array is reduced to the displayed value."""

    colormap: str = "turbo"
    """Colormap name, resolved through pyvista's ``get_cmap_safe`` (matplotlib and colorcet names both work)."""

    log_scale: bool = False
    """Map the color range logarithmically; only the strictly positive values take part."""

    invert: bool = False
    """Reverse the colormap."""

    clim: tuple[float, float] | None = None
    """`[?]`: Pinned color limits in the displayed value's unit, or None to recompute them from the data on every apply."""

    robust: bool = True
    """Clamp the automatic limits to the 2-98 percentiles so a few outlier cells cannot flatten the picture."""

    legend: Legend = Legend.HIDDEN
    """Whether and where the color bar is drawn; field lines mapped like the field share it."""

    expansion: Expansion = Expansion.REVOLVE
    """How a run solved on a section is shown; a run solved in 3D is shown as is."""

    sweep: float = Field(default=FULL_SWEEP, gt=0.0, le=FULL_SWEEP)
    """`[deg]`: The turn a revolved section sweeps; less than a full one leaves a cutaway."""

    warp: str | None = ElmerVariable.DISPLACEMENT.lower()
    """The vector array the bodies are displaced by, None to draw them undeformed."""

    warp_scale: float | None = Field(default=None, ge=0.0)
    """`[-]`: The displacement exaggeration; None moves the largest displacement `WARP_SHARE` of the section diagonal."""

    hidden_bodies: tuple[str, ...] = ()
    """Body names to hide, by their ``entities.json`` name."""

    outline: bool = False
    """Draw the black feature-edge wireframe over the visible bodies."""

    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    """`[-]`: Multiplies every body's own opacity."""

    opacity_map: tuple[Annotated[float, Field(ge=0.0, le=1.0)], Annotated[float, Field(ge=0.0, le=1.0)]] | None = OPACITY_MAP
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

    streamlines: FieldLineOptions | None = Field(default_factory=FieldLineOptions)
    """Options for the field-line overlay, or None to draw none."""

    def fitted(self, fields: Sequence[FieldInfo]) -> DisplaySpec:
        """
        This spec snapped to what a run offers: an unset field takes the first one, a disallowed mode the field's first allowed one, and a warp without a vector field to follow is dropped.

        An unknown field name raises [`KeyError`][] naming the available arrays.
        """
        if not fields:
            raise KeyError("no displayable fields")

        by_name: dict[str, FieldInfo] = {info.name: info for info in fields}
        chosen: FieldInfo = fields[0] if not self.field else by_name[select_array(by_name, self.field)]

        def resolved_warp() -> str | None:
            vectors: list[str] = [info.name for info in fields if info.kind is FieldKind.VECTOR]
            if self.warp is None:
                return None

            try:
                return select_array(vectors, self.warp)
            except KeyError:
                return None

        allowed: tuple[DisplayMode, ...] = chosen.modes
        mode: DisplayMode = self.mode if self.mode in allowed else allowed[0]
        warp: str | None = resolved_warp()
        if chosen.name == self.field and mode is self.mode and warp == self.warp:
            return self

        return self.model_copy(update={"field": chosen.name, "mode": mode, "warp": warp})


def reduce_field(values: AnyArray, mode: DisplayMode) -> FloatArray:
    """
    The scalar a display mode draws from one field array.

    ``VALUE`` passes the array through, ``POLAR_XY`` returns the XY magnitude (the quantity that sets the color range; the angle rides along as hue) and a principal mode picks from each row sorted.
    """
    array: FloatArray = np.asarray(values, dtype=np.float64)
    wide: bool = mode in VECTOR_MODES or mode in PRINCIPAL_MODES
    if wide and (array.ndim < 2 or array.shape[-1] != 3):
        raise ValueError(f"display mode {mode.value!r} needs 3 components per row, got shape {array.shape}")

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
        case DisplayMode.MAX_PRINCIPAL:
            return np.sort(array, axis=-1)[..., 2]
        case DisplayMode.MID_PRINCIPAL:
            return np.sort(array, axis=-1)[..., 1]
        case DisplayMode.MIN_PRINCIPAL:
            return np.sort(array, axis=-1)[..., 0]


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


@dataclass(frozen=True)
class BarPlacement:
    """Where a color bar sits, in normalized viewport coordinates."""

    vertical: bool
    """Whether the bar stands upright."""

    x: float
    """`[-]`: The left edge, as a share of the view's width."""

    y: float
    """`[-]`: The bottom edge, as a share of the view's height."""

    width: float
    """`[-]`: The width, as a share of the view's width."""

    height: float
    """`[-]`: The height, as a share of the view's height."""


def bar_placement(legend: Legend, *, second: bool = False) -> BarPlacement:
    """The color bar's place for a legend: upright at the right, or along the bottom clear of the axes corner; a second bar sits beside the first.

    Args:
        legend: The legend asked for.
        second: Whether this bar joins one already placed.
    """
    if legend is Legend.HORIZONTAL:
        return BarPlacement(vertical=False, x=0.3, y=0.14 if second else 0.02, width=0.4, height=0.1)

    return BarPlacement(vertical=True, x=0.72 if second else 0.86, y=0.25, width=0.03, height=0.5)


def list_fields(grid: pv.DataSet, physics: Physics | None = None) -> list[FieldInfo]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """Every displayable field of a VTU grid (``GeometryIds`` excluded), sorted by display name, with the units ``physics`` decides."""
    point_arrays: dict[str, AnyArray] = {name: np.asarray(grid.point_data[name]) for name in grid.point_data.keys()}
    cell_arrays: dict[str, AnyArray] = {name: np.asarray(grid.cell_data[name]) for name in grid.cell_data.keys()}
    return field_infos(point_arrays, cell_arrays, physics)


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
    extents: list[float] = [bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4]]
    # Elmer writes a 2D mesh with a Z extent of a few 1e-18, never exactly zero.
    return 2 if extents[2] <= FLAT_RATIO * max(extents) else 3


def flatten(grid: pv.DataSet, blocks: pv.MultiBlock) -> None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """Puts a section's points exactly on Z = 0: Elmer leaves them a few 1e-18 off it, which a data-aspect plot scales up by 1e17."""
    for dataset in (grid, *(cast("pv.DataSet", blocks[name]) for name in blocks.keys())):
        points: FloatArray = np.asarray(dataset.points, dtype=np.float64)
        points[:, 2] = 0.0
        dataset.points = points


def warped[Shown: pv.DataSet](dataset: Shown, vectors: str, factor: float) -> Shown:  # pragma: no cover - needs pyvista (viz extra); proven by the gated tests
    """A copy of ``dataset`` displaced by ``factor`` times its ``vectors`` array, an elemental one first averaged onto the points.

    Args:
        dataset: The bodies to displace.
        vectors: The name of the 3-component point or cell array they move along.
        factor: The exaggeration the array is multiplied by.
    """
    if vectors in dataset.point_data:
        return cast("Shown", dataset.warp_by_vector(vectors=vectors, factor=factor, inplace=False))

    projected = cast("pv.DataSet", dataset.cell_data_to_point_data())
    moved = cast("Shown", dataset.copy())
    moved.points = np.asarray(dataset.points, dtype=np.float64) + factor * np.asarray(projected.point_data[vectors], dtype=np.float64)

    return moved


def scaled_basis(plane: SolvePlane) -> Basis:
    """Where the section sits in the authored model, with the plane's origin brought from millimetres to the mesh's metres."""
    basis: Basis = Basis.of(plane)
    origin: tuple[float, float, float] = (basis.origin[0] * MM, basis.origin[1] * MM, basis.origin[2] * MM)

    return Basis(origin=origin, radial=basis.radial, axis=basis.axis)


def run_manifest(run: Run) -> RunManifest | None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """The run's manifest, None when the bundle has none to read."""
    try:
        return run.manifest
    except (FileNotFoundError, ValueError):
        log.bind(run_id=run.path.name).opt(exception=True).warning("Run manifest unreadable; the frame is taken from the grid and the fields stay unitless.")

        return None


def default_display(run: Run) -> DisplaySpec:
    """The display a run opens on: its stack's primary field, on the component its loads share, with field lines and opacity mapping for a flux or current."""
    try:
        manifest: RunManifest = run.manifest
    except (FileNotFoundError, ValueError):
        return DisplaySpec()

    variable: ElmerVariable | None = manifest.physics_config.primary_variable
    if variable is None:
        return DisplaySpec()

    flows: bool = variable.lines_by_default

    return DisplaySpec(
        field=variable.lower(),
        mode=load_mode(manifest.config),
        streamlines=FieldLineOptions() if flows else None,
        opacity_map=OPACITY_MAP if flows else None,
    )


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


class FieldScene:
    """
    One run's solved bodies as a recolorable field display.

    It is a [`Renderable`][femforge.viz.scene.Renderable] whose [`DisplaySpec`][] picks the field, its reduction, its colors and how a section is expanded.
    """

    def __init__(
        self,
        *,
        grid: pv.UnstructuredGrid,
        blocks: pv.MultiBlock,
        fields: Sequence[FieldInfo],
        model_dim: int,
        run_id: str,
        bodies: Bodies | None = None,
        frame: Frame | None = None,
    ) -> None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """Hold one run's solved grid, split into bodies, under a default display.

        Args:
            grid: The solved VTU grid.
            blocks: The grid split into one block per named body.
            fields: The displayable arrays.
            model_dim: 2 for an axisymmetric slice, else 3.
            run_id: The run bundle's name.
            bodies: The named bodies from ``entities.json``; None names none.
            frame: The solve frame; None takes the first shipped frame that solves on the grid's dimension.
        """
        from femforge.run.entities import Bodies

        register_paraview()
        self.frame: Frame = frame if frame is not None else default_frame(model_dim)
        self.model_dim: int = self.frame.model_dim
        if self.model_dim == 2:
            flatten(grid, blocks)

        self.section_grid: pv.UnstructuredGrid = grid
        """The grid as solved."""
        self.section_blocks: pv.MultiBlock = blocks
        """The bodies as solved, whose values set every range."""
        self.grid: pv.UnstructuredGrid = grid
        """The grid as shown, which the field lines run through: the section, its mirror, or the section again under a revolved display, displaced under a warp."""
        self.blocks: pv.MultiBlock = blocks
        """The bodies as shown."""
        self.fields: list[FieldInfo] = list(fields)
        plane: SolvePlane | None = self.frame.plane
        self.basis: Basis | None = scaled_basis(plane) if plane is not None and self.frame.revolves else None
        """Where the section sits in the authored model, None for a run solved whole."""
        self.expanded: tuple[Expansion, float] = (Expansion.SECTION, FULL_SWEEP)
        """The expansion and sweep the shown blocks were built for."""
        self.warped: tuple[str, float] | None = None
        """The vector array and the scale the shown blocks are displaced by, None while they are drawn as solved."""
        self.run_id: str = run_id
        self.bodies: Bodies = bodies if bodies is not None else Bodies([])
        self.spec: DisplaySpec = DisplaySpec().fitted(self.fields) if self.fields else DisplaySpec()
        self.clim: tuple[float, float] = (0.0, 1.0)
        self.data_range: tuple[float, float] = (0.0, 1.0)
        self._actor: pv.Actor | None = None
        self._surfaces: pv.MultiBlock | None = None
        """The surface copy of the shown blocks the body actor draws, which every apply recolors with the blocks."""
        self._styled: dict[str, tuple[str, float]] = {}
        """The ``(color, opacity)`` of every solid body actor the plotter holds."""
        self._lines: tuple[StreamlineActors, FieldLineOptions] | None = None
        """The last integrated field lines and the options they were integrated with, restyled without integrating again."""
        self.drawn_lines: FieldLineOptions | None = None
        """The styled options of the lines the plotter holds."""
        self._lines_bar: str | None = None
        """The title of the field lines' scalar bar the plotter holds."""
        self._field_bar: str | None = None
        """The title of the field's scalar bar the plotter holds."""

    @classmethod
    def from_run(cls, run: Run) -> FieldScene:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """Open a run bundle's VTU as a field scene, its bodies split and named from ``entities.json``."""
        import pyvista as pv

        if run.vtu_path is None:
            raise FileNotFoundError(f"{run.path}: no VTU output to display")

        bodies: Bodies = run.bodies
        if not len(bodies):
            raise FileNotFoundError(f"{run.path}: no named bodies ({BundleFile.ENTITIES} missing); re-solve the run so the bundle carries its entities")

        from femforge.run.mechanical import add_derived_fields

        grid: pv.UnstructuredGrid = cast("pv.UnstructuredGrid", pv.read(str(run.vtu_path)))
        add_derived_fields(grid)
        blocks: pv.MultiBlock = split_by_body(grid, bodies)
        manifest: RunManifest | None = run_manifest(run)
        fields: list[FieldInfo] = list_fields(grid, manifest.physics if manifest is not None else None)
        frame: Frame | None = manifest.config.frame if manifest is not None else None

        log.bind(run_id=run.path.name, bodies=len(blocks), fields=len(fields), frame=frame.label if frame is not None else None).info("Field scene loaded.")
        return cls(grid=grid, blocks=blocks, fields=fields, model_dim=model_dim_of(grid), run_id=run.path.name, bodies=bodies, frame=frame)

    @property
    def expandable(self) -> bool:
        """Whether the run was solved on a section that can be mirrored or revolved."""
        return self.basis is not None

    @property
    def display_dim(self) -> int:
        """2 while the run is shown flat, 3 when it is solid or revolved."""
        return self.expanded[0].dim if self.basis is not None else self.model_dim

    @property
    def bounds(self) -> Bounds:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """The bounds of the shown bodies."""
        return cast("Bounds", tuple(self.blocks.bounds))

    def expansion_of(self, spec: DisplaySpec) -> tuple[Expansion, float]:
        """The expansion and sweep a spec asks of this run: a run solved in 3D is always shown as is, and only a revolution has a sweep."""
        if self.basis is None or spec.expansion is not Expansion.REVOLVE:
            return (Expansion.SECTION if self.basis is None else spec.expansion, FULL_SWEEP)

        return (spec.expansion, spec.sweep)

    def dim_for(self, spec: DisplaySpec) -> int:
        """The dimension the run shows in under a spec."""
        return self.expansion_of(spec)[0].dim if self.basis is not None else self.model_dim

    def auto_warp_scale(self, name: str) -> float:  # pragma: no cover - needs pyvista (viz extra); proven by the gated tests
        """The scale that moves the largest ``name`` displacement `WARP_SHARE` of the section's bounds diagonal, 0.0 when nothing moves."""
        finite: list[FloatArray] = [chunk[np.isfinite(chunk)] for chunk in self.region_magnitudes(name, None)]
        largest: float = max((float(np.max(chunk)) for chunk in finite if chunk.size), default=0.0)
        if largest == 0.0:
            return 0.0

        bounds: Sequence[float] = self.section_blocks.bounds
        diagonal: float = math.dist(bounds[0::2], bounds[1::2])

        return WARP_SHARE * diagonal / largest

    def warp_of(self, spec: DisplaySpec) -> tuple[str, float] | None:
        """The vector array and the scale a spec displaces the bodies by: its own scale, else the automatic one; None for no warp or a zero scale."""
        name: str | None = spec.fitted(self.fields).warp if self.fields else None
        if name is None:
            return None

        scale: float = spec.warp_scale if spec.warp_scale is not None else self.auto_warp_scale(name)

        return (name, scale) if scale > 0.0 else None

    def reshaped(self, spec: DisplaySpec) -> bool:
        """Whether a spec asks for other shown blocks than the scene holds: another expansion, sweep or warp."""
        return self.expansion_of(spec) != self.expanded or self.warp_of(spec) != self.warped

    def expand(self, spec: DisplaySpec) -> bool:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """Rebuilds the shown blocks and grid for the spec's expansion and warp when either changed, dropping the field lines integrated for the old ones; True when it did."""
        import pyvista as pv

        wanted: tuple[Expansion, float] = self.expansion_of(spec)
        warp: tuple[str, float] | None = self.warp_of(spec)
        if wanted == self.expanded and warp == self.warped:
            return False

        expansion, sweep = wanted
        basis: Basis | None = self.basis
        still: frozenset[str] = frozenset(info.name for info in self.fields if info.kind is not FieldKind.VECTOR)

        def deform[Shown: pv.DataSet](dataset: Shown) -> Shown:
            """The dataset displaced by the warp, as is without one."""
            if warp is None:
                return dataset

            return warped(dataset, *warp)

        def rebuilt(build: Callable[[pv.DataSet], pv.DataSet]) -> pv.MultiBlock:
            """Every section block through ``build`` and the warp, under its name."""
            shown: pv.MultiBlock = pv.MultiBlock()
            for name in (str(key) for key in self.section_blocks.keys()):
                shown.append(deform(build(cast("pv.DataSet", self.section_blocks[name]))), name=name)

            return shown

        match expansion:
            case Expansion.SECTION if warp is None:
                self.blocks, self.grid = self.section_blocks, self.section_grid
            case Expansion.SECTION:
                self.blocks, self.grid = rebuilt(lambda block: block), deform(self.section_grid)
            case Expansion.MIRROR:
                self.blocks, self.grid = rebuilt(lambda block: mirror(block, still=still)), deform(mirror(self.section_grid, still=still))
            case Expansion.REVOLVE:
                assert basis is not None, "a run without a basis is shown as is"
                self.blocks, self.grid = rebuilt(lambda block: revolve(block, basis, sweep=sweep, still=still)), deform(self.section_grid)

        # Recorded only once the blocks stand, so a build that raised is tried again by the next draw; the body actor holds the old blocks, so it is added anew.
        self.expanded = wanted
        self.warped = warp
        self._actor = None
        self._surfaces = None
        self._lines = None
        self.drawn_lines = None
        log.bind(
            run_id=self.run_id,
            expansion=str(expansion),
            sweep=sweep,
            warp=warp[0] if warp is not None else None,
            scale=warp[1] if warp is not None else None,
        ).info("Shown bodies rebuilt.")

        return True

    def field_info(self, name: str) -> FieldInfo:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """The [`FieldInfo`][] for an array name, resolved the same way a field query resolves one."""
        by_name: dict[str, FieldInfo] = {info.name: info for info in self.fields}
        return by_name[select_array(by_name, name)]

    def apply(self, spec: DisplaySpec) -> tuple[float, float]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """Write the spec's reduction into every block's ``display_scalars`` array and return the color limits it produced."""
        import pyvista as pv

        fitted: DisplaySpec = spec.fitted(self.fields)
        info: FieldInfo = self.field_info(fitted.field)
        _ = self.expand(fitted)

        def raw_values(block: pv.DataSet) -> FloatArray:
            """One block's copy of the displayed array, zero-filled when the block never carried it."""
            arrays = block.point_data if info.association is Association.POINT else block.cell_data
            width: int = block.n_points if info.association is Association.POINT else block.n_cells
            if info.name not in arrays:
                return np.zeros((width, info.components), dtype=np.float64)

            return np.asarray(arrays[info.name], dtype=np.float64)

        names: list[str] = [str(key) for key in self.blocks.keys()]
        solved: list[AnyArray] = [reduce_field(raw_values(cast("pv.DataSet", self.section_blocks[name])), fitted.mode) for name in names]
        shown: list[AnyArray] = solved if self.blocks is self.section_blocks else [reduce_field(raw_values(cast("pv.DataSet", self.blocks[name])), fitted.mode) for name in names]

        # Every body counts, each through its own range, whatever it is shown as: the scale belongs to the field, not the view. A
        # component or an in-plane direction turns with the expansion, so its range comes from the values drawn.
        ranged: list[AnyArray] = solved if fitted.mode in ROTATION_FREE_MODES else shown
        self.data_range = color_range(ranged, robust=False)
        clim: tuple[float, float] = fitted.clim if fitted.clim is not None else color_range(ranged, log_scale=fitted.log_scale, robust=fitted.robust)

        def painted(block: pv.DataSet) -> AnyArray:
            """What colors one block: the reduction, or RGBA rows in the polar mode, whose reduction only sets the range."""
            values: FloatArray = raw_values(block)
            if fitted.mode is not DisplayMode.POLAR_XY:
                return reduce_field(values, fitted.mode)

            cmap = pv.get_cmap_safe(fitted.colormap)  # pyright: ignore[reportUnknownMemberType]  # pyvista returns an untyped matplotlib Colormap

            def mapper(fractions: FloatArray) -> FloatArray:
                """RGBA rows for cyclic angle fractions, through the spec's colormap."""
                return np.asarray(cmap(fractions), dtype=np.float64)

            return polar_rgba(values, clim[0], clim[1], mapper)

        surfaces: list[pv.DataSet] = [] if self._surfaces is None else [cast("pv.DataSet", self._surfaces[name]) for name in names]
        for block in [*(cast("pv.DataSet", self.blocks[name]) for name in names), *surfaces]:
            arrays = block.point_data if info.association is Association.POINT else block.cell_data
            # VTK's SetScalars replaces whichever array is active, the displayed field itself when it is, so the reduction is added beside it and then made active.
            arrays[SCALARS_ARRAY] = painted(block)
            arrays.active_scalars_name = SCALARS_ARRAY

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

        # pyvista draws a composite through surface copies of its blocks, so the scene keeps those copies to recolor them.
        self._surfaces = self.blocks.as_polydata_blocks()
        self._actor = plotter.add_mesh(  # pyright: ignore[reportUnknownMemberType]
            self._surfaces,
            scalars=SCALARS_ARRAY,
            show_edges=False,
            show_scalar_bar=False,
            name=str(SceneActor.BODIES),
        )
        # Translucent bodies sort per fragment only under depth peeling; without it a far body can paint over a near one.
        _ = cast("object", plotter.enable_depth_peeling())  # pyright: ignore[reportCallIssue, reportUnknownMemberType]  # pyvista's _Wrapped stub loses `self`
        self._redraw(plotter, spec)

    def update(self, plotter: pv.Plotter, spec: DisplaySpec) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context; proven by the gated integration test
        """Recolor an already-rendered scene in place, the cheap path a panel calls on every knob change; a changed expansion or warp draws the scene anew."""
        if self._actor is None or self.reshaped(spec.fitted(self.fields)):
            self.spec = spec
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
        self._update_lines_bar(plotter)

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
        """Re-add the field's color bar under its title (pyvista keys bars by title) where the legend asks for one, or none for a None spec: no visible body takes the colormap."""
        bars: Mapping[str, object] = plotter.scalar_bars  # pyright: ignore[reportAny]  # pyvista's ScalarBars container is untyped
        for name in [name for name in bars.keys() if name != self._lines_bar]:
            plotter.remove_scalar_bar(name, render=False)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista's _Wrapped stub loses `self`

        self._field_bar = None
        if spec is None or spec.mode is DisplayMode.POLAR_XY or spec.legend is Legend.HIDDEN:
            return

        self._add_bar(plotter, info.title, mapper, spec.legend)
        self._field_bar = info.title

    def _add_bar(self, plotter: pv.Plotter, title: str, mapper: object, legend: Legend, *, second: bool = False) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context
        """A compact color bar in the theme's font, placed by the legend beside any bar already there.

        Args:
            plotter: The plotter the bar is added to.
            title: The bar's title, which pyvista keys it by.
            mapper: The mapper whose lookup table the bar shows.
            legend: Where the legend goes.
            second: Whether the field's bar is already placed.
        """
        placement: BarPlacement = bar_placement(legend, second=second)
        size: int = int(plotter.theme.font.size)
        _ = cast(
            "object",
            plotter.add_scalar_bar(  # pyright: ignore[reportCallIssue, reportUnknownMemberType]  # pyvista's _Wrapped stub loses `self`
                title=title,
                mapper=mapper,
                n_labels=BAR_LABELS,
                title_font_size=size + BAR_TITLE_EXTRA,
                label_font_size=size,
                fmt=BAR_FORMAT,
                vertical=placement.vertical,
                position_x=placement.x,
                position_y=placement.y,
                width=placement.width,
                height=placement.height,
            ),
        )

    def _update_lines_bar(self, plotter: pv.Plotter) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context
        """Gives the drawn field lines a color bar of their own where the legend asks for one and the field's bar does not already show their mapping."""
        import pyvista as pv

        self._remove_lines_bar(plotter)
        styled: FieldLineOptions | None = self.drawn_lines
        drawn: object = plotter.renderer.actors.get(str(StreamlineActor.LINES))
        if styled is None or self._lines is None or self.spec.legend is Legend.HIDDEN or not isinstance(drawn, pv.Actor):
            return

        if self._field_bar is not None and self.lines_share(styled):
            return

        self._lines_bar = self.lines_title(self._lines[0].b_name)
        self._add_bar(plotter, self._lines_bar, drawn.mapper, self.spec.legend, second=self._field_bar is not None)

    def lines_share(self, styled: FieldLineOptions) -> bool:
        """Whether lines drawn with ``styled`` map like the field: the same colormap, inversion, log scale, opacity map and limits."""
        spec: DisplaySpec = self.spec
        same_map: bool = styled.colormap == spec.colormap and styled.invert == spec.invert and styled.log_scale == spec.log_scale

        return same_map and styled.opacity_map == spec.opacity_map and styled.clim == self.clim

    def _update_outline(self, plotter: pv.Plotter, spec: DisplaySpec) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context
        """Add or remove the black feature-edge wireframe of the visible bodies, by its own actor name."""
        edges: pv.PolyData | None = feature_outline(self.shown_blocks(spec.hidden_bodies)) if spec.outline else None
        if edges is None:
            _ = plotter.remove_actor(str(SceneActor.OUTLINE))  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista removes by actor name, and its _Wrapped stub loses `self`
            return

        _ = plotter.add_mesh(edges, color="black", line_width=2.0, name=str(SceneActor.OUTLINE))  # pyright: ignore[reportUnknownMemberType]

    def _update_streamlines(self, plotter: pv.Plotter, spec: DisplaySpec) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context
        """Draw the field-line overlay when it or the display it is styled by changed, integrating only new lines, or remove it when the spec turns it off or the grid has no field to follow."""
        vectors: str | None = None if spec.streamlines is None else self.lines_array(spec.streamlines, spec.field)
        if spec.streamlines is None or vectors is None:
            for actor in StreamlineActor:
                _ = plotter.remove_actor(str(actor))  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista removes by actor name, and its _Wrapped stub loses `self`
            self._remove_lines_bar(plotter)
            self.drawn_lines = None
            return

        options: FieldLineOptions = spec.streamlines.model_copy(update={"vectors": vectors})
        cached: StreamlineActors | None = self.integrated(options)
        if cached is not None and self.line_style(options, cached) == self.drawn_lines:
            return

        self.show_streamlines(plotter, cached or self.integrate_streamlines(options), options)

    def lines_array(self, options: FieldLineOptions, shown: str) -> str | None:
        """The array the field lines follow: the one asked for when the grid has it, else the one [`line_vectors`][] picks for the ``shown`` array."""
        if options.vectors is None:
            return line_vectors(self.fields, shown)

        return options.vectors if options.vectors in self.grid.array_names else None

    def integrated(self, options: FieldLineOptions) -> StreamlineActors | None:
        """The cached field lines when ``options`` only restyle them, None when they need integrating."""
        if self._lines is None or not integrates_like(self._lines[1], options):
            return None

        return self._lines[0]

    def integrate_streamlines(self, options: FieldLineOptions) -> StreamlineActors:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """Seed and integrate the field lines through the shown grid, swept into a revolved display from its section — the compute half, safe off the UI thread."""
        basis: Basis | None = self.basis
        if self.expanded[0] is Expansion.REVOLVE and basis is not None:
            source, b_name = prepare_streamline_source(self.grid, options)
            bounds: Bounds = self.region_bounds(options)
            actors: StreamlineActors = build_swept_streamlines(source, b_name, options, basis, bounds, seeds=self.swept_seeds(options, bounds))
        else:
            source, b_name = prepare_streamline_source(self.grid, options)
            actors = build_streamlines(source, b_name, options)

        return replace(actors, magnitudes=self.region_magnitudes(b_name, options.region_id))

    def region_bounds(self, options: FieldLineOptions) -> Bounds:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """The shown bounds the field lines seed: the integration region's body when the options confine them to one, else every body."""
        region: int | None = options.region_id if options.clip_to_region else None
        named: list[str] = [body.name for body in self.bodies if region is not None and body.mesh_id == region and body.name in self.blocks.keys()]
        if not named:
            return self.bounds

        return cast("Bounds", tuple(cast("pv.DataSet", self.blocks[named[0]]).bounds))

    def swept_seeds(self, options: FieldLineOptions, bounds: Bounds) -> FloatArray | None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """The seeds of the revolved display as a solid run would place them in ``bounds``, less the ones a partial sweep cuts away; None leaves a full sweep to seed itself."""
        basis: Basis | None = self.basis
        sweep: float = self.expanded[1]
        if basis is None or sweep >= FULL_SWEEP:
            return None

        filling: FloatArray = np.asarray(seed_points(bounds, options), dtype=np.float64)
        _, theta = basis.fold(filling)
        # Shifted by the slack before the wrap, so a seed a rounding error below the first cut face counts as on it, like one past the last.
        inside = cast("np.typing.NDArray[np.bool_]", np.mod(theta + AXIS_SLACK, 2.0 * math.pi) <= math.radians(sweep) + 2.0 * AXIS_SLACK)

        return filling[inside]

    def region_magnitudes(self, name: str, region_id: int | None) -> list[FloatArray]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        """The magnitudes of array ``name`` per body of a region (every body for None) as solved, the chunks a field-line range spans."""

        chunks: list[FloatArray] = []
        for block_name in (str(key) for key in self.section_blocks.keys()):
            body = self.bodies.get(block_name)
            if region_id is not None and (body is None or body.mesh_id != region_id):
                continue

            block: pv.DataSet = cast("pv.DataSet", self.section_blocks[block_name])
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
        styled: FieldLineOptions = self.line_style(options, actors)
        draw_streamlines(actors, styled, plotter)
        self._lines = (actors, options)
        self.drawn_lines = styled
        self.spec = self.spec.model_copy(update={"streamlines": options})

        # The bar holds the mapper it was made with, and a redraw replaces the lines' actor, so it is made again every time.
        self._update_lines_bar(plotter)

    def lines_follow(self, vectors: str) -> bool:
        """Whether field lines following ``vectors`` take the display's range: it shows that field's magnitude, so one scale reads for both."""
        spec: DisplaySpec = self.spec
        return spec.mode is DisplayMode.MAGNITUDE and spec.field == vectors

    def lines_title(self, b_name: str) -> str:
        """The field lines' scalar bar title: the integrated field's name and unit."""
        info: FieldInfo = self.field_info(b_name)

        return titled(f"{info.display_name}{LINES_SUFFIX}", info.unit)

    def _remove_lines_bar(self, plotter: pv.Plotter) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context
        if self._lines_bar is not None and self._lines_bar in cast("Mapping[str, object]", plotter.scalar_bars):
            plotter.remove_scalar_bar(self._lines_bar, render=False)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista's _Wrapped stub loses `self`

        self._lines_bar = None

    def line_style(self, options: FieldLineOptions, actors: StreamlineActors) -> FieldLineOptions:
        """
        The field lines in the display's colormap, inversion, log scale and opacity map; a polar display leaves them as asked.

        Their range is the display's while it shows their field's magnitude, else the automatic range of that magnitude over their own region, whatever the bodies show.
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
                "clim": self.clim if self.lines_follow(actors.b_name) else color_range(actors.magnitudes, log_scale=spec.log_scale, robust=spec.robust),
            }
        )
