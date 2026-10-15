"""3D field-line viz: pure-logic selector/seed/options tests plus a pyvista-gated off-screen render of a synthetic field."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from femforge.viz.field_lines import (
    Colormap,
    FieldLineOptions,
    SeedPlacement,
    seed_points,
    select_flux_density_array,
    trimmed_lines,
)

# A representative non-cubic domain: distinct extents per axis so centering/half-span bugs surface.
BOUNDS = (0.0, 10.0, 0.0, 20.0, 0.0, 4.0)


# --- trimmed_lines ------------------------------------------------------------


def test_a_line_ends_at_its_first_sharp_reversal_and_a_straight_one_stays_whole() -> None:
    points = np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0], [2, 0, 0], [1.5, 0, 0], [2, 0, 0], [0, 1, 0], [0, 2, 0], [1, 3, 0]])
    lines = np.array([6, 0, 1, 2, 3, 4, 5, 3, 6, 7, 8])

    ids, flat = trimmed_lines(points, lines)

    assert ids.tolist() == [0, 1, 2, 3, 6, 7, 8], "the oscillation after the repeated point is cut, a gentle bend kept"
    assert flat.tolist() == [4, 0, 1, 2, 3, 3, 4, 5, 6]


def test_a_line_left_with_one_point_is_dropped() -> None:
    points = np.array([[0.0, 0, 0], [1, 0, 0], [0, 0, 0], [1, 0, 0]])

    ids, flat = trimmed_lines(points, np.array([4, 0, 1, 2, 3]))
    empty, nothing = trimmed_lines(points, np.array([1, 0]))

    assert ids.tolist() == [0, 1] and flat.tolist() == [2, 0, 1]
    assert empty.tolist() == [] and nothing.tolist() == []


# --- select_flux_density_array ---------------------------------------------


def test_select_prefers_nodal_over_elemental() -> None:
    chosen = select_flux_density_array(["magnetic flux density e", "magnetic flux density", "nodal force"])
    assert chosen == "magnetic flux density"


def test_select_falls_back_to_elemental() -> None:
    # Only the elemental form is present (the MagnetoDynamicsCalcFields default) -> pick it.
    assert select_flux_density_array(["magnetic flux density e", "GeometryIds"]) == "magnetic flux density e"


def test_select_is_case_insensitive_and_returns_original_casing() -> None:
    assert select_flux_density_array(["Magnetic Flux Density E"]) == "Magnetic Flux Density E"


def test_select_contains_fallback() -> None:
    # An unexpected suffix/prefix still matches via the substring fallback.
    assert select_flux_density_array(["xx magnetic flux density (re)"]) == "xx magnetic flux density (re)"


def test_select_raises_when_absent() -> None:
    with pytest.raises(KeyError, match="magnetic flux density"):
        select_flux_density_array(["nodal force", "jfix", "GeometryIds"])


# --- seed_points ------------------------------------------------------------


def test_gap_line_lays_points_along_x_through_the_center() -> None:
    pts = seed_points(BOUNDS, FieldLineOptions(seed_placement=SeedPlacement.GAP_LINE, line_count=4))
    assert len(pts) == 4
    # All on the center y/z line; x spans seed_fraction (0.6) of [0,10] centered on 5 -> [2, 8].
    assert all(y == 10.0 and z == 2.0 for _, y, z in pts)
    assert min(x for x, _, _ in pts) == pytest.approx(2.0)
    assert max(x for x, _, _ in pts) == pytest.approx(8.0)


def test_center_plane_is_a_square_grid_on_mid_z() -> None:
    pts = seed_points(BOUNDS, FieldLineOptions(seed_placement=SeedPlacement.CENTER_PLANE, line_count=3))
    assert len(pts) == 9  # line_count**2
    assert all(z == 2.0 for _, _, z in pts)  # all on the mid-Z plane


def test_volume_is_a_cubic_grid() -> None:
    pts = seed_points(BOUNDS, FieldLineOptions(seed_placement=SeedPlacement.VOLUME, line_count=2))
    assert len(pts) == 8  # line_count**3
    # z now varies (0.6 of [0,4] centered on 2 -> [0.8, 3.2]).
    assert {round(z, 1) for _, _, z in pts} == {0.8, 3.2}


def test_single_seed_collapses_to_the_center() -> None:
    # line_count == 1 exercises the _seed_axis count<=1 branch: one point at the box center.
    pts = seed_points(BOUNDS, FieldLineOptions(seed_placement=SeedPlacement.GAP_LINE, line_count=1))
    assert pts == [(5.0, 10.0, 2.0)]


@pytest.mark.parametrize(
    "placement",
    [SeedPlacement.GAP_LINE, SeedPlacement.CENTER_PLANE, SeedPlacement.VOLUME],
)
def test_random_seeding_stays_within_bounds(placement: SeedPlacement) -> None:
    # seed_grid=False scatters seeds with random.uniform over the full bounds; seed the RNG for determinism.
    import random

    random.seed(0)
    xmin, xmax, ymin, ymax, zmin, zmax = BOUNDS
    pts = seed_points(BOUNDS, FieldLineOptions(seed_placement=placement, seed_grid=False, line_count=8))
    assert len(pts) >= 1
    for x, y, z in pts:
        assert xmin <= x <= xmax
        assert ymin <= y <= ymax
        assert zmin <= z <= zmax


def test_seed_fraction_controls_the_span() -> None:
    full = seed_points(BOUNDS, FieldLineOptions(seed_placement=SeedPlacement.GAP_LINE, line_count=2, seed_fraction=1.0))
    assert min(x for x, _, _ in full) == pytest.approx(0.0)
    assert max(x for x, _, _ in full) == pytest.approx(10.0)


# --- FieldLineOptions -------------------------------------------------------


def test_options_defaults() -> None:
    opts = FieldLineOptions()
    assert opts.seed_placement is SeedPlacement.VOLUME
    assert opts.colormap is Colormap.VIRIDIS
    assert opts.line_count == 12
    assert opts.tube_radius is None
    assert opts.show_outline is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"line_count": 0},  # must be > 0
        {"seed_fraction": 0.0},  # must be > 0
        {"seed_fraction": 1.5},  # must be <= 1
        {"tube_radius": 0.0},  # must be > 0 when set
    ],
)
def test_options_reject_out_of_range(kwargs: dict[str, object]) -> None:
    # model_validate takes the raw mapping, so the intentionally-invalid values are caught at runtime
    # (as ValidationError) rather than by the static checker rejecting the keyword types.
    with pytest.raises(ValidationError):
        FieldLineOptions.model_validate(kwargs)


# --- render_field_lines (gated: needs pyvista) ------------------------------


def test_render_writes_a_png_off_screen(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv  # typed via pyvista's py.typed; imported after the gate so the file still collects without it

    from femforge.viz.field_lines import render_field_lines

    # A synthetic uniform +x B field over a small grid: streamlines integrate cleanly and the
    # read -> select -> streamline -> render path runs end to end without a solved case.vtu. The vector
    # array is a plain list of 3-tuples (pyvista converts on assignment), so no numpy in the test surface.
    grid = pv.ImageData(dimensions=(8, 8, 8), spacing=(1.0, 1.0, 1.0))
    grid.point_data["magnetic flux density"] = [(1.0, 0.0, 0.0)] * grid.n_points
    vtu = tmp_path / "synthetic.vtu"
    grid.cast_to_unstructured_grid().save(str(vtu))  # pyright: ignore[reportUnknownMemberType]  # pyvista's save() carries **kwargs: Unknown

    png = tmp_path / "field_lines.png"
    render_field_lines(vtu, FieldLineOptions(line_count=3), off_screen=True, screenshot=png)

    assert png.is_file() and png.stat().st_size > 0


def test_prepare_streamline_source_projects_the_elemental_b_to_points() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.viz.field_lines import prepare_streamline_source

    # The elemental "... e" form lives on cells, so the source has to project it before integration.
    grid = pv.ImageData(dimensions=(4, 4, 4), spacing=(1.0, 1.0, 1.0)).cast_to_unstructured_grid()
    grid.cell_data["magnetic flux density e"] = [(1.0, 0.0, 0.0)] * grid.n_cells
    grid.cell_data["GeometryIds"] = [1] * grid.n_cells

    source, b_name = prepare_streamline_source(grid, FieldLineOptions())

    assert b_name == "magnetic flux density e"
    assert b_name in source.point_data


def test_prepare_streamline_source_clips_to_one_region() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.viz.field_lines import prepare_streamline_source

    grid = pv.ImageData(dimensions=(4, 4, 4), spacing=(1.0, 1.0, 1.0)).cast_to_unstructured_grid()
    grid.point_data["magnetic flux density"] = [(1.0, 0.0, 0.0)] * grid.n_points
    ids = [1] * grid.n_cells
    ids[0] = 2
    grid.cell_data["GeometryIds"] = ids

    options = FieldLineOptions(region_array="GeometryIds", region_id=2, clip_to_region=True)
    source, _ = prepare_streamline_source(grid, options)

    assert source.n_cells == 1


def test_options_integrate_alike_when_they_differ_only_in_how_lines_are_drawn() -> None:
    from femforge.viz.field_lines import integrates_like

    base = FieldLineOptions(line_count=5)

    assert integrates_like(base, base.model_copy(update={"colormap": "lipari", "invert": True, "opacity": 0.3}))
    assert not integrates_like(base, base.model_copy(update={"line_count": 6}))
