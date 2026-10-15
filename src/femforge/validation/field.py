"""Sample the magnetic flux density **B** from magpylib or from a solved Elmer VTU at a common set of points."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from femforge.common.vector import Vec3
from femforge.validation.sources import MagnetSource
from femforge.viz.field_lines import select_flux_density_array


if TYPE_CHECKING:
    import numpy as np
    import pyvista as pv


def gap_line(sources: list[MagnetSource], samples: int) -> list[Vec3]:
    """
    A line of `samples` points along Z through the centroid of the magnet centres — for a dual-Halbach array it threads the air gap.

    Returns a single mid-point when `samples < 2`; raises `ValueError` for an empty list.
    """
    if not sources:
        raise ValueError("gap_line needs at least one source")

    n: int = len(sources)
    cx: float = sum(s.center.x for s in sources) / n
    cy: float = sum(s.center.y for s in sources) / n
    zmin: float = min(s.center.z - s.size.z / 2 for s in sources)
    zmax: float = max(s.center.z + s.size.z / 2 for s in sources)

    if samples < 2:
        return [Vec3(cx, cy, (zmin + zmax) / 2)]

    step: float = (zmax - zmin) / (samples - 1)
    return [Vec3(cx, cy, zmin + step * i) for i in range(samples)]


def sample_magpylib_b(
    sources: list[MagnetSource], points: np.typing.NDArray[np.float64]
) -> np.typing.NDArray[np.float64]:  # pragma: no cover - needs magpylib (viz extra); proven by the gated integration test
    """Evaluate the analytical **B** (tesla) of `sources` at each point: one SI magpylib `Cuboid` per source, superposed via `Collection.getB`."""
    import magpylib as magpy
    import numpy as np
    from magpylib.magnet import Cuboid

    cuboids: list[Cuboid] = [
        Cuboid(
            position=(s.center.x, s.center.y, s.center.z),
            dimension=(s.size.x, s.size.y, s.size.z),
            polarization=(s.polarization.x, s.polarization.y, s.polarization.z),
        )
        for s in sources
    ]
    # getB superposes every source's field at every observer point; errstate silences the rare on-edge singularity
    # (those points are dropped as NaN by the caller before comparison).
    with np.errstate(invalid="ignore", divide="ignore"):
        b = magpy.Collection(*cuboids).getB(points)
    # getB squeezes a single observer down to shape (3,); normalize to (N, 3) so callers always get rows.
    return cast("np.typing.NDArray[np.float64]", np.atleast_2d(b))


def sample_vtu_b(vtu_path: str | Path, points: list[Vec3]) -> list[Vec3]:  # pragma: no cover - needs pyvista (viz extra) + a solved case.vtu; proven by the gated integration test
    """Interpolate the solved Elmer **B** (tesla) at `points` from a `case.vtu` (pyvista `sample`; elemental arrays are projected to point data first)."""
    import numpy as np
    import pyvista as pv

    grid: pv.UnstructuredGrid = cast("pv.UnstructuredGrid", pv.read(str(vtu_path)))
    b_name: str = select_flux_density_array(list(grid.array_names))
    sampled: pv.DataSet = cast("pv.DataSet", grid.cell_data_to_point_data() if b_name in grid.cell_data else grid)

    point_array = np.array([[p.x, p.y, p.z] for p in points], dtype=float)
    probe: pv.PolyData = pv.PolyData(point_array)
    result: pv.PolyData = cast("pv.PolyData", probe.sample(sampled))

    b_values = np.atleast_2d(np.asarray(result.point_data[b_name], dtype=float))
    rows: list[list[float]] = cast("list[list[float]]", b_values.tolist())
    return [Vec3(row[0], row[1], row[2]) for row in rows]


def extract_vtu_b(
    vtu_path: str | Path,
) -> tuple[np.typing.NDArray[np.float64], np.typing.NDArray[np.float64]]:  # pragma: no cover - needs pyvista (viz extra) + a solved case.vtu; proven by the gated integration test
    """Extract the solved Elmer **B** (tesla) from a `case.vtu` as `(positions, B)` arrays of shape `(n_points, 3)`."""
    import numpy as np
    import pyvista as pv

    grid: pv.UnstructuredGrid = cast("pv.UnstructuredGrid", pv.read(str(vtu_path)))

    b_name: str = select_flux_density_array(list(grid.array_names))

    sampled: pv.DataSet = cast("pv.DataSet", grid.cell_data_to_point_data() if b_name in grid.cell_data else grid)

    positions = np.asarray(sampled.points, dtype=np.float64)
    b_field = np.asarray(sampled.point_data[b_name], dtype=np.float64)

    return positions, b_field
