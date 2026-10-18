"""Axisymmetric expansion: the section basis, the mirrored plane and the revolved surface."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from femforge.frames import Axis, Axisymmetric
from femforge.common.vector import Direction, Vec3
from femforge.viz.symmetry import Basis, Block, Expansion, Polygons, Surface, VtkCell, mirrored, swept


if TYPE_CHECKING:
    import pyvista as pv


type FloatArray = np.typing.NDArray[np.float64]
type IntArray = np.typing.NDArray[np.int64]

SQUARE = np.array([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 1.0, 0.0], [1.0, 1.0, 0.0]])
"""A unit square off the axis: r in [1, 2], z in [0, 1]."""


def _polygons(*blocks: list[list[int]]) -> Polygons:
    """Polygons numbering the cells in order across the given blocks."""
    built: list[Block] = []
    start = 0
    for rows in blocks:
        built.append(Block(np.array(rows, dtype=np.int64), np.arange(start, start + len(rows), dtype=np.int64)))
        start += len(rows)

    return Polygons(tuple(built))


def _cells(surface: Surface) -> list[list[int]]:
    """The point ids of every cell in a surface's flat VTK array."""
    flat = cast("list[int]", surface.faces.tolist())
    cells: list[list[int]] = []
    position = 0
    while position < len(flat):
        size = flat[position]
        cells.append(flat[position + 1 : position + 1 + size])
        position += size + 1

    return cells


def _area_vectors(surface: Surface) -> FloatArray:
    """Each cell's area vector, its fan triangles' cross products halved and summed."""
    vectors: list[FloatArray] = []
    for cell in _cells(surface):
        corners: FloatArray = np.take(surface.points, cell, axis=0)
        fan: FloatArray = np.cross(corners[1:-1] - corners[:1], corners[2:] - corners[:1])
        vectors.append(np.sum(fan, axis=0) / 2.0)

    return np.array(vectors)


def _volume(surface: Surface) -> float:
    """The signed volume a closed surface encloses, positive when its cells face outward."""
    total = 0.0
    for cell in _cells(surface):
        corners: FloatArray = np.take(surface.points, cell, axis=0)
        total += float(np.sum(corners[:1] * np.cross(corners[1:-1], corners[2:]))) / 6.0

    return total


# --- Expansion / Basis ---------------------------------------------------------


def test_only_the_revolve_is_three_dimensional() -> None:
    assert [expansion.dim for expansion in Expansion] == [2, 2, 3]


def test_the_default_frame_maps_radial_to_x_and_the_axis_to_z() -> None:
    basis = Basis.of(Axisymmetric().plane)

    assert basis == Basis.DEFAULT
    assert basis.origin == (0.0, 0.0, 0.0)
    assert basis.radial == (1.0, 0.0, 0.0)
    assert basis.axis == (0.0, 0.0, 1.0)
    assert basis.normal == (0.0, 1.0, 0.0)


def test_a_y_axis_frame_is_the_solve_frame() -> None:
    basis = Basis.of(Axisymmetric(axis=Axis.Y).plane)

    assert basis == Basis.SOLVE
    assert basis.normal == (0.0, 0.0, -1.0)


def test_an_explicit_radial_and_an_offset_axis_set_the_basis() -> None:
    axis = Axis(origin=Vec3(1, 2, 3), direction=Direction(0, 0, 1))
    basis = Basis.of(Axisymmetric(axis=axis, radial=Direction(0, 1, 1)).plane)

    assert basis.origin == (1.0, 2.0, 3.0)
    assert basis.radial == pytest.approx((0.0, 1.0, 0.0))
    assert basis.normal == pytest.approx((-1.0, 0.0, 0.0))


def test_place_and_fold_round_trip() -> None:
    rng = np.random.default_rng(0)
    section = np.column_stack([rng.uniform(0.0, 3.0, 50), rng.uniform(-2.0, 2.0, 50), np.zeros(50)])
    theta = rng.uniform(-math.pi + 1e-6, math.pi, 50)
    basis = Basis.of(Axisymmetric(axis=Axis(origin=Vec3(1, -1, 2), direction=Direction(1, 1, 0))).plane)

    folded, angles = basis.fold(basis.place(section, theta))

    assert np.allclose(folded, section)
    assert np.allclose(angles, theta)


def test_place_ignores_the_section_z_and_fold_keeps_the_angle_in_the_half_open_range() -> None:
    placed = Basis.DEFAULT.place(np.array([[1.0, 2.0, 5.0]]), math.pi / 2)
    _, theta = Basis.DEFAULT.fold(np.array([[-1.0, -0.0, 0.0]]))

    assert np.allclose(placed, [[0.0, 1.0, 2.0]])
    assert theta.tolist() == [math.pi]


