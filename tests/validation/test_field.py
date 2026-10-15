"""The field samplers. Heavy-stack checks (magpylib / pyvista) are gated via `importorskip`; a non-gated import test covers the light top-level surface."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import numpy as np
import pytest

from femforge.common.vector import Vec3
from femforge.validation.field import gap_line, sample_magpylib_b, sample_vtu_b
from femforge.validation.sources import MagnetSource


def test_field_module_exposes_samplers() -> None:
    # Importing the module covers its light surface; the heavy bodies are lazy.
    assert callable(sample_magpylib_b)
    assert callable(sample_vtu_b)


# --- gap_line (pure) --------------------------------------------------------


def _src(center: Vec3, size: Vec3) -> MagnetSource:
    return MagnetSource(center=center, size=size, polarization=Vec3(0, 1.0, 0))


def test_gap_line_runs_through_the_centroid_spanning_z() -> None:
    sources = [
        _src(Vec3(0.0, 0.002, 0.0), Vec3(0.005, 0.005, 0.02)),
        _src(Vec3(0.010, -0.002, 0.0), Vec3(0.005, 0.005, 0.02)),
    ]
    pts = gap_line(sources, samples=5)
    assert len(pts) == 5
    # centroid x = 0.005, y = 0.0; z spans the union extent [-0.01 .. 0.01].
    assert all(p.x == pytest.approx(0.005) and p.y == pytest.approx(0.0) for p in pts)
    assert min(p.z for p in pts) == pytest.approx(-0.01)
    assert max(p.z for p in pts) == pytest.approx(0.01)


def test_gap_line_collapses_to_a_midpoint_for_one_sample() -> None:
    pts = gap_line([_src(Vec3(0, 0, 0), Vec3(0.005, 0.005, 0.02))], samples=1)
    assert len(pts) == 1
    assert (pts[0].x, pts[0].y, pts[0].z) == pytest.approx((0.0, 0.0, 0.0))


def test_gap_line_raises_for_empty_sources() -> None:
    with pytest.raises(ValueError, match="at least one source"):
        _ = gap_line([], samples=4)


# --- sample_magpylib_b (gated: needs magpylib) ------------------------------


def test_sample_magpylib_b_has_expected_sign_and_decay() -> None:
    pytest.importorskip("magpylib")
    src = MagnetSource(center=Vec3(0, 0, 0), size=Vec3(0.005, 0.005, 0.005), polarization=Vec3(0, 1.0, 0))
    points = np.array([[0, 0.01, 0], [0, 0.02, 0]], dtype=np.float64)
    rows = cast("list[list[float]]", sample_magpylib_b([src], points).tolist())
    assert len(rows) == 2
    # On-axis above a +y-polarized cuboid: B points +y, decays with distance, is axis-symmetric.
    assert rows[0][1] > rows[1][1] > 0
    assert rows[0][0] == pytest.approx(0.0, abs=1e-9)
    assert rows[0][2] == pytest.approx(0.0, abs=1e-9)


def test_sample_magpylib_b_superposes_sources() -> None:
    pytest.importorskip("magpylib")
    src = MagnetSource(center=Vec3(0, 0, 0), size=Vec3(0.005, 0.005, 0.005), polarization=Vec3(0, 1.0, 0))
    point = np.array([[0, 0.01, 0]], dtype=np.float64)
    one = cast("list[list[float]]", sample_magpylib_b([src], point).tolist())[0]
    two = cast("list[list[float]]", sample_magpylib_b([src, src], point).tolist())[0]
    assert two[1] == pytest.approx(2 * one[1])  # linear superposition of two coincident magnets


# --- sample_vtu_b (gated: needs pyvista) ------------------------------------


def test_sample_vtu_b_interpolates_a_uniform_field(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    grid = pv.ImageData(dimensions=(8, 8, 8), spacing=(1.0, 1.0, 1.0))
    grid.point_data["magnetic flux density"] = [(0.0, 0.5, 0.0)] * grid.n_points
    vtu = tmp_path / "uniform.vtu"
    grid.cast_to_unstructured_grid().save(str(vtu))  # pyright: ignore[reportUnknownMemberType]  # pyvista save() carries **kwargs: Unknown

    out = sample_vtu_b(vtu, [Vec3(3.0, 3.0, 3.0), Vec3(4.0, 4.0, 4.0)])
    assert len(out) == 2
    for v in out:
        assert v.y == pytest.approx(0.5)
        assert v.x == pytest.approx(0.0)
        assert v.z == pytest.approx(0.0)
