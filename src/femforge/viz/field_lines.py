"""3D magnetic field lines from a solved Elmer ``case.vtu``: streamlines seeded through the domain and colored by ``|B|``."""

from __future__ import annotations

import math
import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from femforge.viz.colormaps import register_paraview
from femforge.viz.symmetry import Basis


if TYPE_CHECKING:
    import pyvista as pv

FLUX_DENSITY = "magnetic flux density"
"""The MagnetoDynamicsCalcFields name for B in its nodal form; the elemental form the solver writes by default appends ``" e"``."""

Bounds = tuple[float, float, float, float, float, float]
"""A pyvista axis-aligned bounding box, as ``(xmin, xmax, ymin, ymax, zmin, zmax)``."""

Point3 = tuple[float, float, float]
"""One 3D seed coordinate, as ``(x, y, z)``."""


class SeedPlacement(StrEnum):
    """Where the streamline seed points are placed within the domain bounding box."""

    CENTER_PLANE = "center_plane"
    """A ``line_count`` × ``line_count`` grid on the mid-Z plane (the field-line cross-section), the default."""

    VOLUME = "volume"
    """A ``line_count``³ grid filling the box: denser, fuller 3D coverage."""

    GAP_LINE = "gap_line"
    """A single line of ``line_count`` points along X through the center (e.g. across the Halbach gap)."""


class StreamlineActor(StrEnum):
    """The plotter actor names [`draw_streamlines`][] registers, so toggling the overlay off is a single ``remove_actor``."""

    LINES = "streamlines"
    """The integrated streamlines."""

    OUTLINE = "streamlines-outline"
    """The domain's bounding-box outline."""


class Colormap(StrEnum):
    """Perceptually-uniform matplotlib colormaps for the ``|B|`` magnitude scale."""

    VIRIDIS = "viridis"
    """Dark blue through green to yellow."""

    PLASMA = "plasma"
    """Dark blue through magenta to yellow."""

    MAGMA = "magma"
    """Black through red to pale yellow."""

    TURBO = "turbo"
    """A smooth rainbow from dark blue to dark red."""

    COOLWARM = "coolwarm"
    """A diverging map from blue through grey to red."""


class OpacityTransfer(StrEnum):
    """A pyvista opacity transfer function that maps the ``|B|`` scalar to per-element opacity; the ``_r`` variants reverse the ramp."""

    LINEAR = "linear"
    """Opacity rises linearly with ``|B|``."""

    LINEAR_R = "linear_r"
    """Opacity falls linearly with ``|B|``."""

    GEOM = "geom"
    """Opacity rises geometrically with ``|B|``."""

    GEOM_R = "geom_r"
    """Opacity falls geometrically with ``|B|``."""

    SIGMOID = "sigmoid"
    """Opacity follows a sigmoid in ``|B|``, so weak-field lines fade out."""

    SIGMOID_R = "sigmoid_r"
    """Opacity follows a reversed sigmoid in ``|B|``, so strong-field lines fade out."""


class InterpolatorType(StrEnum):
    """
    The cell-location strategy the streamline integrator uses (pyvista's ``interpolator_type``).

    Both integrate the same point vector field; this only changes how each step finds its containing cell.
    """

    CELL = "cell"
    """Locate cells with a cell locator, the right default for this geometry.

    It stays robust near the air↔magnet interface and thin air gaps, where a point locator mislocates a step into the wrong (magnet) cell and sends the streamline off as a straight "spike".
    """

    POINT = "point"
    """Locate cells via the nearest mesh point (pyvista's default). Faster, but near interfaces/thin features it can grab the wrong
    cell's vector and produce the spike streamlines."""


