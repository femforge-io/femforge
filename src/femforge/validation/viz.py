"""PyVista rendering of the analytical magpylib field, drawn like the solved Elmer result so both can share a `viz.Scene`."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, cast

from femforge.common.vector import Vec3
from femforge.validation.field import sample_magpylib_b
from femforge.validation.sources import MagnetSource
from femforge.viz.field_lines import FLUX_DENSITY, Bounds, FieldLineOptions, Point3, StreamlineActors, build_streamlines, draw_streamlines


if TYPE_CHECKING:
    import pyvista as pv

DEFAULT_PADDING = Vec3(0.05, 0.05, 0.01)
"""`[m]`: The padding around the magnets on each axis, so the field grid covers the surrounding air and not only the magnet volume."""


def bounds_of_sources(sources: list[MagnetSource], *, padding: Vec3) -> Bounds:
    """The axis-aligned bounding box (metres) enclosing every source's full extent, grown by `padding` on each side; raises `ValueError` for no sources."""
    if not sources:
        raise ValueError("bounds_of_sources needs at least one source")
    xmin: float = min(s.center.x - s.size.x / 2 for s in sources)
    xmax: float = max(s.center.x + s.size.x / 2 for s in sources)
    ymin: float = min(s.center.y - s.size.y / 2 for s in sources)
    ymax: float = max(s.center.y + s.size.y / 2 for s in sources)
    zmin: float = min(s.center.z - s.size.z / 2 for s in sources)
    zmax: float = max(s.center.z + s.size.z / 2 for s in sources)
    return (xmin - padding.x, xmax + padding.x, ymin - padding.y, ymax + padding.y, zmin - padding.z, zmax + padding.z)


class GridBoundsMode(Enum):
    """Whether a sampling grid stays inside its target bounds or covers them."""

    INNER = "inner"
    """The grid stays inside the target bounds: its point counts round down, so it never overflows them."""

    OUTER = "outer"
    """The grid covers the target bounds: its point counts round up, so it never falls short of them."""


def grid_spec(
    bounds: Bounds,
    samples: int,
    mode: GridBoundsMode = GridBoundsMode.OUTER,
) -> tuple[Point3, Point3, tuple[int, int, int]]:
    """The `(origin, spacing, dimensions)` of a `pv.ImageData` sampling `bounds` with near-cubic cells; raises `ValueError` for fewer than 8 samples or a zero-volume box.

    Args:
        bounds: The target box, as ``(xmin, xmax, ymin, ymax, zmin, zmax)``.
        samples: The approximate total point count.
        mode: Keep the sampled extent inside the bounds (`INNER`) or covering them (`OUTER`).
    """
    if samples < 8:
        raise ValueError("grid_spec needs at least 8 samples (2 per axis × 3 axes)")
    xmin, xmax, ymin, ymax, zmin, zmax = bounds
    ex: float = xmax - xmin
    ey: float = ymax - ymin
    ez: float = zmax - zmin
    if ex <= 0 or ey <= 0 or ez <= 0:
        raise ValueError("grid_spec requires a non-degenerate bounding box")

    spacing: float = math.pow(ex * ey * ez / samples, 1.0 / 3.0)

    quantize = math.floor if mode == GridBoundsMode.INNER else math.ceil

    nx: int = max(2, quantize(ex / spacing))
    ny: int = max(2, quantize(ey / spacing))
    nz: int = max(2, quantize(ez / spacing))

    return (xmin, ymin, zmin), (spacing, spacing, spacing), (nx, ny, nz)


def bounded_grid(bounds: Bounds, samples: int) -> pv.ImageData:  # pragma: no cover - needs pyvista (viz extra); proven by the gated integration test
    """A `pv.ImageData` grid spanning `bounds` with ~cubic cells and `samples` in total, ready to carry a sampled vector field as point data."""
    import pyvista as pv

    origin, spacing, dims = grid_spec(bounds, samples)
    return pv.ImageData(dimensions=dims, spacing=spacing, origin=origin)


