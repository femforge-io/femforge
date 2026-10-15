"""Field display: pure reduction/color-range/trace logic on numpy arrays, plus pyvista-gated scene tests on a synthetic grid."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from femforge.common.files import BundleFile
from femforge.run.entities import Bodies, Body, BodyRecord, EntitiesFile
from femforge.run.inspect import Run
from femforge.viz.field_display import (
    SCALARS_ARRAY,
    Association,
    BodyPaint,
    BodyStyle,
    DisplayMode,
    DisplaySpec,
    FieldInfo,
    FieldScene,
    SceneActor,
    camera_needs_reset,
    color_range,
    feature_outline,
    field_infos,
    list_fields,
    log_colorbar,
    log_colorbar_ticks,
    log_intensity,
    mesh3d_traces,
    nice_ticks,
    model_dim_of,
    outline_segments,
    outline_traces,
    plotly_colorscale,
    polar_angle,
    polar_rgba,
    reduce_field,
    sample_colormap,
    split_by_body,
    surface_triangles,
)
from femforge.viz.field_lines import FieldLineOptions


if TYPE_CHECKING:
    import pyvista as pv

# A unit cube of bounds, so a camera placed by hand has an obvious relationship to it.
BOX = (0.0, 1.0, 0.0, 1.0, 0.0, 1.0)

VECTORS = np.array([[1.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 3.0], [-1.0, -1.0, 0.0]], dtype=np.float64)
SCALARS = np.array([-2.0, 0.0, 1.0, 4.0], dtype=np.float64)


def _infos(*specs: tuple[str, Association, int]) -> list[FieldInfo]:
    return [FieldInfo(name=name, association=association, components=components, dtype="float64") for name, association, components in specs]


# --- names and enums --------------------------------------------------------


def test_association_maps_to_the_plotly_intensity_mode() -> None:
    assert Association.POINT.intensitymode == "vertex"
    assert Association.CELL.intensitymode == "cell"


def test_display_mode_label_reads_as_words() -> None:
    assert DisplayMode.ABSVALUE.label == "Absolute Value"
    assert DisplayMode.POLAR_XY.label == "Polar XY"


# --- FieldInfo / field_infos ------------------------------------------------


def test_field_info_display_name_applies_the_renames() -> None:
    plain = FieldInfo(name="joule heating", association=Association.CELL, components=1, dtype="float64")
    renamed = FieldInfo(name="Jfix", association=Association.CELL, components=3, dtype="float64")

    assert plain.display_name == "Joule Heating"
    assert renamed.display_name == "Fixed Current Density"


def test_field_info_modes_depend_on_the_component_count() -> None:
    vector = FieldInfo(name="b", association=Association.CELL, components=3, dtype="float64")
    scalar = FieldInfo(name="t", association=Association.POINT, components=1, dtype="float64")

    assert vector.modes == (DisplayMode.MAGNITUDE, DisplayMode.X, DisplayMode.Y, DisplayMode.Z, DisplayMode.POLAR_XY)
    assert scalar.modes == (DisplayMode.VALUE, DisplayMode.ABSVALUE)


def test_field_infos_excludes_geometry_ids_and_sorts_by_display_name() -> None:
    point_arrays: dict[str, np.typing.NDArray[np.generic]] = {"potential": np.zeros(4)}
    cell_arrays: dict[str, np.typing.NDArray[np.generic]] = {"GeometryIds": np.zeros(2, dtype=np.int64), "magnetic flux density e": np.zeros((2, 3))}

    infos = field_infos(point_arrays, cell_arrays)

    assert [info.name for info in infos] == ["magnetic flux density e", "potential"]
    assert [info.components for info in infos] == [3, 1]
    assert [info.association for info in infos] == [Association.CELL, Association.POINT]
    assert infos[1].dtype == "float64"


# --- DisplaySpec.fitted -----------------------------------------------------


def test_fitted_raises_when_the_run_offers_nothing() -> None:
    with pytest.raises(KeyError, match="no displayable fields"):
        _ = DisplaySpec().fitted([])


def test_fitted_takes_the_first_field_and_its_first_allowed_mode() -> None:
    fields = _infos(("b", Association.CELL, 3), ("t", Association.POINT, 1))

    fitted = DisplaySpec().fitted(fields)

    assert fitted.field == "b"
    assert fitted.mode is DisplayMode.MAGNITUDE


def test_fitted_keeps_an_allowed_mode_and_returns_the_same_object() -> None:
    fields = _infos(("t", Association.POINT, 1))
    spec = DisplaySpec(field="t", mode=DisplayMode.ABSVALUE)

    assert spec.fitted(fields) is spec


def test_fitted_resolves_a_loose_name_the_way_a_field_query_does() -> None:
    fields = _infos(("magnetic flux density e", Association.CELL, 3))

    fitted = DisplaySpec(field="magnetic flux density").fitted(fields)

    assert fitted.field == "magnetic flux density e"


def test_fitted_raises_naming_the_available_arrays() -> None:
    fields = _infos(("t", Association.POINT, 1))

    with pytest.raises(KeyError, match="available"):
        _ = DisplaySpec(field="nope").fitted(fields)


# --- reduce_field -----------------------------------------------------------


def test_reduce_field_passes_scalars_through_and_takes_the_absolute_value() -> None:
    assert reduce_field(SCALARS, DisplayMode.VALUE).tolist() == [-2.0, 0.0, 1.0, 4.0]
    assert reduce_field(SCALARS, DisplayMode.ABSVALUE).tolist() == [2.0, 0.0, 1.0, 4.0]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (DisplayMode.X, [1.0, 0.0, 0.0, -1.0]),
        (DisplayMode.Y, [0.0, 2.0, 0.0, -1.0]),
        (DisplayMode.Z, [0.0, 0.0, 3.0, 0.0]),
        (DisplayMode.MAGNITUDE, [1.0, 2.0, 3.0, 2.0**0.5]),
        (DisplayMode.POLAR_XY, [1.0, 2.0, 0.0, 2.0**0.5]),
    ],
)
def test_reduce_field_components_and_magnitudes(mode: DisplayMode, expected: list[float]) -> None:
    assert reduce_field(VECTORS, mode).tolist() == pytest.approx(expected)


def test_reduce_field_rejects_a_non_vector_array_naming_its_shape() -> None:
    with pytest.raises(ValueError, match=r"shape \(4,\)"):
        _ = reduce_field(SCALARS, DisplayMode.MAGNITUDE)


# --- polar angle and RGBA ---------------------------------------------------


def test_polar_angle_maps_the_xy_direction_onto_the_unit_interval() -> None:
    values = np.array([[-1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)

    assert polar_angle(values).tolist() == pytest.approx([1.0, 0.25, 0.5, 0.75])


def test_polar_angle_rejects_a_one_dimensional_array() -> None:
    with pytest.raises(ValueError, match="at least 2 components"):
        _ = polar_angle(SCALARS)


def _grey(fractions: np.typing.NDArray[np.float64]) -> np.typing.NDArray[np.float64]:
    # A stand-in for a cyclic matplotlib colormap: opaque white for every angle, so only the magnitude scaling shows.
    return np.ones((fractions.shape[0], 4), dtype=np.float64)


def test_polar_rgba_scales_brightness_and_alpha_with_the_magnitude() -> None:
    rgba = polar_rgba(VECTORS, 0.0, 2.0, _grey)

    assert rgba.shape == (4, 4)
    assert rgba.dtype == np.float32
    assert rgba[..., 3].tolist() == pytest.approx(rgba[..., 0].tolist())
    assert np.all((rgba >= 0.0) & (rgba <= 1.0))
    # The z-only vector has zero XY magnitude, the y=2 vector the largest -> alpha is ordered the same way.
    assert rgba[2, 3] < rgba[0, 3] < rgba[1, 3]


def test_polar_rgba_treats_a_degenerate_range_as_a_unit_span() -> None:
    flat = polar_rgba(VECTORS, 1.0, 1.0, _grey)

    assert np.all(np.isfinite(flat))


# --- color_range ------------------------------------------------------------


def test_color_range_falls_back_on_an_empty_selection() -> None:
    assert color_range([]) == (0.0, 1.0)
    assert color_range([np.array([np.nan, np.inf])]) == (0.0, 1.0)
    assert color_range([np.array([-1.0, -2.0])], log_scale=True) == (0.0, 1.0)


def test_color_range_clamps_to_the_2_98_percentiles_when_robust() -> None:
    values = np.concatenate([np.linspace(0.0, 1.0, 100), np.array([1000.0])])

    robust = color_range([values])
    raw = color_range([values], robust=False)

    assert robust[1] < 2.0
    assert raw == pytest.approx((0.0, 1000.0))


def test_color_range_spans_every_chunk_and_ignores_non_finite_values() -> None:
    assert color_range([np.array([1.0, np.nan]), np.array([[3.0], [np.inf]])], robust=False) == pytest.approx((1.0, 3.0))


def test_a_small_chunk_keeps_its_robust_range_beside_a_large_one() -> None:
    air = np.linspace(0.0, 0.05, 100_000)
    magnet = np.linspace(0.3, 0.7, 100)

    lo, hi = color_range([air, magnet])

    assert lo == pytest.approx(0.001) and hi == pytest.approx(0.692)


def test_color_range_uses_only_positives_under_a_log_scale() -> None:
    lo, hi = color_range([np.array([-5.0, 1.0, 10.0, 100.0])], log_scale=True)

    assert lo >= 1.0
    assert hi == pytest.approx(100.0)


@pytest.mark.parametrize(
    ("values", "log_scale", "expected"),
    [
        ([5.0, 5.0], False, (0.0, 10.0)),
        ([0.0, 0.0], False, (-1.0, 1.0)),
        ([4.0, 4.0], True, (2.0, 8.0)),
    ],
)
def test_color_range_widens_a_single_valued_field(values: list[float], log_scale: bool, expected: tuple[float, float]) -> None:
    assert color_range([np.array(values)], log_scale=log_scale) == pytest.approx(expected)


# --- camera_needs_reset -----------------------------------------------------


def test_camera_needs_reset_on_unusable_bounds() -> None:
    assert camera_needs_reset((0.0, 0.0, 5.0), (0.0, 0.0, 0.0), None) is True
    assert camera_needs_reset((0.0, 0.0, 5.0), (0.0, 0.0, 0.0), (0.0, np.nan, 0.0, 1.0, 0.0, 1.0)) is True


def test_camera_needs_reset_when_the_view_direction_is_degenerate() -> None:
    assert camera_needs_reset((0.5, 0.5, 5.0), (0.5, 0.5, 5.0), BOX) is True


def test_camera_keeps_its_view_when_it_looks_at_the_box() -> None:
    assert camera_needs_reset((0.5, 0.5, 5.0), (0.5, 0.5, 0.5), BOX) is False


def test_camera_keeps_its_view_from_inside_the_box() -> None:
    # Looking straight at a face from the centre: no corner is within 30 degrees, but the model still fills the view.
    assert camera_needs_reset((0.5, 0.5, 0.5), (0.5, 0.5, 1.0), BOX) is False


def test_camera_needs_reset_when_the_box_is_behind_it() -> None:
    assert camera_needs_reset((0.5, 0.5, 5.0), (0.5, 0.5, 9.0), BOX) is True


def test_camera_needs_reset_when_the_box_is_off_to_the_side() -> None:
    # In front, but far off-axis: every corner sits well outside the 30-degree cone.
    assert camera_needs_reset((0.5, 0.5, 5.0), (500.0, 0.5, 0.0), BOX) is True


# --- plotly colorscale and log ticks ----------------------------------------


def test_plotly_colorscale_builds_evenly_spaced_rgb_stops() -> None:
    samples = np.array([[0.0, 0.0, 0.0, 1.0], [1.0, 0.5, 0.0, 1.0], [1.0, 1.0, 1.0, 1.0]], dtype=np.float64)

    scale = plotly_colorscale(samples)

    assert scale[0] == [0.0, "rgb(0,0,0)"]
    assert scale[1] == [0.5, "rgb(255,128,0)"]
    assert scale[2] == [1.0, "rgb(255,255,255)"]


def test_plotly_colorscale_inverts_the_ramp_not_the_positions() -> None:
    samples = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], dtype=np.float64)

    scale = plotly_colorscale(samples, invert=True)

    assert scale == [[0.0, "rgb(255,255,255)"], [1.0, "rgb(0,0,0)"]]


@pytest.mark.parametrize(
    "samples",
    [
        np.zeros(3),  # not a 2D array
        np.zeros((4, 2)),  # not RGB or RGBA rows
        np.zeros((1, 3)),  # a single stop cannot span a range
    ],
)
def test_plotly_colorscale_rejects_unusable_samples(samples: np.typing.NDArray[np.float64]) -> None:
    with pytest.raises(ValueError):
        _ = plotly_colorscale(samples)


def test_log_colorbar_ticks_covers_every_decade() -> None:
    ticks, labels = log_colorbar_ticks(0.02, 300.0)

    assert ticks == pytest.approx([0.01, 0.1, 1.0, 10.0, 100.0, 1000.0])
    assert labels == ["1e-2", "1e-1", "1e0", "1e1", "1e2", "1e3"]


def test_log_intensity_clamps_to_the_floor_and_needs_a_positive_one() -> None:
    assert log_intensity(np.array([0.0, 1.0, 100.0]), 1.0).tolist() == [0.0, 0.0, 2.0]

    with pytest.raises(ValueError, match="positive floor"):
        _ = log_intensity(np.array([1.0]), 0.0)


def test_log_colorbar_places_the_decades_inside_the_range_at_their_log_positions() -> None:
    assert log_colorbar(0.5, 20.0) == {"tickvals": [0.0, 1.0], "ticktext": ["1e0", "1e1"]}


def test_log_colorbar_falls_back_to_round_numbers_within_one_decade() -> None:
    bar = log_colorbar(298.27, 329.52)

    assert bar["ticktext"] == ["300", "305", "310", "315", "320", "325"]
    assert bar["tickvals"] == pytest.approx([np.log10(tick) for tick in (300, 305, 310, 315, 320, 325)])


def test_nice_ticks_step_by_one_two_or_five_times_a_power_of_ten() -> None:
    assert nice_ticks(0.0, 1.0) == pytest.approx([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    assert nice_ticks(3.0, 47.0) == pytest.approx([10.0, 20.0, 30.0, 40.0])
    assert nice_ticks(0.0, 4.0) == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0])
    assert nice_ticks(100.0, 140.0) == pytest.approx([100.0, 110.0, 120.0, 130.0, 140.0])

    with pytest.raises(ValueError, match="lo < hi"):
        _ = nice_ticks(1.0, 1.0)


def test_log_colorbar_ticks_rejects_non_positive_bounds() -> None:
    with pytest.raises(ValueError, match="positive bounds"):
        _ = log_colorbar_ticks(0.0, 10.0)


# --- plotly traces ----------------------------------------------------------


def test_mesh3d_traces_carries_the_surface_and_its_color_mapping() -> None:
    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)
    triangles = np.array([[0, 1, 2]], dtype=np.int64)

    traces = mesh3d_traces(
        points,
        triangles,
        np.array([7.0], dtype=np.float64),
        name="AIR",
        intensitymode=Association.CELL,
        colorscale=[[0.0, "rgb(0,0,0)"], [1.0, "rgb(255,255,255)"]],
        cmin=0.0,
        cmax=10.0,
        showscale=True,
        hovertemplate="%{intensity}",
    )

    assert len(traces) == 1
    trace = traces[0]
    assert trace["type"] == "mesh3d"
    assert trace["name"] == "AIR"
    assert trace["x"] == [0.0, 1.0, 0.0]
    assert trace["i"] == [0] and trace["j"] == [1] and trace["k"] == [2]
    assert trace["intensity"] == [7.0]
    assert trace["intensitymode"] == "cell"
    assert trace["cmax"] == 10.0
    assert trace["showscale"] is True
    assert trace["visible"] is True and trace["customdata"] is None and trace["colorbar"] is None


def test_mesh3d_traces_carries_hover_data_the_colorbar_and_a_legend_only_body() -> None:
    points = np.zeros((3, 3), dtype=np.float64)
    triangles = np.array([[0, 1, 2]], dtype=np.int64)

    traces = mesh3d_traces(
        points,
        triangles,
        np.array([0.5], dtype=np.float64),
        name="COIL",
        customdata=np.array([3.0], dtype=np.float64),
        colorbar={"tickvals": [0.0]},
        visible=False,
    )

    trace = traces[0]
    assert trace["customdata"] == [3.0]
    assert trace["colorbar"] == {"tickvals": [0.0]}
    assert trace["visible"] == "legendonly" and trace["showlegend"] is True


def test_outline_traces_separates_segments_with_nans() -> None:
    segments = np.array([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], [[1.0, 0.0, 0.0], [1.0, 1.0, 0.0]]], dtype=np.float64)

    traces = outline_traces(segments)

    assert len(traces) == 1
    xs = cast("list[float]", traces[0]["x"])
    assert len(xs) == 6
    assert np.isnan(xs[2]) and np.isnan(xs[5])


def test_outline_traces_of_nothing_is_nothing() -> None:
    assert outline_traces(np.zeros((0, 2, 3), dtype=np.float64)) == []


# --- pyvista-gated: the scene half ------------------------------------------


def _synthetic_grid() -> pv.UnstructuredGrid:  # pragma: no cover - helper for the pyvista-gated tests below
    import pyvista as pv

    cube1 = [(0.0, 0, 0), (1.0, 0, 0), (1.0, 1, 0), (0.0, 1, 0), (0.0, 0, 1), (1.0, 0, 1), (1.0, 1, 1), (0.0, 1, 1)]
    cube2 = [(1.0, 0, 0), (2.0, 0, 0), (2.0, 1, 0), (1.0, 1, 0), (1.0, 0, 1), (2.0, 0, 1), (2.0, 1, 1), (1.0, 1, 1)]
    points = np.array(cube1 + cube2, dtype=np.float64)

    cells = np.array([8, *range(0, 8), 8, *range(8, 16)])
    celltypes = np.array([pv.CellType.HEXAHEDRON, pv.CellType.HEXAHEDRON])
    grid = pv.UnstructuredGrid(cells, celltypes, points)
    grid.cell_data["GeometryIds"] = np.array([1, 2], dtype=np.int32)
    grid.cell_data["joule heating"] = np.array([2.0, 3.0])
    grid.point_data["magnetic flux density"] = np.column_stack([points[:, 0], points[:, 1], np.zeros(len(points))])
    return grid


def _scene() -> FieldScene:  # pragma: no cover - helper for the pyvista-gated tests below
    grid = _synthetic_grid()
    bodies = Bodies([Body(name="LEFT", mesh_id=1), Body(name="RIGHT", mesh_id=2), Body(name="ABSENT", mesh_id=9)])
    return FieldScene(grid=grid, blocks=split_by_body(grid, bodies), fields=list_fields(grid), model_dim=model_dim_of(grid), run_id="synthetic")


def _block(scene: FieldScene, name: str) -> pv.DataSet:  # pragma: no cover - helper for the pyvista-gated tests below
    return cast("pv.DataSet", scene.blocks[name])


def test_split_and_list_read_the_grid_the_way_a_panel_needs() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()

    assert list(scene.blocks.keys()) == ["LEFT", "RIGHT"]  # the body with no cells is dropped
    assert [info.name for info in scene.fields] == ["joule heating", "magnetic flux density"]
    assert model_dim_of(scene.grid) == 3
    assert scene.spec.field == "joule heating"


def test_apply_writes_display_scalars_and_returns_the_color_range() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()
    clim = scene.apply(DisplaySpec(field="joule heating", mode=DisplayMode.VALUE, robust=False))

    assert clim == pytest.approx((2.0, 3.0))
    assert _block(scene, "LEFT").cell_data[SCALARS_ARRAY].tolist() == [2.0]


def test_apply_in_polar_mode_writes_rgba() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()
    _ = scene.apply(DisplaySpec(field="magnetic flux density", mode=DisplayMode.POLAR_XY, colormap="twilight"))

    assert _block(scene, "LEFT").point_data[SCALARS_ARRAY].shape[1] == 4


def test_feature_outline_and_surface_helpers_describe_the_wireframe() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()
    edges = feature_outline(scene.blocks)

    assert edges is not None
    segments = outline_segments(edges)
    assert segments.ndim == 3 and segments.shape[1:] == (2, 3)

    points, triangles = surface_triangles(_block(scene, "LEFT"))
    assert points.shape[1] == 3 and triangles.shape[1] == 3

    assert sample_colormap("viridis", 8).shape == (8, 4)


def test_figure_builds_one_mesh_per_body_plus_the_outline_with_hidden_bodies_legend_only() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()
    figure = scene.figure(DisplaySpec(field="joule heating", hidden_bodies=("LEFT",)))

    data = cast("list[dict[str, object]]", figure["data"])
    assert [trace["type"] for trace in data] == ["mesh3d", "mesh3d", "scatter3d"]
    assert [trace["name"] for trace in data[:2]] == ["LEFT", "RIGHT"]
    assert data[0]["visible"] == "legendonly" and data[1]["visible"] is True
    assert data[0]["showscale"] is False and data[1]["showscale"] is True
    assert data[1]["customdata"] == data[1]["intensity"]

    layout = cast("dict[str, object]", figure["layout"])
    assert layout["uirevision"] == "synthetic"
    assert layout["showlegend"] is True


def test_figure_under_a_log_scale_plots_log10_with_decade_ticks_and_raw_hover_values() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()
    figure = scene.figure(DisplaySpec(field="joule heating", log_scale=True, clim=(1.0, 100.0), outline=False))

    data = cast("list[dict[str, object]]", figure["data"])
    left = data[0]
    assert left["cmin"] == 0.0 and left["cmax"] == 2.0
    assert left["intensity"] == pytest.approx([np.log10(2.0)] * len(cast("list[float]", left["intensity"])))
    assert left["customdata"] == [2.0] * len(cast("list[float]", left["customdata"]))
    colorbar = cast("dict[str, object]", left["colorbar"])
    assert colorbar["tickvals"] == [0.0, 1.0, 2.0] and colorbar["ticktext"] == ["1e0", "1e1", "1e2"]
    assert "customdata" in str(left["hovertemplate"])


def test_apply_records_the_full_data_range_beside_the_robust_color_limits() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()
    clim = scene.apply(DisplaySpec(field="joule heating", clim=(0.0, 10.0)))

    assert clim == (0.0, 10.0)
    assert scene.data_range == pytest.approx((2.0, 3.0))


def test_streamlines_are_integrated_only_when_their_options_change(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.viz.field_lines import StreamlineActor

    scene = _scene()
    plotter = pv.Plotter(off_screen=True)
    scene.render(plotter)
    integrations: list[FieldLineOptions] = []
    integrate = scene.integrate_streamlines

    def counted(options: FieldLineOptions) -> object:
        integrations.append(options)
        return integrate(options)

    monkeypatch.setattr(scene, "integrate_streamlines", counted)
    few = FieldLineOptions(line_count=2)

    scene.update(plotter, DisplaySpec(field="magnetic flux density", streamlines=few))
    scene.update(plotter, DisplaySpec(field="magnetic flux density", invert=True, streamlines=few))
    scene.update(plotter, DisplaySpec(field="magnetic flux density", streamlines=FieldLineOptions(line_count=3)))
    restyled = FieldLineOptions(line_count=3, colormap="lipari", invert=True, opacity=0.5)
    scene.update(plotter, DisplaySpec(field="magnetic flux density", streamlines=restyled))

    assert [options.line_count for options in integrations] == [2, 3], "a colormap, inversion or opacity change restyles the cached lines"
    assert scene.spec.streamlines == restyled and scene.integrated(FieldLineOptions(line_count=4)) is None
    assert str(StreamlineActor.LINES) in plotter.renderer.actors

    scene.update(plotter, DisplaySpec(field="magnetic flux density"))

    assert str(StreamlineActor.LINES) not in plotter.renderer.actors
    plotter.close()


def test_show_streamlines_draws_a_precomputed_integration_and_records_its_options() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.viz.field_lines import StreamlineActor

    scene = _scene()
    plotter = pv.Plotter(off_screen=True)
    scene.render(plotter)
    options = FieldLineOptions(line_count=2)

    actors = scene.integrate_streamlines(options)
    scene.show_streamlines(plotter, actors, options)

    assert scene.spec.streamlines == options
    assert str(StreamlineActor.LINES) in plotter.renderer.actors
    plotter.close()


def test_figure_falls_back_to_the_magnitude_for_polar_mode() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()
    figure = scene.figure(DisplaySpec(field="magnetic flux density", mode=DisplayMode.POLAR_XY, outline=False))

    data = cast("list[dict[str, object]]", figure["data"])
    intensity = cast("list[float]", data[0]["intensity"])
    assert all(isinstance(value, float) for value in intensity)


def test_render_and_update_drive_a_real_plotter_off_screen() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    scene = _scene()
    plotter = pv.Plotter(off_screen=True)

    scene.render(plotter)
    assert scene.spec.field == "joule heating"

    scene.update(plotter, DisplaySpec(field="magnetic flux density", mode=DisplayMode.MAGNITUDE, log_scale=True, invert=True, hidden_bodies=("RIGHT",)))
    scene.update(plotter, DisplaySpec(field="magnetic flux density", mode=DisplayMode.POLAR_XY, outline=False, streamlines=FieldLineOptions(line_count=2)))
    actor = cast("pv.Actor", plotter.renderer.actors[str(SceneActor.BODIES)])
    mapper = actor.mapper
    assert isinstance(mapper, pv.CompositePolyDataMapper)
    assert mapper.color_mode == "direct" and mapper.scalar_map_mode == "point"

    scene.update(plotter, DisplaySpec(field="joule heating", clim=(0.0, 5.0)))
    assert mapper.color_mode == "map" and mapper.scalar_map_mode == "cell"

    plotter.close()


def test_update_before_render_renders_first() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    scene = _scene()
    plotter = pv.Plotter(off_screen=True)

    scene.update(plotter, DisplaySpec(field="joule heating"))

    assert scene.clim == pytest.approx((2.0, 3.0), rel=0.5)
    plotter.close()


def test_from_run_names_bodies_from_the_bundle(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")

    _synthetic_grid().save(str(tmp_path / "case_t0001.vtu"))  # pyright: ignore[reportUnknownMemberType]  # pyvista's save signature is partially untyped
    entities = EntitiesFile(bodies={1: BodyRecord(name="LEFT"), 2: BodyRecord(name="RIGHT")})
    _ = (tmp_path / BundleFile.ENTITIES).write_text(entities.model_dump_json(), encoding="utf-8")

    scene = FieldScene.from_run(Run(tmp_path))

    assert list(scene.blocks.keys()) == ["LEFT", "RIGHT"]
    assert scene.run_id == tmp_path.name


def test_from_run_without_entities_says_so(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")

    _synthetic_grid().save(str(tmp_path / "case_t0001.vtu"))  # pyright: ignore[reportUnknownMemberType]  # pyvista's save signature is partially untyped

    with pytest.raises(FileNotFoundError, match="no named bodies"):
        _ = FieldScene.from_run(Run(tmp_path))


def test_from_run_without_a_vtu_says_so(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")

    with pytest.raises(FileNotFoundError, match="no VTU output"):
        _ = FieldScene.from_run(Run(tmp_path))


def test_a_scene_without_fields_keeps_the_default_spec() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    empty = FieldScene(grid=_synthetic_grid(), blocks=pv.MultiBlock(), fields=[], model_dim=2, run_id="empty")

    assert empty.spec == DisplaySpec()


def test_opacity_and_body_styles_validate_their_range() -> None:
    with pytest.raises(ValueError, match="less than or equal to 1"):
        _ = DisplaySpec(opacity_map=(0.0, 1.5))

    with pytest.raises(ValueError, match="greater than or equal to 0"):
        _ = BodyStyle(body="A", opacity=-0.1)

    assert SceneActor.STYLED.of("A") == "field-styled:A"


def test_opacity_maps_through_the_lookup_table_from_the_low_limit_whatever_the_inversion() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    scene = _scene()
    plotter = pv.Plotter(off_screen=True)
    scene.render(plotter)
    actor = cast("pv.Actor", plotter.renderer.actors[str(SceneActor.BODIES)])

    def ends() -> tuple[float, float]:
        table = cast("object", actor.GetMapper().GetLookupTable())  # pyright: ignore[reportUnknownMemberType]  # GetLookupTable is declared twice in the VTK stubs without @overload
        assert isinstance(table, pv.LookupTable)
        return (round(table.GetTableValue(0)[3], 2), round(table.GetTableValue(table.GetNumberOfTableValues() - 1)[3], 2))

    alphas: list[tuple[float, float]] = []
    for invert in (False, True):
        scene.update(plotter, DisplaySpec(field="magnetic flux density", invert=invert, opacity=0.3, opacity_map=(0.1, 0.9)))
        alphas.append(ends())

    scene.update(plotter, DisplaySpec(field="magnetic flux density", opacity=0.5, body_styles=(BodyStyle(body="LEFT", opacity=0.4),)))
    mapper = actor.mapper
    assert isinstance(mapper, pv.CompositePolyDataMapper)
    blocks = [round(float(mapper.block_attr[index].opacity or 0.0), 2) for index in (1, 2)]

    assert alphas == [(0.1, 0.9), (0.1, 0.9)]
    assert blocks == [0.2, 0.5] and ends() == (1.0, 1.0), "each body draws at its own opacity times the overall one"
    assert plotter.renderer.GetUseDepthPeeling()
    plotter.close()


def test_a_styled_body_draws_in_its_own_actor_and_the_outline_skips_hidden_bodies() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    scene = _scene()
    plotter = pv.Plotter(off_screen=True)
    scene.render(plotter)
    styled = SceneActor.STYLED.of("LEFT")

    scene.update(plotter, DisplaySpec(field="joule heating", body_styles=(BodyStyle(body="LEFT", paint=BodyPaint.SOLID, color="red", opacity=0.3),), hidden_bodies=("RIGHT",)))
    style_actor = cast("pv.Actor", plotter.renderer.actors[styled])
    outline = cast("pv.Actor", plotter.renderer.actors[str(SceneActor.OUTLINE)])
    outline_bounds = outline.bounds
    bars = len(cast("dict[str, object]", plotter.scalar_bars))
    composite_bounds = cast("pv.Actor", plotter.renderer.actors[str(SceneActor.BODIES)]).GetBounds()

    scene.update(plotter, DisplaySpec(field="joule heating", hidden_bodies=("LEFT", "RIGHT")))

    assert not all(map(np.isfinite, composite_bounds)) or composite_bounds[0] > composite_bounds[1], "both bodies leave the composite"
    assert style_actor.GetProperty().GetOpacity() == pytest.approx(0.3) and not style_actor.GetMapper().GetScalarVisibility()
    assert outline_bounds[1] == pytest.approx(1.0), "the hidden RIGHT body is not outlined"
    assert bars == 0, "no visible body takes the colormap"
    assert styled not in plotter.renderer.actors and str(SceneActor.OUTLINE) not in plotter.renderer.actors
    plotter.close()


def test_figure_draws_a_styled_body_in_its_color_and_the_rest_at_the_field_opacity() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()
    figure = scene.figure(DisplaySpec(field="joule heating", opacity=0.5, body_styles=(BodyStyle(body="LEFT", paint=BodyPaint.SOLID, color="#ff0000", opacity=0.2),), hidden_bodies=("RIGHT",)))

    data = cast("list[dict[str, object]]", figure["data"])
    left, right = data[0], data[1]
    outline = data[2]
    xs = [x for x in cast("list[float]", outline["x"]) if x == x]

    assert (left["color"], left["opacity"], left["intensity"]) == ("#ff0000", pytest.approx(0.1), None)
    assert right["opacity"] == 0.5 and right["intensity"] is not None
    assert max(xs) == pytest.approx(1.0), "the hidden RIGHT body is not outlined"


def test_the_color_range_covers_every_body_however_it_is_shown() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()
    every = scene.apply(DisplaySpec(field="joule heating", robust=False))
    without_right = scene.apply(DisplaySpec(field="joule heating", robust=False, hidden_bodies=("RIGHT",)))
    translucent_left = scene.apply(DisplaySpec(field="joule heating", robust=False, body_styles=(BodyStyle(body="LEFT", opacity=0.5),)))
    styled_left = scene.apply(DisplaySpec(field="joule heating", robust=False, body_styles=(BodyStyle(body="LEFT", paint=BodyPaint.SOLID, color="#888888"),)))
    styled_data = scene.data_range
    nothing_shown = scene.apply(DisplaySpec(field="joule heating", robust=False, hidden_bodies=("LEFT", "RIGHT")))

    assert every == pytest.approx((2.0, 3.0))
    assert without_right == every and translucent_left == every and nothing_shown == every, "visibility and opacity leave the range alone"
    assert styled_left == every and styled_data == pytest.approx((2.0, 3.0)), "a solid body still counts"


def test_field_lines_take_the_display_mapping_over_their_region_range_and_restyle_without_integrating(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.viz.field_lines import StreamlineActor

    scene = _scene()
    plotter = pv.Plotter(off_screen=True)
    scene.render(plotter)
    integrations: list[FieldLineOptions] = []
    integrate = scene.integrate_streamlines

    def counted(options: FieldLineOptions) -> object:
        integrations.append(options)
        return integrate(options)

    def table() -> pv.LookupTable:
        actor = cast("pv.Actor", plotter.renderer.actors[str(StreamlineActor.LINES)])
        lookup = cast("object", actor.GetMapper().GetLookupTable())  # pyright: ignore[reportUnknownMemberType]  # GetLookupTable is declared twice in the VTK stubs without @overload
        assert isinstance(lookup, pv.LookupTable)
        return lookup

    monkeypatch.setattr(scene, "integrate_streamlines", counted)
    lines = FieldLineOptions(line_count=2)
    flux = DisplaySpec(field="magnetic flux density", mode=DisplayMode.MAGNITUDE, colormap="lipari", streamlines=lines)

    scene.update(plotter, flux)
    plain = table()
    plain_alpha = (plain.GetTableValue(0)[3], plain.GetTableValue(plain.GetNumberOfTableValues() - 1)[3])
    region = scene.drawn_lines.clim if scene.drawn_lines is not None else None
    scene.update(plotter, flux.model_copy(update={"opacity_map": (0.1, 0.9), "clim": (0.5, 1.5)}))
    mapped = table()
    mapped_alpha = (round(mapped.GetTableValue(0)[3], 2), round(mapped.GetTableValue(mapped.GetNumberOfTableValues() - 1)[3], 2))
    mapped_range = mapped.GetTableRange()
    mapped_bars = list(cast("dict[str, object]", plotter.scalar_bars).keys())
    scene.update(plotter, flux.model_copy(update={"hidden_bodies": ("LEFT",), "opacity_map": (0.1, 0.9)}))
    hidden = scene.drawn_lines.clim if scene.drawn_lines is not None else None
    scene.update(plotter, DisplaySpec(field="joule heating", streamlines=lines))
    other = scene.drawn_lines

    assert len(integrations) == 1, "a display change restyles the cached lines"
    assert plain_alpha == (1.0, 1.0)
    assert mapped_alpha == (0.1, 0.9), "the lines fade like the bodies"
    assert mapped_range == pytest.approx((0.5, 1.5)), "a pinned |B| range reaches the lines"
    assert "Magnetic Flux Density Lines" in mapped_bars
    assert region is not None
    assert hidden == region, "hiding a body leaves the lines' range alone"
    assert other is not None and other.clim == region and other.opacity_map is None and other.colormap == "viridis"
    assert "Magnetic Flux Density Lines" in cast("dict[str, object]", plotter.scalar_bars)
    plotter.close()


def test_field_lines_follow_their_vectors_and_range_over_each_body_of_their_region() -> None:
    pytest.importorskip("pyvista")

    grid = _synthetic_grid()
    grid.point_data["magnetic field strength"] = 5.0 * np.asarray(grid.point_data["magnetic flux density"])
    bodies = Bodies([Body(name="LEFT", mesh_id=1), Body(name="RIGHT", mesh_id=2)])
    scene = FieldScene(grid=grid, blocks=split_by_body(grid, bodies), fields=list_fields(grid), model_dim=3, run_id="synthetic", bodies=bodies)

    whole = scene.integrate_streamlines(FieldLineOptions(line_count=2, vectors="magnetic field strength"))
    left = scene.region_magnitudes("magnetic field strength", 1)
    _ = scene.apply(DisplaySpec(field="magnetic field strength", mode=DisplayMode.MAGNITUDE, clim=(1.0, 2.0)))

    assert whole.b_name == "magnetic field strength" and len(whole.magnitudes) == 2
    assert len(left) == 1 and float(left[0].max()) == pytest.approx(5.0 * np.sqrt(2.0))
    assert scene.lines_pinned("magnetic field strength") and not scene.lines_pinned("magnetic flux density")


def test_a_polar_display_leaves_the_field_lines_as_asked() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()
    _ = scene.apply(DisplaySpec(field="magnetic flux density", mode=DisplayMode.POLAR_XY, colormap="twilight"))
    lines = FieldLineOptions(colormap="viridis")
    actors = scene.integrate_streamlines(lines)

    assert scene.line_style(lines, actors) == lines


def test_colormap_bodies_share_the_body_colormap_and_a_solid_body_needs_a_color() -> None:
    pytest.importorskip("pyvista")
    from femforge.viz.field_display import spread_colors

    spec = DisplaySpec(
        body_colormap="gray",
        body_styles=(
            BodyStyle(body="A", paint=BodyPaint.COLORMAP),
            BodyStyle(body="B", paint=BodyPaint.SOLID, color="#123456"),
            BodyStyle(body="C", paint=BodyPaint.COLORMAP),
            BodyStyle(body="D", color="#ffffff"),
        ),
    )

    assert spec.solid() == {"A": "#404040", "B": "#123456", "C": "#c0c0c0"}, "a field body ignores its color"
    assert spread_colors("gray", 2) == ["#404040", "#c0c0c0"]
    with pytest.raises(ValueError, match="a solid body needs a color"):
        _ = BodyStyle(body="E", paint=BodyPaint.SOLID)


def test_the_field_lines_bar_names_the_field_and_is_made_again_for_new_lines() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.run.fields import GEOMETRY_IDS_ARRAY

    scene = _scene()
    plotter = pv.Plotter(off_screen=True)
    scene.render(plotter)
    whole = FieldLineOptions(line_count=2)
    region = whole.model_copy(update={"region_array": GEOMETRY_IDS_ARRAY, "region_id": 2, "clip_to_region": True})

    scene.update(plotter, DisplaySpec(field="joule heating", streamlines=whole))
    bars = cast("dict[str, object]", plotter.scalar_bars)
    first = bars["Magnetic Flux Density Lines"]
    scene.update(plotter, DisplaySpec(field="joule heating", streamlines=region))
    titles = list(cast("dict[str, object]", plotter.scalar_bars).keys())

    assert sorted(titles) == ["Joule Heating", "Magnetic Flux Density Lines"]
    assert cast("dict[str, object]", plotter.scalar_bars)["Magnetic Flux Density Lines"] is not first, "new lines get a bar over their own mapper"
    plotter.close()


def test_unmatched_names_the_hidden_and_styled_bodies_a_run_lacks() -> None:
    spec = DisplaySpec(
        hidden_bodies=("AIR", "GONE"),
        body_styles=(BodyStyle(body="SEAT", paint=BodyPaint.SOLID, color="#4c00ff"), BodyStyle(body="STEEL1080", paint=BodyPaint.SOLID, color="#c800ff"), BodyStyle(body="GONE")),
    )
    assert spec.unmatched(["AIR", "SEAT", "BEARING"]) == ["GONE", "STEEL1080"]
    assert DisplaySpec().unmatched([]) == []