def test_rotate_turns_solve_frame_vectors_with_the_angle() -> None:
    quarter = math.pi / 2
    radial = Basis.DEFAULT.rotate(np.array([[1.0, 0.0, 0.0]]), quarter)
    axial = Basis.DEFAULT.rotate(np.array([[0.0, 1.0, 0.0]]), quarter)
    out_of_plane = Basis.DEFAULT.rotate(np.array([[0.0, 0.0, 1.0]]), quarter)

    assert np.allclose(radial, [Basis.DEFAULT.normal])
    assert np.allclose(axial, [[0.0, 0.0, 1.0]])
    assert np.allclose(out_of_plane, [[1.0, 0.0, 0.0]]), "solve Z is -e_phi, and e_phi(90 deg) = -X"


def test_carry_rotates_float_vectors_only() -> None:
    vectors = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
    ids = np.array([[1, 0, 0]], dtype=np.int64)
    scalars = np.array([4.0])

    rotated = Basis.DEFAULT.carry(vectors, math.pi)

    assert rotated.dtype == np.float32
    assert np.allclose(rotated, [[-1.0, 0.0, 0.0]], atol=1e-7)
    assert Basis.DEFAULT.carry(ids, math.pi) is ids
    assert Basis.DEFAULT.carry(scalars, math.pi) is scalars


# --- Polygons --------------------------------------------------------------------


def test_polygons_read_triangles_quads_and_pixels_and_drop_lines() -> None:
    celltypes = np.array([5, 3, 8, 5, 9], dtype=np.uint8)
    cells: dict[int, IntArray] = {
        3: np.array([[0, 1]]),
        5: np.array([[0, 1, 2], [2, 3, 4]]),
        8: np.array([[0, 1, 3, 4]]),
        9: np.array([[1, 2, 3, 4]]),
    }

    polygons = Polygons.of(cells, celltypes)

    assert [block.cells.tolist() for block in polygons.blocks] == [[0, 3], [2], [4]]
    assert polygons.blocks[1].corners.tolist() == [[0, 1, 4, 3]], "a pixel walks its boundary as a quad"
    assert [VtkCell(kind).order for kind in (5, 9)] == [[0, 1, 2], [0, 1, 2, 3]]


def test_oriented_reverses_only_clockwise_cells() -> None:
    points = np.array([[0.0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])
    polygons = _polygons([[0, 1, 2], [0, 2, 1]], [[0, 3, 2, 1]])

    oriented = polygons.oriented(points)

    assert oriented.blocks[0].corners.tolist() == [[0, 1, 2], [0, 1, 2]]
    assert oriented.blocks[1].corners.tolist() == [[0, 1, 2, 3]]


def test_boundary_keeps_edges_used_once_in_cell_order() -> None:
    polygons = _polygons([[0, 1, 2], [0, 2, 3]])

    edges, owners = polygons.boundary()

    pairs = cast("list[list[int]]", edges.tolist())

    assert sorted(pairs) == [[0, 1], [1, 2], [2, 3], [3, 0]]
    assert cast("list[int]", owners.tolist())[pairs.index([2, 3])] == 1


# --- mirrored ----------------------------------------------------------------------


def test_mirrored_doubles_the_points_and_reverses_the_reflected_cells() -> None:
    points = np.array([[0.0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])
    surface = mirrored(points, _polygons([[0, 1, 2], [0, 2, 3]]))

    assert np.allclose(surface.points[4:], points * [-1, 1, 1])
    assert _cells(surface) == [[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6]]
    assert surface.cell_source.tolist() == [0, 1, 0, 1]
    assert np.allclose(surface.point_values(np.tile([1.0, 2.0, 3.0], (4, 1)))[4:], [[-1.0, 2.0, -3.0]] * 4)


def test_mirrored_and_swept_of_no_cells_are_empty() -> None:
    nothing = Polygons(())

    assert mirrored(SQUARE, nothing).points.shape == (0, 3)
    assert swept(SQUARE, nothing, Basis.DEFAULT).faces.size == 0


# --- swept -------------------------------------------------------------------------


def test_a_full_sweep_of_a_square_is_a_closed_outward_torus() -> None:
    surface = swept(SQUARE, _polygons([[0, 1, 2, 3]]), Basis.DEFAULT, segments=72)

    assert len(surface.points) == 4 * 72
    assert surface.corners.tolist() == [4] * (4 * 72)
    assert float(np.sum(np.linalg.norm(_area_vectors(surface), axis=1))) == pytest.approx(12.0 * math.pi, rel=0.01), "2pi(1 + 2) sides + 2 * pi(4 - 1) annuli"
    assert _volume(surface) == pytest.approx(2.0 * math.pi * 1.5, rel=0.01)


def test_a_clockwise_square_still_faces_outward() -> None:
    surface = swept(SQUARE, _polygons([[0, 3, 2, 1]]), Basis.DEFAULT, segments=36)

    assert _volume(surface) > 0.0


def test_a_triangle_on_the_axis_sweeps_into_a_cone_without_degenerate_cells() -> None:
    points = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]])
    surface = swept(points, _polygons([[0, 1, 2]]), Basis.DEFAULT, segments=36)

    areas = np.linalg.norm(_area_vectors(surface), axis=1)

    assert len(surface.points) == 36 + 2, "one ring for the off-axis corner, the two axis corners once"
    assert surface.corners.tolist() == [3] * 72
    assert float(areas.min()) > 0.0
    assert _volume(surface) == pytest.approx(math.pi / 3.0, rel=0.01)