class FieldLineOptions(BaseModel):
    """Typed options for the 3D field-line render."""

    model_config = ConfigDict(use_attribute_docstrings=True, validate_assignment=True)

    seed_placement: SeedPlacement = SeedPlacement.CENTER_PLANE
    """Where to seed the streamlines within the domain bounding box."""

    seed_grid: bool = Field(default=True)
    """Seed the streamlines on a grid (``True``) or at random points (``False``)."""

    line_count: int = Field(default=18, gt=0)
    """Seed points per axis: ``CENTER_PLANE`` seeds ``line_count²``, ``VOLUME`` ``line_count³``, ``GAP_LINE`` ``line_count``."""

    max_length: float = Field(default=0.75, gt=0.0, le=1.0)
    """`[-]`: Fraction of the domain diagonal a streamline may integrate along, keeping the picture framed on the magnets."""

    colormap: Colormap | str | list[str] = Colormap.TURBO
    """The ``|B|`` colormap: a [`Colormap`][] member, any matplotlib or colorcet colormap name, or a list of colors interpolated across the scalar range."""

    invert: bool = False
    """Reverse the colormap."""

    log_scale: bool = False
    """Map ``|B|`` to colors logarithmically."""

    clim: tuple[float, float] | None = None
    """`[?]`: The ``|B|`` color limits, or None for the lines' own range."""

    opacity_map: tuple[Annotated[float, Field(ge=0.0, le=1.0)], Annotated[float, Field(ge=0.0, le=1.0)]] | None = None
    """`[-]`: The opacity at the low and high color limits, linear in the color mapping between them; it replaces an [`OpacityTransfer`][]."""

    opacity: float | OpacityTransfer = 1.0
    """`[-]`: Line or tube opacity: a constant in ``[0, 1]``, or an [`OpacityTransfer`][] that maps ``|B|`` to opacity like the color."""

    interpolator: InterpolatorType = InterpolatorType.CELL
    """The integrator's cell-location strategy. ``CELL`` (default) fixes the straight "spike" streamlines a point locator produces near the air↔magnet interface."""

    seed_fraction: float = Field(default=0.6, gt=0.0, le=1.0)
    """`[-]`: Fraction of the bounding box, centered, that the seed grid spans, keeping seeds out of the outer air shell where B ≈ 0."""

    region_array: str | None = None
    """Name of a cell-data region-id array (e.g. Elmer's ``"GeometryIds"``) identifying each cell's body. Used with [`region_id`][] and [`clip_to_region`][]."""

    region_id: int | None = None
    """The single region id to confine integration to when [`clip_to_region`][] is set (e.g. the air body id)."""

    clip_to_region: bool = False
    """Confine streamline integration to [`region_id`][] (needs [`region_array`][] and [`region_id`][]), which keeps elemental B from averaging across the air↔magnet interface."""

    tube_radius: float | None = Field(default=None, gt=0.0)
    """`r_tube` `[m]`: Streamline tube radius; None draws the streamlines as plain poly-lines."""

    initial_step_length: float = Field(default=0.5, gt=0.0)
    """`[-]`: The integrator's initial step, in cell lengths; smaller values follow tight curves."""

    min_step_length: float = Field(default=0.01, gt=0.0)
    """`[-]`: The integrator's smallest step, in cell lengths."""

    max_step_length: float = Field(default=1.0, gt=0.0)
    """`[-]`: The integrator's largest step, in cell lengths."""

    show_outline: bool = True
    """Draw the domain bounding-box outline for spatial context."""

    vectors: str | None = None
    """The 3-component array the lines follow; None picks the magnetic flux density."""


DRAW_ONLY: frozenset[str] = frozenset({"colormap", "invert", "log_scale", "clim", "opacity", "opacity_map"})
"""The [`FieldLineOptions`][] fields that only restyle integrated lines, never change them."""


def ramp_alpha(table: pv.LookupTable, low: float, high: float) -> None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration tests
    """Writes a linear opacity ramp into a lookup table, ``low`` at its first entry and ``high`` at its last.

    Args:
        table: The lookup table to change in place.
        low: The opacity of the first entry.
        high: The opacity of the last entry.
    """
    values: np.typing.NDArray[np.float64] = np.array(table.values, dtype=np.float64)
    values[:, 3] = np.round(np.linspace(low, high, len(values)) * 255.0)
    table.values = values


