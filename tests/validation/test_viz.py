"""The magpylib field visualization. Unit tests cover grid sizing (`bounds_of_sources`, `grid_spec`); a gated integration test constructs `MagpylibField` and renders off-screen to a PNG."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from femforge.common.vector import Vec3
from femforge.validation.sources import MagnetSource
from femforge.validation.viz import (
    DEFAULT_PADDING,
    MagpylibField,
    bounds_of_sources,
    grid_spec,
)
from femforge.viz.field_lines import FieldLineOptions


def _source(center: Vec3, size: Vec3) -> MagnetSource:
    return MagnetSource(center=center, size=size, polarization=Vec3(0, 1.0, 0))


# --- bounds_of_sources ------------------------------------------------------


def test_bounds_encloses_all_sources_plus_padding() -> None:
    sources = [
        _source(Vec3(0, 0, 0), Vec3(0.002, 0.004, 0.006)),
        _source(Vec3(0.010, 0, 0), Vec3(0.002, 0.002, 0.002)),
    ]
    xmin, xmax, ymin, ymax, zmin, zmax = bounds_of_sources(sources, padding=Vec3(0.1, 0.2, 0.3))
    # x: [-0.001 .. 0.011] -> padded by 0.1; y: [-0.002 .. 0.002] -> padded 0.2; z: [-0.003 .. 0.003] -> padded 0.3.
    assert xmin == pytest.approx(-0.001 - 0.1)
    assert xmax == pytest.approx(0.011 + 0.1)
    assert ymin == pytest.approx(-0.002 - 0.2)
    assert ymax == pytest.approx(0.002 + 0.2)
    assert zmin == pytest.approx(-0.003 - 0.3)
    assert zmax == pytest.approx(0.003 + 0.3)


def test_bounds_raises_for_empty_sources() -> None:
    with pytest.raises(ValueError, match="at least one source"):
        _ = bounds_of_sources([], padding=DEFAULT_PADDING)


# --- grid_spec --------------------------------------------------------------
# `samples` is the total target point count; grid_spec picks the largest cubic-cell spacing whose grid has at least that many points.


def test_grid_spec_targets_total_samples_with_cubic_cells() -> None:
    origin, spacing, dims = grid_spec((0.0, 1.0, 0.0, 1.0, 0.0, 1.0), samples=27)
    assert origin == (0.0, 0.0, 0.0)
    assert spacing[0] == pytest.approx(spacing[1]) == pytest.approx(spacing[2])  # cubic cells
    assert dims == (3, 3, 3)  # 27 points on a unit cube
    assert math.prod(dims) >= 27


def test_grid_spec_scales_dims_to_extents() -> None:
    # Extents 0.4 : 0.2 : 0.1 -> longest axis gets the most points, shortest the fewest (cubic cells).
    _origin, spacing, dims = grid_spec((0.0, 0.4, 0.0, 0.2, 0.0, 0.1), samples=216)
    assert spacing[0] == pytest.approx(spacing[1]) == pytest.approx(spacing[2])
    assert dims[0] > dims[1] > dims[2] >= 2
    assert math.prod(dims) >= 216


def test_grid_spec_floors_short_axes_to_two_points() -> None:
    # A nearly-flat axis still gets at least 2 points (valid ImageData needs >= 2 per axis).
    _origin, _spacing, dims = grid_spec((0.0, 1.0, 0.0, 1.0, 0.0, 0.0001), samples=8)
    assert dims[2] == 2


@pytest.mark.parametrize("samples", [1, 0, -3])
def test_grid_spec_rejects_too_few_samples(samples: int) -> None:
    with pytest.raises(ValueError, match="at least 8 samples"):
        _ = grid_spec((0.0, 1.0, 0.0, 1.0, 0.0, 1.0), samples=samples)


@pytest.mark.parametrize(
    "bounds",
    [
        (0.0, 0.0, 0.0, 1.0, 0.0, 1.0),  # zero x-extent
        (0.0, 1.0, 1.0, 1.0, 0.0, 1.0),  # zero y-extent
        (0.0, 1.0, 0.0, 1.0, 1.0, 1.0),  # zero z-extent
    ],
)
def test_grid_spec_rejects_degenerate_bounds(bounds: tuple[float, float, float, float, float, float]) -> None:
    with pytest.raises(ValueError, match="non-degenerate bounding box"):
        _ = grid_spec(bounds, samples=27)


# --- MagpylibField construction + render (gated: needs magpylib + pyvista) --


def test_magpylib_field_renders_to_png_off_screen(tmp_path: Path) -> None:
    pytest.importorskip("magpylib")
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.validation.viz import magpylib_field_grid

    sources = [_source(Vec3(0, 0, 0), Vec3(0.005, 0.005, 0.0254))]
    grid = magpylib_field_grid(sources, padding=Vec3(0.01, 0.01, 0.01), samples=200)
    assert grid.n_points > 0
    assert "magnetic flux density" in grid.point_data

    # Construction runs __post_init__ (samples + integrates streamlines); render only draws.
    renderable = MagpylibField(sources=sources, options=FieldLineOptions(line_count=3), samples=200)
    assert renderable.samples == 200 and renderable.show_magnets is True  # defaults survive the dataclass
    plotter = pv.Plotter(off_screen=True)
    renderable.render(plotter)
    png = tmp_path / "magpylib_field.png"
    plotter.show(screenshot=str(png))  # pyright: ignore[reportUnknownMemberType]  # pyvista show() carries **kwargs: Unknown
    assert png.is_file() and png.stat().st_size > 0