def test_a_partial_sweep_has_caps_and_one_more_ring() -> None:
    surface = swept(SQUARE, _polygons([[0, 1, 2, 3]]), Basis.DEFAULT, segments=72, sweep=90.0)

    assert len(surface.points) == 4 * 19 + 2 * 4
    assert surface.corners.size == 4 * 18 + 2
    assert surface.cell_theta[-2:].tolist() == [0.0, math.pi / 2]
    assert _volume(surface) == pytest.approx(2.0 * math.pi * 1.5 / 4.0, rel=0.01)


def test_a_tiny_partial_sweep_keeps_one_segment() -> None:
    surface = swept(SQUARE, _polygons([[0, 1, 2, 3]]), Basis.DEFAULT, segments=4, sweep=1.0)

    assert len(surface.points) == 4 * 2 + 2 * 4


def test_swept_cells_carry_their_owner_and_rotated_vectors() -> None:
    points = np.array([[1.0, 0, 0], [2, 0, 0], [2, 1, 0], [1, 1, 0], [3, 0, 0], [3, 1, 0]])
    surface = swept(points, _polygons([[0, 1, 2, 3], [1, 4, 5, 2]]), Basis.DEFAULT, segments=4)
    radial = np.tile([1.0, 0.0, 0.0], (len(points), 1))
    owners = np.array([10, 20])

    point_vectors = surface.point_values(radial)
    quarter = np.isclose(surface.point_theta, math.pi / 2)

    assert np.unique(surface.cell_values(owners)).tolist() == [10, 20]
    assert np.allclose(point_vectors[quarter], [0.0, 1.0, 0.0])
    assert np.unique(np.round(surface.cell_theta, 9)).tolist() == pytest.approx([math.pi / 4 + k * math.pi / 2 for k in range(4)]), "mid-segment angles"


@pytest.mark.parametrize(("segments", "sweep"), [(2, 360.0), (72, 0.0), (72, 400.0)])
def test_swept_rejects_too_few_segments_and_a_bad_sweep(segments: int, sweep: float) -> None:
    with pytest.raises(ValueError, match="segments|sweep"):
        _ = swept(SQUARE, _polygons([[0, 1, 2, 3]]), Basis.DEFAULT, segments=segments, sweep=sweep)


# --- mirror / revolve (gated: needs pyvista) -------------------------------------------