def integrates_like(a: FieldLineOptions, b: FieldLineOptions) -> bool:
    """Whether two option sets integrate the same lines, differing at most in how they are drawn."""
    return a.model_dump(exclude=set(DRAW_ONLY)) == b.model_dump(exclude=set(DRAW_ONLY))


def select_flux_density_array(names: Iterable[str]) -> str:
    """
    Pick the B array from a VTU's array names: the nodal form first (no cell→point projection needed), then the elemental ``"... e"``, then any name containing the phrase (case-insensitive).

    Raises [`KeyError`][] naming the available arrays if none matches.
    """
    by_lower: dict[str, str] = {name.lower(): name for name in names}
    if FLUX_DENSITY in by_lower:
        return by_lower[FLUX_DENSITY]
    if f"{FLUX_DENSITY} e" in by_lower:
        return by_lower[f"{FLUX_DENSITY} e"]
    for lower, original in by_lower.items():
        if FLUX_DENSITY in lower:
            return original
    raise KeyError(f"no '{FLUX_DENSITY}' array in {sorted(by_lower.values())}; was MagnetoDynamicsCalcFields run?")


REVERSAL_COSINE = -0.5
"""The cosine between consecutive steps below which a line has turned back on itself."""


def trimmed_lines(points: np.typing.NDArray[np.float64], lines: np.typing.NDArray[np.int64]) -> tuple[np.typing.NDArray[np.int64], np.typing.NDArray[np.int64], np.typing.NDArray[np.int64]]:
    """
    Every polyline of a flat VTK ``lines`` array cut at its first sharp reversal, as the kept point ids, the renumbered ``lines`` array and the kept polylines' indices.

    A field with sources, such as H at a magnet's pole faces, sends the integrator back and forth across a face until its step limit.
    """
    kept: list[np.typing.NDArray[np.int64]] = []
    flat: list[int] = []
    cells: list[int] = []
    sizes = cast("list[int]", lines.tolist())
    start = 0
    position = 0
    cell = -1
    while position < len(sizes):
        cell += 1
        count = sizes[position]
        ids = lines[position + 1 : position + 1 + count]
        position += count + 1

        steps = np.diff(points[ids], axis=0)
        lengths = np.linalg.norm(steps, axis=1)
        moving = lengths > 0.0
        directions = steps[moving] / lengths[moving, np.newaxis]
        cosines: np.typing.NDArray[np.float64] = np.sum(directions[:-1] * directions[1:], axis=1)
        turns = np.flatnonzero(cosines < REVERSAL_COSINE)
        # A turn between moving steps i and i+1 ends the line at the point the (i+1)th moving step starts from.
        end = int(np.flatnonzero(moving)[turns[0] + 1]) + 1 if turns.size else len(ids)
        if end < 2:
            continue

        kept.append(ids[:end])
        flat.extend([end, *range(start, start + end)])
        cells.append(cell)
        start += end

    ids_kept = np.concatenate(kept) if kept else np.zeros(0, dtype=np.int64)

    return ids_kept, np.asarray(flat, dtype=np.int64), np.asarray(cells, dtype=np.int64)


def trim_reversals(streamlines: pv.PolyData) -> pv.PolyData:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration tests
    """The streamlines cut at their first sharp reversal, with their point and cell data."""
    import pyvista as pv

    ids, lines, cells = trimmed_lines(np.asarray(streamlines.points, dtype=np.float64), np.asarray(streamlines.lines, dtype=np.int64))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # pyvista's lines property is untyped
    if ids.size == 0:
        return pv.PolyData()

    trimmed = pv.PolyData(np.asarray(streamlines.points)[ids], lines=lines)
    for name in streamlines.point_data.keys():
        trimmed.point_data[name] = np.asarray(streamlines.point_data[name])[ids]

    for name in streamlines.cell_data.keys():
        trimmed.cell_data[name] = np.asarray(streamlines.cell_data[name])[cells]

    return trimmed


