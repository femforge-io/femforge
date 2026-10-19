"""The cells of each body and boundary group, read from the Elmer mesh files of a run as linear pyvista grids."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np
from numpy.typing import NDArray

from femforge.common.files import BundleFile


if TYPE_CHECKING:
    import pandas as pd
    import pyvista as pv

    from femforge.run.entities import Body, Boundary


__all__ = ["MESH_IDS_ARRAY", "ElmerFamily", "ElmerBlock", "read_elmer_cells", "read_elmer_nodes", "MeshGroups"]

MESH_IDS_ARRAY = "MeshIds"
"""The cell array that carries the ``mesh_id`` of the group each cell belongs to."""

MAX_ELEMENT_NODES = 27
"""The most nodes an Elmer element lists, a 27-node hexahedron."""

type IntArray = NDArray[np.int64]


class ElmerFamily(IntEnum):
    """An Elmer element family, the hundreds digit of its type code."""

    VERTEX = 1
    LINE = 2
    TRIANGLE = 3
    QUADRILATERAL = 4
    TETRAHEDRON = 5
    PYRAMID = 6
    WEDGE = 7
    HEXAHEDRON = 8

    @property
    def corners(self) -> int:
        """The corner nodes of the family, the nodes a linear cell keeps."""
        match self:
            case ElmerFamily.VERTEX:
                return 1
            case ElmerFamily.LINE:
                return 2
            case ElmerFamily.TRIANGLE:
                return 3
            case ElmerFamily.QUADRILATERAL | ElmerFamily.TETRAHEDRON:
                return 4
            case ElmerFamily.PYRAMID:
                return 5
            case ElmerFamily.WEDGE:
                return 6
            case ElmerFamily.HEXAHEDRON:
                return 8

    @property
    def vtk_cell(self) -> int:
        """The VTK cell type of the linear cell of the family."""
        match self:
            case ElmerFamily.VERTEX:
                return 1
            case ElmerFamily.LINE:
                return 3
            case ElmerFamily.TRIANGLE:
                return 5
            case ElmerFamily.QUADRILATERAL:
                return 9
            case ElmerFamily.TETRAHEDRON:
                return 10
            case ElmerFamily.PYRAMID:
                return 14
            case ElmerFamily.WEDGE:
                return 13
            case ElmerFamily.HEXAHEDRON:
                return 12


@dataclass(frozen=True)
class ElmerBlock:
    """The elements of one family in an Elmer mesh file: their group ids and the node ids of their corners."""

    family: ElmerFamily
    """The element family."""

    tags: IntArray
    """The group id of each element, the ``mesh_id`` of its body or boundary."""

    corners: IntArray
    """The ``(m, corners)`` node ids of the corners of each element."""


def _table(path: Path, width: int) -> NDArray[np.float64]:
    """The whitespace-separated numeric rows of ``path``, ragged rows padded with NaN to ``width`` columns."""
    import pandas as pd

    frame: pd.DataFrame = pd.read_csv(
        path,
        sep=" ",
        header=None,
        names=list(range(width)),
        skipinitialspace=True,
    )

    return cast("NDArray[np.float64]", frame.to_numpy(dtype=np.float64))


def read_elmer_cells(path: str | Path, *, tag_column: int, type_column: int) -> list[ElmerBlock]:
    """The elements of ``mesh.elements`` or ``mesh.boundary`` by family, corners only.

    Args:
        path: The Elmer mesh file.
        tag_column: The 0-based column of the group id.
        type_column: The 0-based column of the element type code; the node ids follow it.
    """
    table: NDArray[np.float64] = _table(Path(path), type_column + 1 + MAX_ELEMENT_NODES)
    codes: IntArray = table[:, type_column].astype(np.int64)
    first: int = type_column + 1
    blocks: list[ElmerBlock] = []

    for code in cast("list[int]", np.unique(codes).tolist()):
        family: ElmerFamily = ElmerFamily(code // 100)
        rows: NDArray[np.float64] = table[codes == code]
        block: ElmerBlock = ElmerBlock(
            family=family,
            tags=rows[:, tag_column].astype(np.int64),
            corners=rows[:, first : first + family.corners].astype(np.int64),
        )
        blocks.append(block)

    return blocks


def read_elmer_nodes(path: str | Path) -> tuple[IntArray, NDArray[np.float64]]:
    """The node ids and the ``(n, 3)`` coordinates of ``mesh.nodes``, in mesh units."""
    table: NDArray[np.float64] = _table(Path(path), 5)

    return table[:, 0].astype(np.int64), table[:, 2:5]


def _cells_of(grid: pv.UnstructuredGrid, mesh_id: int) -> pv.UnstructuredGrid:  # pragma: no cover - needs pyvista (exercised by the gated mesh-group tests)
    """The cells of ``grid`` whose ``MeshIds`` is ``mesh_id``."""
    ids: IntArray = np.asarray(grid.cell_data[MESH_IDS_ARRAY], dtype=np.int64)

    picked = cast("NDArray[np.bool_]", ids == mesh_id)

    return cast("pv.UnstructuredGrid", grid.extract_cells(np.flatnonzero(picked)))  # pyright: ignore[reportUnknownMemberType]  # pyvista's extract_cells index parameter is partially untyped


class MeshGroups:
    """The body and boundary cells of a run, read lazily from its Elmer mesh files, each cell tagged with its group ``mesh_id``."""

    def __init__(self, sim_dir: str | Path) -> None:
        """Read the Elmer mesh in ``sim_dir`` on first use."""
        self.path: Path = Path(sim_dir)

    @cached_property
    def _nodes(self) -> tuple[IntArray, NDArray[np.float64]]:
        return read_elmer_nodes(self.path / BundleFile.MESH_NODES)

    def _grid(self, blocks: list[ElmerBlock]) -> pv.UnstructuredGrid:  # pragma: no cover - needs pyvista (exercised by the gated mesh-group tests)
        import pyvista as pv

        ids, points = self._nodes
        lookup: IntArray = np.zeros(int(ids.max()) + 1, dtype=np.int64)
        lookup[ids] = np.arange(len(ids))
        cells: list[IntArray] = [np.column_stack([np.full(len(block.tags), block.family.corners), lookup[block.corners]]).ravel() for block in blocks]
        types: list[NDArray[np.uint8]] = [np.full(len(block.tags), block.family.vtk_cell, dtype=np.uint8) for block in blocks]
        grid = pv.UnstructuredGrid(np.concatenate(cells), np.concatenate(types), points)
        grid.cell_data[MESH_IDS_ARRAY] = np.concatenate([block.tags for block in blocks])

        return grid

    @cached_property
    def bodies(self) -> pv.UnstructuredGrid:  # pragma: no cover - needs pyvista (exercised by the gated mesh-group tests)
        """Every body cell, with its body ``mesh_id`` in ``MeshIds``."""
        return self._grid(read_elmer_cells(self.path / BundleFile.MESH_ELEMENTS, tag_column=1, type_column=2))

    @cached_property
    def boundaries(self) -> pv.UnstructuredGrid:  # pragma: no cover - needs pyvista (exercised by the gated mesh-group tests)
        """Every boundary cell, with its boundary ``mesh_id`` in ``MeshIds``."""
        return self._grid(read_elmer_cells(self.path / BundleFile.MESH_BOUNDARY, tag_column=1, type_column=4))

    def body(self, body: Body) -> pv.UnstructuredGrid:  # pragma: no cover - needs pyvista (exercised by the gated mesh-group tests)
        """The cells of ``body``."""
        return _cells_of(self.bodies, body.mesh_id)

    def boundary(self, boundary: Boundary) -> pv.UnstructuredGrid:  # pragma: no cover - needs pyvista (exercised by the gated mesh-group tests)
        """The cells of ``boundary``."""
        return _cells_of(self.boundaries, boundary.mesh_id)
