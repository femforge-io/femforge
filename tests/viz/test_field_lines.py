"""3D field-line viz: pure-logic selector/seed/options tests plus a pyvista-gated off-screen render of a synthetic field."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest
from pydantic import ValidationError

from femforge.viz.field_lines import (
    Bounds,
    Colormap,
    FieldLineOptions,
    SeedPlacement,
    seed_points,
    select_flux_density_array,
    trimmed_lines,
)


if TYPE_CHECKING:
    import pyvista as pv

# A representative non-cubic domain: distinct extents per axis so centering/half-span bugs surface.
BOUNDS = (0.0, 10.0, 0.0, 20.0, 0.0, 4.0)


# --- trimmed_lines ------------------------------------------------------------


def test_a_line_ends_at_its_first_sharp_reversal_and_a_straight_one_stays_whole() -> None:
    points = np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0], [2, 0, 0], [1.5, 0, 0], [2, 0, 0], [0, 1, 0], [0, 2, 0], [1, 3, 0]])
    lines = np.array([6, 0, 1, 2, 3, 4, 5, 3, 6, 7, 8])

    ids, flat, cells = trimmed_lines(points, lines)

    assert ids.tolist() == [0, 1, 2, 3, 6, 7, 8], "the oscillation after the repeated point is cut, a gentle bend kept"
    assert flat.tolist() == [4, 0, 1, 2, 3, 3, 4, 5, 6]
    assert cells.tolist() == [0, 1]


def test_a_line_left_with_one_point_is_dropped() -> None:
    points = np.array([[0.0, 0, 0], [1, 0, 0], [0, 0, 0], [1, 0, 0]])

    ids, flat, cells = trimmed_lines(points, np.array([4, 0, 1, 2, 3]))
    empty, nothing, none = trimmed_lines(points, np.array([1, 0, 4, 0, 1, 2, 3]))

    assert ids.tolist() == [0, 1] and flat.tolist() == [2, 0, 1] and cells.tolist() == [0]
    assert empty.tolist() == [0, 1] and nothing.tolist() == [2, 0, 1] and none.tolist() == [1], "the dropped first line shifts no index"


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
    assert opts.seed_placement is SeedPlacement.CENTER_PLANE
    assert opts.colormap is Colormap.TURBO
    assert opts.line_count == 18
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


# --- build_swept_streamlines (gated: needs pyvista) ------------------------------------


def _uniform_axial_section() -> pv.UnstructuredGrid:  # pragma: no cover - helper for the pyvista-gated tests below
    """The r in [0, 1], z in [0, 1] section carrying a uniform axial B = (0, 1, 0)."""
    import pyvista as pv

    grid = pv.ImageData(dimensions=(11, 11, 1), spacing=(0.1, 0.1, 1.0)).cast_to_unstructured_grid()
    grid.point_data["B"] = np.tile([0.0, 1.0, 0.0], (grid.n_points, 1))

    return grid


def _line_owners(lines: pv.PolyData) -> np.typing.NDArray[np.int64]:  # pragma: no cover - helper for the pyvista-gated tests below
    """The polyline index of every point of a polyline set."""
    flat = cast("list[int]", np.asarray(lines.lines).tolist())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # pyvista's lines property is untyped
    owners = np.zeros(lines.n_points, dtype=np.int64)
    position = 0
    cell = 0
    while position < len(flat):
        size = flat[position]
        owners[flat[position + 1 : position + 1 + size]] = cell
        position += size + 1
        cell += 1

    return owners


REVOLVED: Bounds = (-1.0, 1.0, -1.0, 1.0, 0.0, 1.0)
"""The authored bounds of the uniform section revolved about Z."""


def test_gap_line_seeds_trace_straight_lines_along_the_authored_axis() -> None:
    pytest.importorskip("pyvista")
    from femforge.viz.field_lines import build_swept_streamlines
    from femforge.viz.symmetry import Basis

    options = FieldLineOptions(seed_placement=SeedPlacement.GAP_LINE, line_count=4)

    actors = build_swept_streamlines(_uniform_axial_section(), "B", options, Basis.DEFAULT, REVOLVED)
    lines = actors.streamlines
    points = np.asarray(lines.points)
    owners = _line_owners(lines)
    seeds = np.asarray(seed_points(REVOLVED, options))[np.asarray(lines.cell_data["SeedIds"])][owners]
    heights: np.typing.NDArray[np.float64] = points @ np.array([0.0, 0.0, 1.0])

    assert np.unique(np.asarray(lines.cell_data["SeedIds"])).tolist() == [0, 1, 2, 3]
    assert np.allclose(points[:, :2], seeds[:, :2]), "each line keeps its seed's authored x and y"
    assert 0.0 <= float(heights.min()) and float(heights.max()) <= 1.0 and float(np.ptp(heights)) > 0.8, "the lines run the section height"
    assert np.allclose(np.asarray(lines.point_data["B"]), [0.0, 0.0, 1.0])
    assert actors.outline is not None and actors.outline.bounds == pytest.approx(REVOLVED)
    assert np.allclose(actors.magnitudes[0], 1.0)


def test_center_plane_lines_stay_at_their_seed_angle_and_radius() -> None:
    pytest.importorskip("pyvista")
    from femforge.viz.field_lines import build_swept_streamlines
    from femforge.viz.symmetry import Basis

    options = FieldLineOptions(seed_placement=SeedPlacement.CENTER_PLANE, line_count=3, tube_radius=0.01, show_outline=False)
    plain = options.model_copy(update={"tube_radius": None})

    lines = build_swept_streamlines(_uniform_axial_section(), "B", plain, Basis.DEFAULT, REVOLVED).streamlines
    tubed = build_swept_streamlines(_uniform_axial_section(), "B", options, Basis.DEFAULT, REVOLVED)
    seed_section, seed_theta = Basis.DEFAULT.fold(np.asarray(seed_points(REVOLVED, plain)))
    folded, theta = Basis.DEFAULT.fold(np.asarray(lines.points))
    seed_of_point = np.asarray(lines.cell_data["SeedIds"])[_line_owners(lines)]
    radial = np.array([1.0, 0.0, 0.0])
    seed_radius = np.take(seed_section @ radial, seed_of_point)
    off_axis = np.flatnonzero(np.isin(np.sign(seed_radius), [1.0]))

    assert np.allclose(folded @ radial, seed_radius)
    assert off_axis.size and np.allclose(np.take(theta, off_axis), np.take(np.take(seed_theta, seed_of_point), off_axis))
    assert tubed.outline is None and tubed.streamlines.n_cells > lines.n_cells


def test_explicit_seeds_at_two_azimuths_keep_their_angles() -> None:
    pytest.importorskip("pyvista")
    from femforge.viz.field_lines import build_swept_streamlines
    from femforge.viz.symmetry import Basis

    basis = Basis.DEFAULT
    section_seeds = np.array([[0.5, 0.5, 0.0], [0.25, 0.4, 0.0]])
    angles = [0.3, 2.0]
    authored = np.concatenate([basis.place(section_seeds, angle) for angle in angles])
    expected_theta = np.repeat(angles, 2)

    lines = build_swept_streamlines(_uniform_axial_section(), "B", FieldLineOptions(), basis, REVOLVED, seeds=authored).streamlines
    seed_of_point = np.asarray(lines.cell_data["SeedIds"])[_line_owners(lines)]
    folded, theta = basis.fold(np.asarray(lines.points))

    assert np.unique(seed_of_point).tolist() == [0, 1, 2, 3]
    assert np.allclose(theta, expected_theta[seed_of_point])
    assert np.allclose(folded[:, 0], np.tile(section_seeds[:, 0], 2)[seed_of_point])


def test_seeds_outside_the_section_trace_nothing() -> None:
    pytest.importorskip("pyvista")
    from femforge.viz.field_lines import build_swept_streamlines
    from femforge.viz.symmetry import Basis

    outside = np.array([[5.0, 0.0, 0.5], [0.0, 0.0, 9.0]])

    actors = build_swept_streamlines(_uniform_axial_section(), "B", FieldLineOptions(tube_radius=0.01), Basis.DEFAULT, REVOLVED, seeds=outside)

    assert actors.streamlines.n_points == 0


def test_trim_reversals_carries_the_cell_data_of_the_kept_lines() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.viz.field_lines import trim_reversals

    points = np.array([[0.0, 0, 0], [1, 0, 0], [5, 5, 5], [0, 1, 0], [0, 2, 0]])
    lines = pv.PolyData(points, lines=np.array([1, 2, 2, 0, 1, 2, 3, 4]))
    lines.cell_data["SeedIds"] = np.array([7, 8, 9])

    trimmed = trim_reversals(lines)

    assert np.asarray(trimmed.cell_data["SeedIds"]).tolist() == [8, 9]