def magpylib_field_grid(
    sources: list[MagnetSource], *, padding: Vec3 = DEFAULT_PADDING, samples: int = 32
) -> pv.ImageData:  # pragma: no cover - needs magpylib + pyvista (viz extra); proven by the gated integration test
    """Sample the analytical **B** of `sources` onto a regular grid over their padded bounding box, a `pv.ImageData` ready for the streamline path.

    Args:
        sources: The magnets.
        padding: The air added around the magnets on each side, in metres.
        samples: The approximate total point count.
    """
    import numpy as np
    import pyvista as pv

    bounds: Bounds = bounds_of_sources(sources, padding=padding)
    origin, spacing, dims = grid_spec(bounds, samples)
    grid: pv.ImageData = pv.ImageData(dimensions=dims, spacing=spacing, origin=origin)
    # pyvista's `.points` has an Unknown dtype; cast pins it so the magpylib sampler gets a typed (N, 3) array.
    points: np.typing.NDArray[np.float64] = cast("np.typing.NDArray[np.float64]", grid.points)
    grid.point_data[FLUX_DENSITY] = sample_magpylib_b(sources, points)
    return grid


@dataclass
class MagpylibField:
    """
    A `viz.scene.Renderable` that draws the analytical magpylib field (|B| streamlines + magnet boxes) for a set of `MagnetSource`s.

    Sampling and streamline integration run in `__post_init__` (whichever worker thread constructs it); `render` only draws precomputed actors, keeping the plotter thread free.
    """

    sources: list[MagnetSource]
    """The magnets whose analytical field is drawn."""

    options: FieldLineOptions | None = None
    """The streamline options; None uses the defaults."""

    padding: Vec3 = DEFAULT_PADDING
    """`[m]`: The padding around the magnets on each axis that the field grid covers."""

    samples: int = 32
    """The approximate total point count of the field grid."""

    show_magnets: bool = True
    """Whether to draw the magnet boxes alongside the streamlines."""

    _actors: StreamlineActors | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:  # pragma: no cover - needs magpylib + pyvista (viz extra); proven by the gated integration test
        grid = magpylib_field_grid(self.sources, padding=self.padding, samples=self.samples)
        self._actors = build_streamlines(grid, FLUX_DENSITY, self.options)

    def render(self, plotter: pv.Plotter) -> None:  # pragma: no cover - needs pyvista + a VTK render context
        """Draw the precomputed magpylib field lines (and, when `show_magnets`, the magnet bodies) into `plotter` (on the plotter thread)."""
        import pyvista as pv

        assert self._actors is not None  # set in __post_init__
        draw_streamlines(self._actors, self.options if self.options is not None else FieldLineOptions(), plotter)
        if self.show_magnets:
            for s in self.sources:
                cube: pv.PolyData = pv.Cube(  # pyright: ignore[reportUnknownMemberType]  # pyvista Cube carries **kwargs: Unknown
                    center=(s.center.x, s.center.y, s.center.z),
                    x_length=s.size.x,
                    y_length=s.size.y,
                    z_length=s.size.z,
                )
                _ = plotter.add_mesh(cube, color="silver", opacity=0.35, show_scalar_bar=False)  # pyright: ignore[reportUnknownMemberType]


def load_gmsh_mesh(
    msh_path: str | Path,
) -> pv.UnstructuredGrid:  # pragma: no cover - needs pyvista + meshio (viz extra); proven by the gated integration test
    """Load a Gmsh `.msh` (via meshio) into a `pv.UnstructuredGrid` with the region names attached as the `"Region_Name"` cell-data array."""
    import meshio
    import numpy as np
    import pyvista as pv

    msh_data = cast("meshio.Mesh", meshio.read(str(msh_path)))  # pyright: ignore[reportUnknownMemberType]  # meshio.read returns a Mesh, but meshio ships no stubs

    cells = cast("list[meshio.CellBlock]", msh_data.cells)
    total_cells = sum(len(cast("np.ndarray", cell_block.data)) for cell_block in cells)

    cell_names = np.empty(total_cells, dtype=object)

    # Map the per-block cell_sets masks back to global cell index positions.
    for set_name, cell_set_masks in msh_data.cell_sets.items():
        if set_name == "gmsh:bounding_entities":  # gmsh metadata set, not a region
            continue

        global_idx_offset = 0
        for block_idx, mask in enumerate(cast("list[np.ndarray | None]", cell_set_masks)):
            block_length = len(cast("np.ndarray", cells[block_idx].data))

            if mask is not None and len(mask) > 0:
                # Mask can be a boolean array or an array of local integer indices.
                if mask.dtype == bool:
                    local_indices = np.where(mask)[0]
                else:
                    local_indices = mask

                global_indices = local_indices + global_idx_offset
                cell_names[global_indices] = set_name

            global_idx_offset += block_length

    # Clear cell_sets before conversion — the PyVista reader crashes on it.
    msh_data.cell_sets = {}
    mesh = pv.from_meshio(msh_data)

    mesh.cell_data["Region_Name"] = cell_names

    return mesh