def _seed_axis(center: float, half_span: float, count: int) -> list[float]:
    """``count`` evenly-spaced coordinates centered on ``center`` spanning ``±half_span`` (a single point at the center when ``count == 1``)."""
    if count <= 1:
        return [center]
    low: float = center - half_span
    step: float = (2.0 * half_span) / (count - 1)
    return [low + step * i for i in range(count)]


def seed_points(bounds: Bounds, options: FieldLineOptions) -> list[Point3]:
    """
    The ``(N, 3)`` streamline seed coordinates for the domain ``bounds`` and ``options`` (pure Python — unit-testable without a render context).

    The grid is centered and spans ``seed_fraction`` of each axis, keeping seeds out of the outer air shell where B ≈ 0.
    """
    xmin, xmax, ymin, ymax, zmin, zmax = bounds

    cx, cy, cz = (xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2
    f: float = options.seed_fraction
    hx, hy, hz = (xmax - xmin) * f / 2, (ymax - ymin) * f / 2, (zmax - zmin) * f / 2

    n: int = options.line_count
    xs: list[float] = _seed_axis(cx, hx, n)
    ys: list[float] = _seed_axis(cy, hy, n)
    zs: list[float] = _seed_axis(cz, hz, n)

    dims: float = 3.0
    match options.seed_placement:
        case SeedPlacement.GAP_LINE:
            dims = 1.0
        case SeedPlacement.CENTER_PLANE:
            dims = 2.0
        case SeedPlacement.VOLUME:
            dims = 3.0

    # Reduce n to keep the n constant regardless of dim
    n = int(n ** (1.0 / dims))  # pyright: ignore[reportAny]
    if options.seed_grid:
        match options.seed_placement:
            case SeedPlacement.GAP_LINE:
                return [(x, cy, cz) for x in xs]
            case SeedPlacement.CENTER_PLANE:
                return [(x, y, cz) for y in ys for x in xs]
            case SeedPlacement.VOLUME:
                return [(x, y, z) for z in zs for y in ys for x in xs]
    else:
        match options.seed_placement:
            case SeedPlacement.GAP_LINE:
                return [(random.uniform(xmin, xmax), cy, cz) for _ in range(n)]
            case SeedPlacement.CENTER_PLANE:
                return [(random.uniform(xmin, xmax), random.uniform(ymin, ymax), cz) for _ in range(n * n)]
            case SeedPlacement.VOLUME:
                return [(random.uniform(xmin, xmax), random.uniform(ymin, ymax), random.uniform(zmin, zmax)) for _ in range(n * n * n)]


def render_field_lines(
    vtu_path: str | Path,
    options: FieldLineOptions | None = None,
    *,
    off_screen: bool = False,
    screenshot: str | Path | None = None,
    plotter: pv.Plotter | None = None,
) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context; proven by the gated integration test
    """Render the magnetic flux-density field lines from a solved ``case.vtu``, headless to a PNG or in an interactive window.

    Args:
        vtu_path: The solved ``case.vtu``.
        options: The field-line options; None takes the defaults.
        off_screen: Render without a window.
        screenshot: The PNG to write when rendering off screen.
        plotter: A live plotter to add the lines to instead of opening one.
    """
    import pyvista as pv

    opts: FieldLineOptions = options if options is not None else FieldLineOptions()
    actors: StreamlineActors = build_streamlines_from_vtu(vtu_path, opts)

    own_plotter: bool = plotter is None
    if plotter is None:
        plotter = pv.Plotter(off_screen=off_screen)

    draw_streamlines(actors, opts, plotter)

    if own_plotter:
        screenshot_arg: str | bool = str(screenshot) if screenshot is not None else False
        plotter.show(screenshot=screenshot_arg)  # pyright: ignore[reportUnknownMemberType]


@dataclass
class StreamlineActors:
    """
    The precomputed drawables for a field-line view, split from plotting so the expensive part runs off the UI thread.

    Built by [`build_streamlines`][] (worker-thread safe) and drawn by [`draw_streamlines`][] (plotter thread only).
    """

    streamlines: pv.PolyData
    """The integrated streamlines, within the integration region."""

    b_name: str
    """The name of the B array the lines carry for coloring."""

    outline: pv.PolyData | None
    """The domain's bounding-box outline; None when ``show_outline`` is off."""

    magnitudes: list[np.typing.NDArray[np.float64]]
    """The integrated field's magnitudes over the integration region, one chunk per body, which set the lines' color range."""


def build_streamlines(
    dataset: pv.DataSet, b_name: str, options: FieldLineOptions | None = None
) -> StreamlineActors:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration tests
    """Seed and integrate |B| streamlines through a dataset that already carries B as **point** data, without touching a plotter.

    Args:
        dataset: The dataset carrying B on its points.
        b_name: The point array the lines follow.
        options: The field-line options; None takes the defaults.
    """
    import pyvista as pv

    opts: FieldLineOptions = options if options is not None else FieldLineOptions()
    bounds: Bounds = cast("Bounds", tuple(dataset.bounds))
    seeds: pv.PolyData = pv.PolyData(seed_points(bounds, opts))

    # Far from the magnets the air field is weak and near-uniform, so the arc length is bounded by a share of the domain diagonal to keep the picture framed on the magnets.
    xmin, xmax, ymin, ymax, zmin, zmax = bounds
    dx, dy, dz = xmax - xmin, ymax - ymin, zmax - zmin
    max_length: float = math.sqrt(dx * dx + dy * dy + dz * dz) * opts.max_length
    streamlines: pv.PolyData = cast(
        "pv.PolyData",
        dataset.streamlines_from_source(
            seeds,
            vectors=b_name,
            integration_direction="both",
            max_length=max_length,
            initial_step_length=opts.initial_step_length,
            min_step_length=opts.min_step_length,
            max_step_length=opts.max_step_length,
            interpolator_type=opts.interpolator.value,
        ),
    )
    streamlines = trim_reversals(streamlines)

    # Tubes when a radius is given, else raw poly-lines (cheaper, the right look for a dense field). pyvista's
    # geometry methods carry `**kwargs: Unknown`, so pin the typed return with a cast and silence the member report.
    tubed: pv.PolyData = (
        cast("pv.PolyData", streamlines.tube(radius=opts.tube_radius))  # pyright: ignore[reportUnknownMemberType]
        if opts.tube_radius is not None
        else streamlines
    )
    outline: pv.PolyData | None = cast("pv.PolyData", dataset.outline()) if opts.show_outline else None
    magnitudes: np.typing.NDArray[np.float64] = np.linalg.norm(np.asarray(dataset.point_data[b_name], dtype=np.float64).reshape(dataset.n_points, -1), axis=1)
    return StreamlineActors(streamlines=tubed, b_name=b_name, outline=outline, magnitudes=[magnitudes])


SEED_IDS = "SeedIds"
"""The cell array vtkStreamTracer writes with each line's seed index; a "both" trace gives each seed a forward and a backward line."""


def build_swept_streamlines(
    section: pv.DataSet,
    b_name: str,
    options: FieldLineOptions,
    basis: Basis,
    bounds: Bounds,
    *,
    seeds: np.typing.NDArray[np.float64] | None = None,
) -> StreamlineActors:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration tests
    """Field lines of a revolved axisymmetric section: seeded in the authored frame, folded into the section, integrated there and swept back to each seed's angle.

    Args:
        section: The solved section in the solve frame, carrying B on its points.
        b_name: The point array the lines follow.
        options: The field-line options.
        basis: Where the section sits in the authored frame.
        bounds: The revolved display's authored bounds, which the seeds fill and the outline draws.
        seeds: ``(N, 3)`` authored seed points to use instead of seeding ``bounds``.
    """
    import pyvista as pv

    authored: np.typing.NDArray[np.float64] = seeds if seeds is not None else np.asarray(seed_points(bounds, options), dtype=np.float64)
    folded, theta = basis.fold(authored)

    xmin, xmax, ymin, ymax, zmin, zmax = cast("Bounds", tuple(section.bounds))
    max_length: float = math.sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2) * options.max_length
    traced: pv.PolyData = cast(
        "pv.PolyData",
        section.streamlines_from_source(
            pv.PolyData(folded),
            vectors=b_name,
            integration_direction="both",
            max_length=max_length,
            initial_step_length=options.initial_step_length,
            min_step_length=options.min_step_length,
            max_step_length=options.max_step_length,
            interpolator_type=options.interpolator.value,
        ),
    )
    lines: pv.PolyData = trim_reversals(traced)

    if lines.n_points:
        counts: np.typing.NDArray[np.int64] = np.diff(np.asarray(lines.line_offsets, dtype=np.int64))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # pyvista types line_offsets loosely
        point_theta: np.typing.NDArray[np.float64] = np.repeat(theta[np.asarray(lines.cell_data[SEED_IDS], dtype=np.int64)], counts)
        lines.points = basis.place(np.asarray(lines.points, dtype=np.float64), point_theta)
        for name in lines.point_data.keys():
            lines.point_data[name] = basis.carry(np.asarray(lines.point_data[name]), point_theta)

    tubed: pv.PolyData = (
        cast("pv.PolyData", lines.tube(radius=options.tube_radius))  # pyright: ignore[reportUnknownMemberType]
        if options.tube_radius is not None and lines.n_points
        else lines
    )
    outline: pv.PolyData | None = cast("pv.PolyData", pv.Box(bounds).outline()) if options.show_outline else None  # pyright: ignore[reportUnknownMemberType]  # pyvista types Box bounds loosely
    magnitudes: np.typing.NDArray[np.float64] = np.linalg.norm(np.asarray(section.point_data[b_name], dtype=np.float64).reshape(section.n_points, -1), axis=1)

    return StreamlineActors(streamlines=tubed, b_name=b_name, outline=outline, magnitudes=[magnitudes])