def _strip() -> pv.UnstructuredGrid:  # pragma: no cover - helper for the pyvista-gated tests below
    """Two triangles and a boundary line, with a radial vector, an id per cell and a scalar per point."""
    import pyvista as pv

    points = np.array([[0.0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])
    grid = pv.UnstructuredGrid([3, 0, 1, 2, 3, 0, 2, 3, 2, 0, 1], [pv.CellType.TRIANGLE, pv.CellType.TRIANGLE, pv.CellType.LINE], points)
    grid.point_data["B"] = np.tile([1.0, 2.0, 3.0], (4, 1))
    grid.point_data["potential"] = np.arange(4, dtype=np.float64)
    grid.cell_data["GeometryIds"] = np.array([1, 2, 3], dtype=np.int32)
    grid.cell_data["J"] = np.tile([0.0, 0.0, 5.0], (3, 1))

    return grid


def test_mirror_reflects_the_cells_and_flips_the_vectors() -> None:
    pytest.importorskip("pyvista")
    from femforge.viz.symmetry import mirror

    grid = _strip()

    mirrored_grid = mirror(grid)
    points = np.asarray(mirrored_grid.points)

    assert mirrored_grid.n_points == 8
    assert mirrored_grid.celltypes.tolist() == [VtkCell.TRIANGLE] * 4, "the line cell is dropped"  # pyright: ignore[reportUnknownMemberType]  # pyvista's celltypes is untyped
    assert np.allclose(points[4:], points[:4] * [-1, 1, 1])
    assert np.allclose(np.asarray(mirrored_grid.point_data["B"])[4:], [[-1.0, 2.0, -3.0]] * 4)
    assert np.asarray(mirrored_grid.point_data["potential"]).tolist() == [0.0, 1.0, 2.0, 3.0] * 2
    assert np.asarray(mirrored_grid.cell_data["GeometryIds"]).tolist() == [1, 2, 1, 2]
    assert np.allclose(np.asarray(mirrored_grid.cell_data["J"])[2:], [[0.0, 0.0, -5.0]] * 2)
    assert mirrored_grid.get_cell(2).point_ids == [4, 6, 5]


def test_revolve_keeps_the_body_ids_and_turns_the_vectors_with_each_ring() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.viz.symmetry import revolve

    grid = pv.UnstructuredGrid([4, 0, 1, 2, 3], [pv.CellType.QUAD], SQUARE)
    grid.point_data["B"] = np.tile([1.0, 0.0, 0.0], (4, 1))
    grid.cell_data["GeometryIds"] = np.array([7], dtype=np.int32)

    full = revolve(grid, Basis.DEFAULT, segments=72)
    quarter = revolve(grid, Basis.DEFAULT, segments=72, sweep=90.0)
    points = np.asarray(full.points)
    on_y = np.isclose(points[:, 0], 0.0) & (points[:, 1] > 0.0)

    assert full.n_cells == 4 * 72 and full.n_points == 4 * 72
    assert full.is_manifold
    assert full.area == pytest.approx(12.0 * math.pi, rel=0.01)
    assert np.unique(np.asarray(full.cell_data["GeometryIds"])).tolist() == [7]
    assert np.allclose(np.asarray(full.point_data["B"])[on_y], [0.0, 1.0, 0.0])
    assert quarter.n_cells == 4 * 18 + 2
    assert quarter.volume == pytest.approx(2.0 * math.pi * 1.5 / 4.0, rel=0.01)


def test_mirror_and_revolve_carry_still_arrays_unturned() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.viz.symmetry import mirror, revolve

    grid = pv.UnstructuredGrid([4, 0, 1, 2, 3], [pv.CellType.QUAD], SQUARE)
    grid.point_data["B"] = np.tile([1.0, 0.0, 3.0], (4, 1))
    grid.point_data["principal"] = np.tile([1.0, 0.0, 3.0], (4, 1))
    grid.cell_data["principal e"] = np.array([[1.0, 0.0, 3.0]])
    still = frozenset({"principal", "principal e"})

    mirrored_grid = mirror(grid, still=still)
    revolved = revolve(grid, Basis.DEFAULT, segments=8, still=still)

    for shown in (mirrored_grid, revolved):
        assert np.allclose(np.asarray(shown.point_data["principal"]), [1.0, 0.0, 3.0])
        assert np.allclose(np.asarray(shown.cell_data["principal e"]), [1.0, 0.0, 3.0])
        assert not np.allclose(np.asarray(shown.point_data["B"]), [1.0, 0.0, 3.0]), "a vector still turns"


def test_surface_values_stay_unturned_when_asked() -> None:
    surface = mirrored(SQUARE, _polygons([[0, 1, 2, 3]]))
    vectors = np.tile([1.0, 2.0, 3.0], (4, 1))

    assert np.allclose(surface.point_values(vectors, turns=False), [1.0, 2.0, 3.0])
    assert np.allclose(surface.cell_values(np.array([[1.0, 2.0, 3.0]]), turns=False), [1.0, 2.0, 3.0])
    assert np.allclose(surface.point_values(vectors)[4:], [-1.0, 2.0, -3.0])


def test_revolve_of_a_section_without_2d_cells_is_empty() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.viz.symmetry import revolve

    lines = pv.UnstructuredGrid([2, 0, 1], [pv.CellType.LINE], SQUARE[:2])

    assert revolve(lines, Basis.DEFAULT).n_points == 0


def test_revolve_reads_image_data_pixels_as_quads() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.viz.symmetry import revolve

    image = pv.ImageData(dimensions=(3, 3, 1), spacing=(0.5, 0.5, 1.0), origin=(1.0, 0.0, 0.0))

    surface = revolve(image, Basis.DEFAULT, segments=72)

    assert surface.area == pytest.approx(12.0 * math.pi, rel=0.01)
