"""The Elmer mesh files read back as per-group linear cells."""

from pathlib import Path
from typing import cast

import numpy as np
import pytest

from femforge.run.entities import Body, Boundary, BoundaryKind
from femforge.run.mesh_groups import MESH_IDS_ARRAY, ElmerFamily, MeshGroups, read_elmer_cells, read_elmer_nodes


NODES = """\
1 -1 0 0 0
2 -1 1 0 0
3 -1 0 1 0
4 -1 0.5 0 0
5 -1 0.5 0.5 0
6 -1 0 0.5 0
7 -1 1 1 0
"""

ELEMENTS = """\
1 1 306 1 2 3 4 5 6
2 2 303 2 7 3
"""

BOUNDARY = """\
1 3 1 0 203 1 2 4
2 3 1 0 203 2 7 5
3 4 2 0 202 7 3
"""


def _bundle(path: Path) -> Path:
    for name, text in (("mesh.nodes", NODES), ("mesh.elements", ELEMENTS), ("mesh.boundary", BOUNDARY)):
        _ = (path / name).write_bytes(text.encode())

    return path


def test_the_cells_keep_their_corners_only(tmp_path: Path) -> None:
    blocks = read_elmer_cells(_bundle(tmp_path) / "mesh.elements", tag_column=1, type_column=2)

    assert [block.family for block in blocks] == [ElmerFamily.TRIANGLE, ElmerFamily.TRIANGLE]
    assert [block.tags.tolist() for block in blocks] == [[2], [1]]
    assert [block.corners.tolist() for block in blocks] == [[[2, 7, 3]], [[1, 2, 3]]]


def test_the_boundary_cells_group_by_type_code(tmp_path: Path) -> None:
    blocks = read_elmer_cells(_bundle(tmp_path) / "mesh.boundary", tag_column=1, type_column=4)

    assert [(block.family, block.tags.tolist(), block.corners.tolist()) for block in blocks] == [
        (ElmerFamily.LINE, [4], [[7, 3]]),
        (ElmerFamily.LINE, [3, 3], [[1, 2], [2, 7]]),
    ]


def test_the_nodes_read_with_their_ids(tmp_path: Path) -> None:
    ids, points = read_elmer_nodes(_bundle(tmp_path) / "mesh.nodes")

    assert ids.tolist() == [1, 2, 3, 4, 5, 6, 7]
    assert cast("list[list[float]]", points.tolist())[6] == [1.0, 1.0, 0.0]


@pytest.mark.parametrize("family", list(ElmerFamily))
def test_every_family_has_a_linear_vtk_cell(family: ElmerFamily) -> None:
    assert 1 <= family.corners <= 8
    assert family.vtk_cell in {1, 3, 5, 9, 10, 12, 13, 14}


def test_the_groups_read_as_pyvista_grids(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")
    groups = MeshGroups(_bundle(tmp_path))
    edge = Boundary(name="AIR__EXT", mesh_id=3, bodies=("AIR",), kind=BoundaryKind.EXTERNAL)

    assert groups.bodies.n_cells == 2 and groups.boundaries.n_cells == 3
    assert np.array_equal(np.sort(np.asarray(groups.bodies.cell_data[MESH_IDS_ARRAY])), [1, 2])
    assert groups.boundary(edge).n_cells == 2
    assert groups.body(Body(name="AIR", mesh_id=1)).n_cells == 1
    assert groups.body(Body(name="AIR", mesh_id=1)).bounds[:2] == (0.0, 1.0)