def prepare_streamline_source(grid: pv.UnstructuredGrid, options: FieldLineOptions) -> tuple[pv.DataSet, str]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """Narrow a read grid to the integration region and put B on its points, returning the dataset and the B array name — the read-independent half of [`build_streamlines_from_vtu`][]."""
    # Viz toggle: confine integration to one region (e.g. air) by thresholding to that body BEFORE projecting B to
    # points. This kills the "spike" streamlines: the cell->point average then never mixes across the air<->magnet
    # interface, so air-side nodes don't inherit the magnets' huge magnetization-aligned B.
    if options.clip_to_region and options.region_array is not None and options.region_id is not None and options.region_array in grid.cell_data:
        rid: float = float(options.region_id)
        grid = cast("pv.UnstructuredGrid", grid.threshold([rid, rid], scalars=options.region_array, preference="cell"))  # pyright: ignore[reportUnknownMemberType]

    b_name: str = options.vectors if options.vectors is not None else select_flux_density_array(list(grid.array_names))
    # A true nodal B is integrated as-is; the elemental "... e" form (cell data) is projected to points here.
    sampled: pv.DataSet = cast("pv.DataSet", grid.cell_data_to_point_data() if b_name in grid.cell_data else grid)
    return (sampled, b_name)


def build_streamlines_from_vtu(vtu_path: str | Path, options: FieldLineOptions | None = None) -> StreamlineActors:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """Read a solved ``case.vtu`` and integrate its B-field streamlines into a drawable [`StreamlineActors`][] — no plotter touched, safe off the UI thread."""
    import pyvista as pv

    opts: FieldLineOptions = options if options is not None else FieldLineOptions()
    grid: pv.UnstructuredGrid = cast("pv.UnstructuredGrid", pv.read(str(vtu_path)))

    sampled, b_name = prepare_streamline_source(grid, opts)
    return build_streamlines(sampled, b_name, opts)


