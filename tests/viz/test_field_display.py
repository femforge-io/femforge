"""Field display: pure reduction/color-range/trace logic on numpy arrays, plus pyvista-gated scene tests on a synthetic grid."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from femforge.common.files import BundleFile
from femforge.run.entities import Bodies, Body, BodyRecord, EntitiesFile
from femforge.run.inspect import Run
from femforge.run.mechanical import VOIGT_COMPONENTS, derived_arrays
from femforge.viz.field_display import (
    SCALARS_ARRAY,
    Association,
    BodyPaint,
    BodyStyle,
    DisplayMode,
    DisplaySpec,
    FieldInfo,
    FieldKind,
    FieldScene,
    Legend,
    SceneActor,
    bar_placement,
    camera_needs_reset,
    color_range,
    feature_outline,
    field_infos,
    field_kind,
    line_vectors,
    list_fields,
    model_dim_of,
    polar_angle,
    polar_rgba,
    reduce_field,
    run_manifest,
    sample_colormap,
    scaled_basis,
    split_by_body,
)
from femforge.viz.field_lines import FieldLineOptions, SeedPlacement, prepare_streamline_source
from femforge.viz.symmetry import Expansion


if TYPE_CHECKING:
    import pyvista as pv

# A unit cube of bounds, so a camera placed by hand has an obvious relationship to it.
BOX = (0.0, 1.0, 0.0, 1.0, 0.0, 1.0)

VECTORS = np.array([[1.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 3.0], [-1.0, -1.0, 0.0]], dtype=np.float64)
SCALARS = np.array([-2.0, 0.0, 1.0, 4.0], dtype=np.float64)


def _infos(*specs: tuple[str, Association, int]) -> list[FieldInfo]:
    return [FieldInfo(name=name, association=association, components=components, dtype="float64") for name, association, components in specs]


# --- names and enums --------------------------------------------------------




def test_display_mode_label_reads_as_words() -> None:
    assert DisplayMode.ABSVALUE.label == "Absolute Value"
    assert DisplayMode.POLAR_XY.label == "Polar XY"
    assert DisplayMode.MAX_PRINCIPAL.label == "Max Principal"


# --- FieldInfo / field_infos ------------------------------------------------


def test_a_field_is_titled_with_its_unit_when_it_has_one() -> None:
    flux = FieldInfo(name="magnetic flux density", association=Association.POINT, components=3, dtype="float64", unit="T")
    plain = FieldInfo(name="custom", association=Association.POINT, components=1, dtype="float64")

    assert flux.title == "Magnetic Flux Density [T]" and plain.title == "Custom"


def test_field_infos_take_their_units_from_the_variable_catalogue_and_the_physics() -> None:
    from femforge.elmer.physics import Physics

    arrays = {"potential": SCALARS, "magnetic flux density e": VECTORS}
    unitless = {info.name: info.unit for info in field_infos({}, arrays)}
    magnetic = {info.name: info.unit for info in field_infos({}, arrays, Physics.MAGNETOSTATICS_2D)}
    electric = {info.name: info.unit for info in field_infos({}, arrays, Physics.CURRENT_CONDUCTION)}

    assert unitless == {"potential": None, "magnetic flux density e": "T"}
    assert magnetic["potential"] == "T·m" and electric["potential"] == "V"


def test_field_info_display_name_applies_the_renames() -> None:
    plain = FieldInfo(name="joule heating", association=Association.CELL, components=1, dtype="float64")
    renamed = FieldInfo(name="Jfix", association=Association.CELL, components=3, dtype="float64")

    assert plain.display_name == "Joule Heating"
    assert renamed.display_name == "Fixed Current Density"


def test_field_info_modes_depend_on_the_component_count() -> None:
    vector = FieldInfo(name="b", association=Association.CELL, components=3, dtype="float64")
    scalar = FieldInfo(name="t", association=Association.POINT, components=1, dtype="float64")

    principal = FieldInfo(name="principal stress", association=Association.POINT, components=3, dtype="float64")
    tensor = FieldInfo(name="stress", association=Association.POINT, components=6, dtype="float64")

    assert vector.modes == (DisplayMode.MAGNITUDE, DisplayMode.X, DisplayMode.Y, DisplayMode.Z, DisplayMode.POLAR_XY)
    assert scalar.modes == (DisplayMode.VALUE, DisplayMode.ABSVALUE)
    assert principal.modes == (DisplayMode.MAX_PRINCIPAL, DisplayMode.MID_PRINCIPAL, DisplayMode.MIN_PRINCIPAL)
    assert tensor.modes == ()


@pytest.mark.parametrize(
    ("name", "components", "kind"),
    [
        ("vonmises", 1, FieldKind.SCALAR),
        ("displacement", 3, FieldKind.VECTOR),
        ("magnetic flux density e", 3, FieldKind.VECTOR),
        ("principal stress", 3, FieldKind.PRINCIPAL),
        ("Principal Strain e", 3, FieldKind.PRINCIPAL),
        ("tresca", 1, FieldKind.SCALAR),
        ("stress", 6, FieldKind.TENSOR),
        ("strain", 4, FieldKind.TENSOR),
        ("stress_xx", 1, FieldKind.TENSOR),
        ("Strain_XZ e", 1, FieldKind.TENSOR),
        ("custom_xx", 1, FieldKind.SCALAR),
        ("stress_ab", 1, FieldKind.SCALAR),
    ],
)
def test_field_kind_follows_the_width_and_the_principal_variables(name: str, components: int, kind: FieldKind) -> None:
    assert field_kind(name, components) is kind


def test_field_infos_excludes_geometry_ids_and_sorts_by_display_name() -> None:
    point_arrays: dict[str, np.typing.NDArray[np.generic]] = {"potential": np.zeros(4)}
    cell_arrays: dict[str, np.typing.NDArray[np.generic]] = {"GeometryIds": np.zeros(2, dtype=np.int64), "magnetic flux density e": np.zeros((2, 3))}

    infos = field_infos(point_arrays, cell_arrays)

    assert [info.name for info in infos] == ["magnetic flux density e", "potential"]
    assert [info.components for info in infos] == [3, 1]
    assert [info.association for info in infos] == [Association.CELL, Association.POINT]
    assert infos[1].dtype == "float64"


def _elastic_arrays() -> dict[str, np.typing.NDArray[np.float64]]:
    """The point arrays of an Elmer elasticity VTU, its split tensor columns stacked by the derived fields."""
    columns: dict[str, np.typing.NDArray[np.float64]] = {f"{variable}_{component}": np.full(4, 1.0) for variable in ("stress", "strain") for component in VOIGT_COMPONENTS}
    solved: dict[str, np.typing.NDArray[np.float64]] = {
        **columns,
        "vonmises": np.zeros(4),
        "tresca": np.zeros(4),
        "displacement": np.zeros((4, 3)),
        "principal stress": np.zeros((4, 3)),
        "principal strain": np.zeros((4, 3)),
    }

    return {**solved, **derived_arrays(solved)}


def test_field_infos_leave_out_tensors_and_their_columns_and_keep_the_elasticity_fields() -> None:
    arrays = _elastic_arrays()
    infos = field_infos(arrays, {})

    assert {"stress", "strain", "stress_xx", "strain_xz"} <= arrays.keys()
    assert [info.name for info in infos] == ["displacement", "principal strain", "principal stress", "strain energy density", "tresca", "vonmises"]
    assert [info.kind for info in infos] == [FieldKind.VECTOR, FieldKind.PRINCIPAL, FieldKind.PRINCIPAL, FieldKind.SCALAR, FieldKind.SCALAR, FieldKind.SCALAR]


def test_elasticity_fields_take_their_units_and_names() -> None:
    titles = [info.title for info in field_infos(_elastic_arrays(), {})]

    assert titles == [
        "Displacement [m]",
        "Principal Strain [-]",
        "Principal Stress [Pa]",
        "Strain Energy Density [J/m³]",
        "Tresca [Pa]",
        "Von Mises [Pa]",
    ]
    assert FieldInfo(name="vonmises e", association=Association.CELL, components=1, dtype="float64").display_name == "Von Mises E"


def test_line_vectors_prefer_the_shown_vector_then_the_flux_density_then_the_first_vector() -> None:
    magnetic = _infos(("joule heating", Association.CELL, 1), ("magnetic field strength", Association.POINT, 3), ("magnetic flux density e", Association.CELL, 3))
    elastic = _infos(("displacement", Association.POINT, 3), ("principal stress", Association.POINT, 3), ("vonmises", Association.POINT, 1))

    assert line_vectors(magnetic, "magnetic field strength") == "magnetic field strength"
    assert line_vectors(magnetic, "joule heating") == "magnetic flux density e"
    assert line_vectors(elastic, "vonmises") == "displacement"
    assert line_vectors(elastic, "principal stress") == "displacement", "principal values are not a direction to follow"
    assert line_vectors(_infos(("temperature", Association.POINT, 1)), "temperature") is None


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


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (DisplayMode.MAX_PRINCIPAL, [3.0, 5.0]),
        (DisplayMode.MID_PRINCIPAL, [1.0, -1.0]),
        (DisplayMode.MIN_PRINCIPAL, [-2.0, -4.0]),
    ],
)
def test_principal_modes_pick_the_sorted_values(mode: DisplayMode, expected: list[float]) -> None:
    unsorted = np.array([[1.0, -2.0, 3.0], [-4.0, 5.0, -1.0]])

    assert reduce_field(unsorted, mode).tolist() == expected


def test_reduce_field_rejects_a_principal_mode_on_a_scalar() -> None:
    with pytest.raises(ValueError, match=r"max_principal.*shape \(4,\)"):
        _ = reduce_field(SCALARS, DisplayMode.MAX_PRINCIPAL)


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


def test_feature_outline_describes_the_wireframe() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()
    edges = feature_outline(scene.blocks)

    assert edges is not None and edges.n_lines > 0
    assert sample_colormap("viridis", 8).shape == (8, 4)






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

    scene.update(plotter, DisplaySpec(field="magnetic flux density", streamlines=None))

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

    scene.update(plotter, DisplaySpec(field="magnetic flux density", opacity=0.5, opacity_map=None, body_styles=(BodyStyle(body="LEFT", opacity=0.4),)))
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

    scene.update(plotter, DisplaySpec(field="joule heating", outline=True, body_styles=(BodyStyle(body="LEFT", paint=BodyPaint.SOLID, color="red", opacity=0.3),), hidden_bodies=("RIGHT",)))
    style_actor = cast("pv.Actor", plotter.renderer.actors[styled])
    outline = cast("pv.Actor", plotter.renderer.actors[str(SceneActor.OUTLINE)])
    outline_bounds = outline.bounds
    bars = len(cast("dict[str, object]", plotter.scalar_bars))
    composite_bounds = cast("pv.Actor", plotter.renderer.actors[str(SceneActor.BODIES)]).GetBounds()

    scene.update(plotter, DisplaySpec(field="joule heating", outline=True, hidden_bodies=("LEFT", "RIGHT")))

    assert not all(map(np.isfinite, composite_bounds)) or composite_bounds[0] > composite_bounds[1], "both bodies leave the composite"
    assert style_actor.GetProperty().GetOpacity() == pytest.approx(0.3) and not style_actor.GetMapper().GetScalarVisibility()
    assert outline_bounds[1] == pytest.approx(1.0), "the hidden RIGHT body is not outlined"
    assert bars == 0, "no visible body takes the colormap"
    assert styled not in plotter.renderer.actors and str(SceneActor.OUTLINE) not in plotter.renderer.actors
    plotter.close()




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
    flux = DisplaySpec(field="magnetic flux density", mode=DisplayMode.MAGNITUDE, colormap="lipari", opacity_map=None, legend=Legend.VERTICAL, streamlines=lines)

    scene.update(plotter, flux)
    plain = table()
    plain_alpha = (plain.GetTableValue(0)[3], plain.GetTableValue(plain.GetNumberOfTableValues() - 1)[3])
    auto_bars = list(cast("dict[str, object]", plotter.scalar_bars).keys())
    region = scene.drawn_lines.clim if scene.drawn_lines is not None else None
    scene.update(plotter, flux.model_copy(update={"opacity_map": (0.1, 0.9), "clim": (0.5, 1.5)}))
    mapped = table()
    mapped_alpha = (round(mapped.GetTableValue(0)[3], 2), round(mapped.GetTableValue(mapped.GetNumberOfTableValues() - 1)[3], 2))
    mapped_range = mapped.GetTableRange()
    mapped_bars = list(cast("dict[str, object]", plotter.scalar_bars).keys())
    scene.update(plotter, flux.model_copy(update={"hidden_bodies": ("LEFT",), "opacity_map": (0.1, 0.9)}))
    hidden = scene.drawn_lines.clim if scene.drawn_lines is not None else None
    scene.update(plotter, DisplaySpec(field="joule heating", legend=Legend.VERTICAL, streamlines=lines))
    other = scene.drawn_lines

    assert len(integrations) == 1, "a display change restyles the cached lines"
    assert plain_alpha == (1.0, 1.0)
    assert mapped_alpha == (0.1, 0.9), "the lines fade like the bodies"
    assert mapped_range == pytest.approx((0.5, 1.5)), "a pinned |B| range reaches the lines"
    assert auto_bars == ["Magnetic Flux Density [T]"] and mapped_bars == ["Magnetic Flux Density [T]"], "lines of the shown field share its bar under an automatic range and a pinned one"
    assert region is not None
    assert hidden == region, "hiding a body leaves the lines' range alone"
    assert other is not None and other.clim == region and other.opacity_map == (0.15, 1.0) and other.colormap == "turbo"
    assert "Magnetic Flux Density Lines [T]" in cast("dict[str, object]", plotter.scalar_bars), "lines following another field get a bar of their own, titled with their unit"
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
    assert scene.lines_follow("magnetic field strength") and not scene.lines_follow("magnetic flux density")


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
    from vtkmodules.vtkRenderingAnnotation import vtkScalarBarActor

    from femforge.run.fields import GEOMETRY_IDS_ARRAY

    scene = _scene()
    plotter = pv.Plotter(off_screen=True)
    scene.render(plotter)
    whole = FieldLineOptions(line_count=2)
    region = whole.model_copy(update={"region_array": GEOMETRY_IDS_ARRAY, "region_id": 2, "clip_to_region": True})

    scene.update(plotter, DisplaySpec(field="joule heating", legend=Legend.VERTICAL, streamlines=whole))
    bars = cast("dict[str, object]", plotter.scalar_bars)
    first = bars["Magnetic Flux Density Lines [T]"]
    scene.update(plotter, DisplaySpec(field="joule heating", legend=Legend.VERTICAL, streamlines=region))
    titles = list(cast("dict[str, object]", plotter.scalar_bars).keys())
    columns = {bar.GetPosition()[0] for bar in cast("dict[str, object]", plotter.scalar_bars).values() if isinstance(bar, vtkScalarBarActor)}
    second = cast("dict[str, object]", plotter.scalar_bars)["Magnetic Flux Density Lines [T]"]
    scene.update(plotter, DisplaySpec(field="joule heating", legend=Legend.HORIZONTAL, streamlines=region))
    horizontal = [bar.GetOrientation() for bar in cast("dict[str, object]", plotter.scalar_bars).values() if isinstance(bar, vtkScalarBarActor)]
    scene.update(plotter, DisplaySpec(field="joule heating", streamlines=region))
    hidden = list(cast("dict[str, object]", plotter.scalar_bars).keys())

    assert sorted(titles) == ["Joule Heating [W/m³]", "Magnetic Flux Density Lines [T]"]
    assert second is not first, "new lines get a bar over their own mapper"
    assert len(columns) == 2, "the lines' bar stands beside the field's, never on it"
    assert horizontal == [0, 0], "a horizontal legend lays both bars along the bottom"
    assert hidden == [], "a hidden legend draws no bar"
    plotter.close()


def test_unmatched_names_the_hidden_and_styled_bodies_a_run_lacks() -> None:
    spec = DisplaySpec(
        hidden_bodies=("AIR", "GONE"),
        body_styles=(BodyStyle(body="SEAT", paint=BodyPaint.SOLID, color="#4c00ff"), BodyStyle(body="STEEL1080", paint=BodyPaint.SOLID, color="#c800ff"), BodyStyle(body="GONE")),
    )
    assert spec.unmatched(["AIR", "SEAT", "BEARING"]) == ["GONE", "STEEL1080"]
    assert DisplaySpec().unmatched([]) == []


# --- pyvista-gated: a section and its expansions --------------------------------


def _section_grid(columns: int = 4, rows: int = 2) -> pv.UnstructuredGrid:  # pragma: no cover - helper for the pyvista-gated tests below
    import pyvista as pv

    # A grid of quads over r in [0, 2], z in [0, 1]: the inner half is one body, the outer half the other; Elmer writes a flat mesh with a Z extent of a few 1e-18, never exactly zero.
    points = np.array([(column * 2.0 / columns, row / rows, 0.0) for row in range(rows + 1) for column in range(columns + 1)], dtype=np.float64)
    points[-1, 2] = 1e-18
    quads = [[(row * (columns + 1)) + column + offset for offset in (0, 1, columns + 2, columns + 1)] for row in range(rows) for column in range(columns)]
    cells = np.array([[4, *quad] for quad in quads]).ravel()
    grid = pv.UnstructuredGrid(cells, np.full(len(quads), pv.CellType.QUAD), points)
    grid.cell_data["GeometryIds"] = np.array([1 if quad[0] % (columns + 1) < columns // 2 else 2 for quad in quads], dtype=np.int32)
    grid.point_data["magnetic flux density"] = np.column_stack([np.zeros(len(points)), np.ones(len(points)), np.zeros(len(points))])

    return grid


def _section_scene(columns: int = 4, rows: int = 2) -> FieldScene:  # pragma: no cover - helper for the pyvista-gated tests below
    grid = _section_grid(columns, rows)
    bodies = Bodies([Body(name="INNER", mesh_id=1), Body(name="OUTER", mesh_id=2)])

    return FieldScene(grid=grid, blocks=split_by_body(grid, bodies), fields=list_fields(grid), model_dim=model_dim_of(grid), run_id="section", bodies=bodies)


def test_a_flat_grid_with_a_vanishing_z_extent_is_a_section_and_is_put_exactly_on_the_plane() -> None:
    pytest.importorskip("pyvista")

    scene = _section_scene()

    assert model_dim_of(_section_grid()) == 2
    assert scene.section_grid.bounds[5] == 0.0 and all(cast("pv.DataSet", scene.section_blocks[name]).bounds[5] == 0.0 for name in scene.section_blocks.keys())


def test_a_section_revolves_by_default_mirrors_on_request_and_keeps_its_solved_ranges() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    scene = _section_scene()
    flat = DisplaySpec(field="magnetic flux density", expansion=Expansion.SECTION)
    _ = scene.apply(flat)
    flat_range = scene.data_range

    assert scene.expandable and scene.model_dim == 2 and scene.display_dim == 2
    assert scene.expansion_of(DisplaySpec()) == (Expansion.REVOLVE, 360.0) and scene.dim_for(DisplaySpec()) == 3
    assert not scene.expand(flat), "the section is what the scene starts from"

    _ = scene.apply(DisplaySpec(field="magnetic flux density"))
    revolved = scene.bounds

    assert scene.display_dim == 3 and scene.expanded == (Expansion.REVOLVE, 360.0)
    assert all(isinstance(scene.blocks[name], pv.PolyData) for name in scene.blocks.keys()), "only the surfaces are revolved"
    assert revolved[0] == pytest.approx(-2.0) and revolved[1] == pytest.approx(2.0) and revolved[5] == pytest.approx(1.0), "the section turns about Z into the authored frame"
    assert scene.data_range == flat_range, "the scale belongs to the solved field, not the view"

    _ = scene.apply(DisplaySpec(field="magnetic flux density", expansion=Expansion.MIRROR))

    assert scene.display_dim == 2 and scene.bounds[0] == pytest.approx(-2.0) and scene.bounds[5] == pytest.approx(0.0)
    assert scene.grid.n_cells == 16, "the mirrored grid carries both halves"


def test_an_expanded_section_keeps_the_principal_values_unturned_and_turns_the_displacement() -> None:
    pytest.importorskip("pyvista")

    grid = _section_grid()
    count = grid.n_points
    grid.point_data["displacement"] = np.tile([1.0, 0.0, 2.0], (count, 1))
    grid.point_data["principal stress"] = np.tile([3.0, 1.0, 2.0], (count, 1))
    bodies = Bodies([Body(name="INNER", mesh_id=1), Body(name="OUTER", mesh_id=2)])
    scene = FieldScene(grid=grid, blocks=split_by_body(grid, bodies), fields=list_fields(grid), model_dim=model_dim_of(grid), run_id="section", bodies=bodies)

    _ = scene.apply(DisplaySpec(field="principal stress", expansion=Expansion.MIRROR))
    mirrored_points = scene.grid.point_data

    assert np.allclose(np.asarray(mirrored_points["principal stress"]), [3.0, 1.0, 2.0])
    assert np.allclose(np.asarray(mirrored_points["displacement"])[count:], [-1.0, 0.0, -2.0]), "the reflected half turns the vector"
    assert all(np.allclose(np.asarray(cast("pv.DataSet", scene.blocks[name]).point_data[SCALARS_ARRAY]), 3.0) for name in scene.blocks.keys())

    _ = scene.apply(DisplaySpec(field="principal stress", mode=DisplayMode.MIN_PRINCIPAL))
    revolved = [cast("pv.DataSet", scene.blocks[name]) for name in scene.blocks.keys()]

    assert all(np.allclose(np.asarray(block.point_data["principal stress"]), [3.0, 1.0, 2.0]) for block in revolved)
    assert all(np.allclose(np.asarray(block.point_data[SCALARS_ARRAY]), 1.0) for block in revolved)
    assert any(not np.allclose(np.asarray(block.point_data["displacement"]), [1.0, 0.0, 2.0]) for block in revolved)


def test_a_mirrored_section_keeps_the_field_lines_of_both_halves() -> None:
    pytest.importorskip("pyvista")

    scene = _section_scene(columns=16, rows=8)
    _ = scene.apply(DisplaySpec(field="magnetic flux density", expansion=Expansion.MIRROR))

    actors = scene.integrate_streamlines(FieldLineOptions(line_count=9))
    points = np.asarray(actors.streamlines.points)

    assert points[:, 0].min() < -1.0 and points[:, 0].max() > 1.0, "a flat mirrored grid sits on the box face, so nothing may clip its lines away"


def test_a_revolved_section_seeds_the_swept_solid_and_sweeps_its_field_lines() -> None:
    pytest.importorskip("pyvista")

    scene = _section_scene(columns=16, rows=8)
    _ = scene.apply(DisplaySpec(field="magnetic flux density", sweep=270.0))
    plane = FieldLineOptions(line_count=9)
    volume = FieldLineOptions(line_count=3, seed_placement=SeedPlacement.VOLUME)

    cut = scene.swept_seeds(plane, scene.bounds)
    actors = scene.integrate_streamlines(plane)
    filling = scene.swept_seeds(volume, scene.bounds)
    _ = scene.apply(DisplaySpec(field="magnetic flux density"))
    full = scene.swept_seeds(plane, scene.bounds)

    assert cut is not None and 0 < len(cut) < 81 and bool(np.all((cut[:, 0] <= 0.0) | (cut[:, 1] >= 0.0))), "a 270 degree sweep keeps the center plane seeds out of the cut-away quadrant"
    assert filling is not None and 0 < len(filling) < 27, "volume seeds fill the swept solid only"
    assert full is None, "a full sweep seeds its bounds like a solid run"
    assert actors.streamlines.n_points > 0 and len(actors.magnitudes) == 2, "the lines come from the section, one magnitude chunk per solved body"


def test_a_sweep_expanded_before_the_update_re_adds_the_body_actor() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    scene = _section_scene()
    plotter = pv.Plotter(off_screen=True)
    scene.render(plotter)
    full_y = plotter.bounds[2]
    quarter = DisplaySpec(field="magnetic flux density", sweep=90.0, streamlines=None)

    assert scene.expand(quarter), "the viewer expands off the loop before it updates"
    scene.update(plotter, quarter)

    assert full_y == pytest.approx(-2.0) and plotter.bounds[2] == pytest.approx(0.0), "the body actor shows the quarter, not the blocks it was added with"
    plotter.close()


def test_the_revolved_seeds_fill_the_integration_region_and_keep_the_first_cut_face() -> None:
    pytest.importorskip("pyvista")
    from femforge.run.fields import GEOMETRY_IDS_ARRAY
    from femforge.viz.field_lines import seed_points

    scene = _section_scene(columns=16, rows=8)
    _ = scene.apply(DisplaySpec(field="magnetic flux density", sweep=270.0))
    whole = FieldLineOptions(line_count=9)
    inner = whole.model_copy(update={"region_array": GEOMETRY_IDS_ARRAY, "region_id": 1, "clip_to_region": True})
    face = (0.0, 2.0, -1e-17, -1e-17, 0.0, 1.0)

    on_face = scene.swept_seeds(whole, face)

    assert scene.region_bounds(whole)[1] == pytest.approx(2.0) and scene.region_bounds(inner)[1] == pytest.approx(1.0), "a region seeds its own revolved body"
    assert on_face is not None and len(on_face) == len(seed_points(face, whole)), "seeds a rounding error below the first cut face stay"


def test_a_changed_expansion_draws_the_scene_anew_in_the_plotter() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    scene = _section_scene()
    plotter = pv.Plotter(off_screen=True)
    scene.render(plotter)
    revolved_bounds = plotter.bounds

    scene.update(plotter, DisplaySpec(field="magnetic flux density", expansion=Expansion.SECTION, streamlines=None))

    assert revolved_bounds[0] == pytest.approx(-2.0) and plotter.bounds[0] == pytest.approx(0.0)
    assert scene.display_dim == 2 and str(SceneActor.BODIES) in plotter.renderer.actors
    plotter.close()


def test_a_solid_run_is_shown_as_is_whatever_the_spec_asks() -> None:
    pytest.importorskip("pyvista")

    scene = _scene()

    assert not scene.expandable and scene.display_dim == 3
    assert scene.expansion_of(DisplaySpec(expansion=Expansion.MIRROR)) == (Expansion.SECTION, 360.0) and scene.dim_for(DisplaySpec()) == 3
    assert not scene.expand(DisplaySpec())


def test_a_planar_section_is_shown_flat_and_never_expands() -> None:
    pytest.importorskip("pyvista")
    from femforge.frames import Cartesian2D
    from femforge.physical.units import U

    grid = _section_grid()
    bodies = Bodies([Body(name="INNER", mesh_id=1), Body(name="OUTER", mesh_id=2)])
    frame = Cartesian2D(thickness=10 * U.mm)
    scene = FieldScene(grid=grid, blocks=split_by_body(grid, bodies), fields=list_fields(grid), model_dim=2, run_id="planar", bodies=bodies, frame=frame)

    assert not scene.expandable and scene.basis is None
    assert scene.display_dim == 2 and scene.dim_for(DisplaySpec()) == 2
    assert scene.expansion_of(DisplaySpec()) == (Expansion.SECTION, 360.0)
    assert not scene.expand(DisplaySpec())


def test_the_basis_origin_comes_from_millimetres_to_metres() -> None:
    from femforge.frames import Axis, Axisymmetric
    from femforge.common.vector import Direction, Vec3

    basis = scaled_basis(Axisymmetric(axis=Axis(origin=Vec3(0, 0, 10), direction=Direction(0, 0, 1))).plane)

    assert basis.origin == pytest.approx((0.0, 0.0, 0.01)) and basis.axis == (0.0, 0.0, 1.0)


def test_a_bundle_without_a_manifest_takes_its_frame_from_the_grid(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")

    grid = _section_grid()
    root = tmp_path / "flat"
    root.mkdir()
    grid.save(str(root / "case_t0001.vtu"))  # pyright: ignore[reportUnknownMemberType]  # pyvista's save signature is partially untyped
    _ = (root / BundleFile.ENTITIES).write_text(EntitiesFile(bodies={1: BodyRecord(name="INNER"), 2: BodyRecord(name="OUTER")}).model_dump_json(), encoding="utf-8")

    scene = FieldScene.from_run(Run(root))

    assert run_manifest(Run(root)) is None
    assert scene.expandable and scene.frame.label == "axisymmetric about Z"


def test_the_legend_places_the_bars_and_a_second_one_beside_the_first() -> None:
    assert bar_placement(Legend.VERTICAL).vertical and not bar_placement(Legend.HORIZONTAL).vertical
    assert bar_placement(Legend.HIDDEN) == bar_placement(Legend.VERTICAL), "a hidden legend never reaches a placement"
    assert bar_placement(Legend.VERTICAL, second=True).x < bar_placement(Legend.VERTICAL).x
    assert bar_placement(Legend.HORIZONTAL, second=True).y > bar_placement(Legend.HORIZONTAL).y