def draw_streamlines(
    actors: StreamlineActors, options: FieldLineOptions, plotter: pv.Plotter
) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context; proven by the gated integration tests
    """Draw a precomputed [`StreamlineActors`][] into ``plotter`` without clearing or showing it, so it composes inside a [`Scene`][femforge.viz.scene.Scene].

    Args:
        actors: The integrated lines; an empty set removes any lines drawn before.
        options: The field-line options that style them.
        plotter: The plotter to draw into, on its own thread.
    """
    if actors.streamlines.n_points == 0:
        _ = plotter.remove_actor(str(StreamlineActor.LINES))  # pyright: ignore[reportUnknownMemberType, reportArgumentType]  # pyvista removes by actor name, and its _Wrapped stub loses `self`
        return

    register_paraview()

    # The scalar bar title avoids a literal "|" -- VTK's off-screen text renderer crashes on a pipe glyph.
    # opacity: a constant float, or an OpacityTransfer name; pyvista types it as a Literal a StrEnum can't satisfy
    # statically even though it's valid at runtime -- hence the reportArgumentType ignore.
    actor = plotter.add_mesh(  # pyright: ignore[reportUnknownMemberType]
        actors.streamlines,
        scalars=actors.b_name,
        cmap=options.colormap,
        flip_scalars=options.invert,
        clim=options.clim,
        log_scale=options.log_scale,
        opacity=options.opacity,  # pyright: ignore[reportArgumentType]
        lighting=False,
        show_scalar_bar=False,
        scalar_bar_args={"title": "B (T)"},
        label=f"streamlines ({options.seed_placement.value})",
        name=str(StreamlineActor.LINES),
    )
    if options.opacity_map is not None:
        ramp_alpha(actor.mapper.lookup_table, *options.opacity_map)

    if actors.outline is not None:
        plotter.add_mesh(actors.outline, color="gray", name=str(StreamlineActor.OUTLINE))  # pyright: ignore[reportUnknownMemberType]


def render_streamlines(
    dataset: pv.DataSet,
    b_name: str,
    options: FieldLineOptions | None = None,
    *,
    off_screen: bool = False,
    screenshot: str | Path | None = None,
    plotter: pv.Plotter | None = None,
) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context; proven by the gated integration tests
    """One-shot: build streamlines through ``dataset`` and draw them, in a plotter of its own when ``plotter`` is None.

    Args:
        dataset: The dataset carrying B on its points.
        b_name: The point array the lines follow.
        options: The field-line options; None takes the defaults.
        off_screen: Render without a window.
        screenshot: The PNG to write when rendering off screen.
        plotter: A live plotter to add the lines to instead of opening one.
    """
    import pyvista as pv

    opts: FieldLineOptions = options if options is not None else FieldLineOptions()
    actors: StreamlineActors = build_streamlines(dataset, b_name, opts)

    own_plotter: bool = plotter is None
    if plotter is None:
        plotter = pv.Plotter(off_screen=off_screen)

    draw_streamlines(actors, opts, plotter)

    if own_plotter:
        screenshot_arg: str | bool = str(screenshot) if screenshot is not None else False
        plotter.show(screenshot=screenshot_arg)  # pyright: ignore[reportUnknownMemberType]


@dataclass
class FieldLines:
    """
    A [`Renderable`][femforge.viz.scene.Renderable] for the solved B-field streamlines of one ``case.vtu``.

    VTU read + streamline integration run in ``__post_init__`` (whichever worker thread constructs it); ``render`` only draws precomputed actors, keeping the plotter thread free.
    """

    vtu_path: str | Path
    """The solved ``case.vtu`` whose B field the lines follow."""

    options: FieldLineOptions | None = None
    """The streamline options; None uses the defaults."""

    _actors: StreamlineActors | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
        self._actors = build_streamlines_from_vtu(self.vtu_path, self.options)

    def render(self, plotter: pv.Plotter) -> None:  # pragma: no cover - needs pyvista (viz extra) + a VTK render context
        """Draw the precomputed field lines into ``plotter``."""
        assert self._actors is not None  # set in __post_init__

        options = self.options if self.options is not None else FieldLineOptions()
        draw_streamlines(self._actors, options, plotter)
