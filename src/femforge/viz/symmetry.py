"""Expansion of an axisymmetric r–z section into the mirrored plane or the revolved solid of the frame it was authored in."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, ClassVar, Literal, cast

import numpy as np

from femforge.frames import Axisymmetric, SolvePlane


if TYPE_CHECKING:
    import pyvista as pv


type FloatArray = np.typing.NDArray[np.float64]
type IntArray = np.typing.NDArray[np.int64]
type BoolArray = np.typing.NDArray[np.bool_]
type AnyArray = np.typing.NDArray[np.generic]
type Triple = tuple[float, float, float]

AXIS_TOLERANCE = 1e-9
"""`[-]`: The radius, as a fraction of the section's largest extent, at or below which a point lies on the axis."""


class Expansion(StrEnum):
    """How an axisymmetric section is shown."""

    SECTION = "section"
    """The r–z half-plane as solved."""

    MIRROR = "mirror"
    """Both halves of the r–z plane, still 2D in the solve frame."""

    REVOLVE = "revolve"
    """The section swept around the axis into the authored 3D frame."""

    @property
    def dim(self) -> Literal[2, 3]:
        """The dimension of the shown geometry."""
        return 3 if self is Expansion.REVOLVE else 2


class VtkCell(IntEnum):
    """The VTK cell types a section's 2D cells arrive as."""

    TRIANGLE = 5
    """A three-corner cell."""

    PIXEL = 8
    """An axis-aligned four-corner cell in raster order, as ImageData casts to."""

    QUAD = 9
    """A four-corner cell walked around its boundary."""

    @property
    def order(self) -> list[int]:
        """The corner order that walks the cell's boundary."""
        match self:
            case VtkCell.TRIANGLE:
                return [0, 1, 2]
            case VtkCell.PIXEL:
                return [0, 1, 3, 2]
            case VtkCell.QUAD:
                return [0, 1, 2, 3]


def _triple(values: FloatArray) -> Triple:
    """The three components of a vector as floats."""
    x, y, z = cast("list[float]", values.tolist())

    return (x, y, z)


@dataclass(frozen=True)
class Basis:
    """Where the solve frame sits in the authored model."""

    origin: Triple
    """A point on the axis, the section's ``(0, 0)``."""

    radial: Triple
    """The unit radial direction at θ = 0, the section's +X."""

    axis: Triple
    """The unit axis direction, the section's +Y."""

    DEFAULT: ClassVar[Basis]
    """The default frame's basis: radial +X, axis +Z, normal +Y."""

    SOLVE: ClassVar[Basis]
    """The solve frame itself, whose θ = π half is the section's mirror image."""

    @property
    def normal(self) -> Triple:
        """The radial direction at θ = 90°, ``axis × radial``."""
        return _triple(np.cross(np.asarray(self.axis), np.asarray(self.radial)))

    @classmethod
    def of(cls, plane: SolvePlane) -> Basis:
        """The basis of a frame's solve plane, as numpy rows."""
        return cls(
            origin=_triple(np.asarray(plane.origin.to_list(), dtype=np.float64)),
            radial=_triple(np.asarray(plane.x_dir.to_list(), dtype=np.float64)),
            axis=_triple(np.asarray(plane.y_dir.to_list(), dtype=np.float64)),
        )

    def directions(self, theta: FloatArray | float) -> tuple[FloatArray, FloatArray]:
        """The unit radial and azimuthal directions ``e_r`` and ``e_φ`` at each angle, as ``(N, 3)`` rows."""
        angles: FloatArray = np.asarray(theta, dtype=np.float64).reshape(-1, 1)
        cosine: FloatArray = np.cos(angles)
        sine: FloatArray = np.sin(angles)
        radial: FloatArray = np.asarray(self.radial)
        normal: FloatArray = np.asarray(self.normal)

        return cosine * radial + sine * normal, cosine * normal - sine * radial

    def place(self, section: FloatArray, theta: FloatArray | float) -> FloatArray:
        """The authored points of section points ``(r, z)`` swept to ``theta``; any section Z is ignored."""
        e_r, _ = self.directions(theta)

        return np.asarray(self.origin) + section[:, :1] * e_r + section[:, 1:2] * np.asarray(self.axis)

    def rotate(self, vectors: FloatArray, theta: FloatArray | float) -> FloatArray:
        """The authored form of solve-frame vectors at ``theta``: X along ``e_r``, Y along the axis, Z along ``-e_φ``."""
        e_r, e_phi = self.directions(theta)

        return vectors[:, :1] * e_r + vectors[:, 1:2] * np.asarray(self.axis) - vectors[:, 2:3] * e_phi

    def fold(self, points: FloatArray) -> tuple[FloatArray, FloatArray]:
        """The section points ``(r, z, 0)`` of authored points and their angles in ``(-π, π]``, inverting [`place`][]."""
        offset: FloatArray = points - np.asarray(self.origin)
        along: FloatArray = offset @ np.asarray(self.axis)
        x: FloatArray = offset @ np.asarray(self.radial)
        y: FloatArray = offset @ np.asarray(self.normal)
        angles: FloatArray = np.arctan2(y, x)
        theta: FloatArray = np.where(angles <= -np.pi, np.pi, angles)
        radius: FloatArray = np.hypot(x, y)

        return np.column_stack([radius, along, np.zeros_like(radius)]), theta

    def carry(self, values: AnyArray, theta: FloatArray | float) -> AnyArray:
        """``values`` rotated to ``theta`` when they are 3-component floating vectors, else unchanged."""
        if values.ndim != 2 or values.shape[1] != 3 or not np.issubdtype(values.dtype, np.floating):
            return values

        return self.rotate(values.astype(np.float64), theta).astype(values.dtype)


Basis.DEFAULT = Basis.of(Axisymmetric().plane)
Basis.SOLVE = Basis(origin=(0.0, 0.0, 0.0), radial=(1.0, 0.0, 0.0), axis=(0.0, 1.0, 0.0))


@dataclass(frozen=True)
class Block:
    """Same-sized 2D cells: their corner point ids and their cell index in the source dataset."""

    corners: IntArray
    """The ``(n, k)`` corner point ids, walking each cell's boundary."""

    cells: IntArray
    """The ``(n,)`` index of each cell in the source dataset."""

    def reversed(self) -> Block:
        """The same cells with their winding reversed, first corner kept."""
        order: list[int] = [0, *range(np.size(self.corners, 1) - 1, 0, -1)]

        return Block(self.corners[:, order], self.cells)


@dataclass(frozen=True)
class Polygons:
    """A section's triangles and quads, one block per corner count."""

    blocks: tuple[Block, ...]
    """The cell blocks; empty when the section has no 2D cells."""

    @classmethod
    def of(cls, cells: Mapping[int, IntArray], celltypes: np.typing.NDArray[np.uint8]) -> Polygons:
        """The triangles, quads and pixels (as quads) of a pyvista ``cells_dict``; every other cell type is dropped."""
        blocks: list[Block] = []
        for kind in VtkCell:
            if kind not in cells:
                continue

            corners: IntArray = np.asarray(cells[kind], dtype=np.int64)[:, kind.order]
            blocks.append(Block(corners, np.flatnonzero(np.isin(celltypes, [kind])).astype(np.int64)))

        return cls(tuple(blocks))

    def reversed(self) -> Polygons:
        """Every cell with its winding reversed."""
        return Polygons(tuple(block.reversed() for block in self.blocks))

    def oriented(self, points: FloatArray) -> Polygons:
        """Every cell wound counterclockwise in the section's XY plane."""

        def counterclockwise(block: Block) -> Block:
            """The block with its clockwise cells reversed."""
            located: FloatArray = np.take(points, block.corners, axis=0)
            x: FloatArray = located @ np.array([1.0, 0.0, 0.0])
            y: FloatArray = located @ np.array([0.0, 1.0, 0.0])
            area: FloatArray = np.sum(x * np.roll(y, -1, axis=1) - np.roll(x, -1, axis=1) * y, axis=1)
            flipped: IntArray = block.reversed().corners

            return Block(np.where((area < 0.0)[:, np.newaxis], flipped, block.corners), block.cells)

        return Polygons(tuple(counterclockwise(block) for block in self.blocks))

    def boundary(self) -> tuple[IntArray, IntArray]:
        """The ``(E, 2)`` edges used by exactly one cell, directed as that cell winds them, and each edge's owning cell."""
        edges: list[IntArray] = []
        owners: list[IntArray] = []
        for block in self.blocks:
            following: IntArray = np.roll(block.corners, -1, axis=1)
            edges.append(np.stack([block.corners, following], axis=2).reshape(-1, 2))
            owners.append(np.repeat(block.cells, np.size(block.corners, 1)))

        directed: IntArray = np.concatenate(edges)
        ordered: IntArray = np.sort(directed, axis=1)
        keys: IntArray = ordered[:, 0] * (int(ordered.max()) + 1) + ordered[:, 1]
        unique = np.unique_all(keys)
        single: BoolArray = np.isin(np.take(unique.counts, unique.inverse_indices), [1])

        return directed[single], np.concatenate(owners)[single]

    def flat(self, ids: IntArray) -> tuple[IntArray, IntArray, IntArray]:
        """The size-prefixed VTK cell array with point ids mapped through ``ids``, each cell's corner count and its source cell."""
        faces: list[IntArray] = []
        corners: list[IntArray] = []
        for block in self.blocks:
            sizes: IntArray = np.full(len(block.corners), np.size(block.corners, 1), dtype=np.int64)
            faces.append(np.column_stack([sizes, ids[block.corners]]).reshape(-1))
            corners.append(sizes)

        cells: IntArray = np.concatenate([block.cells for block in self.blocks])

        return np.concatenate(faces), np.concatenate(corners), cells


@dataclass(frozen=True)
class Surface:
    """An expanded section as arrays: its cells, and which section point and cell each of its points and cells copies at which angle."""

    points: FloatArray
    """The ``(M, 3)`` output points."""

    faces: IntArray
    """The size-prefixed VTK cell array."""

    corners: IntArray
    """Each output cell's corner count, 3 or 4."""

    point_source: IntArray
    """The section point each output point copies."""

    point_theta: FloatArray
    """The angle each output point sits at."""

    cell_source: IntArray
    """The section cell each output cell copies."""

    cell_theta: FloatArray
    """The angle each output cell's data is rotated to."""

    basis: Basis
    """The basis the angles turn about."""

    @classmethod
    def empty(cls, basis: Basis) -> Surface:
        """A surface with no points or cells."""
        ids: IntArray = np.zeros(0, dtype=np.int64)
        angles: FloatArray = np.zeros(0, dtype=np.float64)

        return cls(np.zeros((0, 3), dtype=np.float64), ids, ids, ids, angles, ids, angles, basis)

    def point_values(self, values: AnyArray, *, turns: bool = True) -> AnyArray:
        """A section point array carried onto the output points, its vectors turned with them unless ``turns`` is False."""
        copied: AnyArray = values[self.point_source]

        return self.basis.carry(copied, self.point_theta) if turns else copied

    def cell_values(self, values: AnyArray, *, turns: bool = True) -> AnyArray:
        """A section cell array carried onto the output cells, its vectors turned with them unless ``turns`` is False."""
        copied: AnyArray = values[self.cell_source]

        return self.basis.carry(copied, self.cell_theta) if turns else copied


def mirrored(points: FloatArray, polygons: Polygons) -> Surface:
    """The section's cells beside their reflection x → -x with reversed winding, both in the solve frame."""
    if not polygons.blocks:
        return Surface.empty(Basis.SOLVE)

    count: int = len(points)
    ids: IntArray = np.arange(count, dtype=np.int64)
    reflected: FloatArray = points * np.array([-1.0, 1.0, 1.0])
    faces, corners, cells = polygons.flat(ids)
    back_faces, back_corners, back_cells = polygons.reversed().flat(ids + count)

    return Surface(
        points=np.concatenate([points, reflected]),
        faces=np.concatenate([faces, back_faces]),
        corners=np.concatenate([corners, back_corners]),
        point_source=np.concatenate([ids, ids]),
        point_theta=np.repeat([0.0, np.pi], count),
        cell_source=np.concatenate([cells, back_cells]),
        cell_theta=np.repeat([0.0, np.pi], len(cells)),
        basis=Basis.SOLVE,
    )


def swept(points: FloatArray, polygons: Polygons, basis: Basis, *, segments: int = 72, sweep: float = 360.0) -> Surface:
    """The surface of the section's cells swept ``sweep`` degrees about the axis, ``segments`` steps per turn, capped when partial.

    Args:
        points: The ``(N, 3)`` section points, X = r and Y = z.
        polygons: The section's 2D cells.
        basis: Where the section sits in the authored frame.
        segments: The ring count per full turn.
        sweep: The swept angle in degrees, in ``(0, 360]``.
    """
    if segments < 3:
        raise ValueError(f"A revolve needs at least 3 segments per turn, not {segments}.")

    if not 0.0 < sweep <= 360.0:
        raise ValueError(f"The sweep must lie in (0, 360] degrees, not {sweep:g}.")

    if not polygons.blocks:
        return Surface.empty(basis)

    full: bool = sweep >= 360.0
    steps: int = segments if full else max(1, round(segments * sweep / 360.0))
    rings: int = steps if full else steps + 1
    step: float = math.radians(sweep) / steps
    angles: FloatArray = step * np.arange(rings, dtype=np.float64)

    cells: Polygons = polygons.oriented(points)
    all_edges, all_owners = cells.boundary()
    extent: float = float(np.max(np.ptp(points, axis=0)))
    on_axis: BoolArray = points[:, 0] <= AXIS_TOLERANCE * extent
    spinning: BoolArray = ~(on_axis[all_edges[:, 0]] & on_axis[all_edges[:, 1]])
    edges: IntArray = all_edges[spinning]
    owners: IntArray = all_owners[spinning]

    used: IntArray = np.unique(edges)
    ring_points: IntArray = used[~on_axis[used]]
    axis_points: IntArray = used[on_axis[used]]
    per_ring: int = len(ring_points)
    slot: IntArray = np.zeros(len(points), dtype=np.int64)
    slot[ring_points] = np.arange(per_ring)
    slot[axis_points] = rings * per_ring + np.arange(len(axis_points))
    stride: IntArray = np.where(on_axis, 0, per_ring)

    def at(ids: IntArray, ring: IntArray) -> IntArray:
        """The ``(E, S)`` output ids of section points ``ids`` on each ring; an axis point is the same on every ring."""
        return slot[ids][:, np.newaxis] + stride[ids][:, np.newaxis] * ring[np.newaxis, :]

    segment: IntArray = np.arange(steps, dtype=np.int64)
    following: IntArray = (segment + 1) % rings
    start: IntArray = edges[:, 0]
    end: IntArray = edges[:, 1]
    quads: IntArray = np.stack([at(start, segment), at(start, following), at(end, following), at(end, segment)], axis=2)
    middle: FloatArray = np.broadcast_to(angles[segment] + step / 2.0, quads.shape[:2])
    owner: IntArray = np.broadcast_to(owners[:, np.newaxis], quads.shape[:2])

    starts_on_axis: BoolArray = on_axis[start]
    ends_on_axis: BoolArray = on_axis[end]
    off_axis: BoolArray = ~(starts_on_axis | ends_on_axis)
    quad_corners: IntArray = quads[off_axis].reshape(-1, 4)
    triangles: IntArray = np.concatenate([quads[starts_on_axis][:, :, [0, 2, 3]], quads[ends_on_axis][:, :, [0, 1, 2]]]).reshape(-1, 3)
    quad_sizes: IntArray = np.full(len(quad_corners), 4, dtype=np.int64)
    triangle_sizes: IntArray = np.full(len(triangles), 3, dtype=np.int64)

    ring_xyz: FloatArray = basis.place(np.tile(points[ring_points], (rings, 1)), np.repeat(angles, per_ring))
    swept_points: FloatArray = np.concatenate([ring_xyz, basis.place(points[axis_points], 0.0)])
    surface = Surface(
        points=swept_points,
        faces=np.concatenate([np.column_stack([quad_sizes, quad_corners]).reshape(-1), np.column_stack([triangle_sizes, triangles]).reshape(-1)]),
        corners=np.concatenate([quad_sizes, triangle_sizes]),
        point_source=np.concatenate([np.tile(ring_points, rings), axis_points]),
        point_theta=np.concatenate([np.repeat(angles, per_ring), np.zeros(len(axis_points))]),
        cell_source=np.concatenate([owner[off_axis].reshape(-1), owner[starts_on_axis].reshape(-1), owner[ends_on_axis].reshape(-1)]),
        cell_theta=np.concatenate([middle[off_axis].reshape(-1), middle[starts_on_axis].reshape(-1), middle[ends_on_axis].reshape(-1)]),
        basis=basis,
    )
    if full:
        return surface

    covered: IntArray = np.unique(np.concatenate([block.corners.reshape(-1) for block in cells.blocks]))
    lookup: IntArray = np.zeros(len(points), dtype=np.int64)
    lookup[covered] = len(swept_points) + np.arange(len(covered))
    near_faces, near_corners, near_cells = cells.flat(lookup)
    far_faces, far_corners, far_cells = cells.reversed().flat(lookup + len(covered))
    last: float = math.radians(sweep)
    cap_theta: FloatArray = np.repeat([0.0, last], len(covered))

    return Surface(
        points=np.concatenate([swept_points, basis.place(np.tile(points[covered], (2, 1)), cap_theta)]),
        faces=np.concatenate([surface.faces, near_faces, far_faces]),
        corners=np.concatenate([surface.corners, near_corners, far_corners]),
        point_source=np.concatenate([surface.point_source, covered, covered]),
        point_theta=np.concatenate([surface.point_theta, cap_theta]),
        cell_source=np.concatenate([surface.cell_source, near_cells, far_cells]),
        cell_theta=np.concatenate([surface.cell_theta, np.repeat([0.0, last], len(near_cells))]),
        basis=basis,
    )


def _section(dataset: pv.DataSet) -> tuple[pv.UnstructuredGrid, FloatArray, Polygons]:  # pragma: no cover - needs pyvista (viz extra); proven by the gated tests
    """A dataset as an unstructured grid, with its points and its 2D cells."""
    import pyvista as pv

    grid: pv.UnstructuredGrid = dataset if isinstance(dataset, pv.UnstructuredGrid) else cast("pv.UnstructuredGrid", dataset.cast_to_unstructured_grid())
    kinds: set[int] = {int(kind) for kind in VtkCell}
    blocks: dict[int, IntArray] = {int(kind): np.asarray(cells, dtype=np.int64) for kind, cells in grid.cells_dict.items() if int(kind) in kinds}  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportUnknownArgumentType]  # pyvista types cells_dict values loosely

    return grid, np.asarray(grid.points, dtype=np.float64), Polygons.of(blocks, np.asarray(grid.celltypes))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # pyvista's celltypes is untyped


def _dress(source: pv.DataSet, surface: Surface, target: pv.DataSet, still: frozenset[str]) -> None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated tests
    """Copy every point and cell array of ``source`` onto ``target`` through ``surface``, the ones named in ``still`` unturned."""
    for name in source.point_data.keys():
        target.point_data[name] = surface.point_values(np.asarray(source.point_data[name]), turns=name not in still)

    for name in source.cell_data.keys():
        target.cell_data[name] = surface.cell_values(np.asarray(source.cell_data[name]), turns=name not in still)


def mirror(dataset: pv.DataSet, *, still: frozenset[str] = frozenset()) -> pv.UnstructuredGrid:  # pragma: no cover - needs pyvista (viz extra); proven by the gated tests
    """Both halves of an axisymmetric section in the solve frame: its triangles and quads beside their reflection across the axis; line and vertex cells are dropped.

    Args:
        dataset: The section in the solve frame.
        still: The array names copied unturned, such as principal values.
    """
    import pyvista as pv

    grid, points, polygons = _section(dataset)
    surface: Surface = mirrored(points, polygons)
    celltypes: np.typing.NDArray[np.uint8] = np.full(len(surface.corners), VtkCell.QUAD, dtype=np.uint8)
    celltypes[surface.corners == 3] = VtkCell.TRIANGLE
    output = pv.UnstructuredGrid(surface.faces, celltypes, surface.points)
    _dress(grid, surface, output, still)

    return output


def revolve(
    dataset: pv.DataSet, basis: Basis, *, segments: int = 72, sweep: float = 360.0, still: frozenset[str] = frozenset()
) -> pv.PolyData:  # pragma: no cover - needs pyvista (viz extra); proven by the gated tests
    """The surfaces of an axisymmetric section swept about its axis into the authored frame, capped on a partial sweep.

    Args:
        dataset: The section in the solve frame; line and vertex cells are dropped.
        basis: Where the section sits in the authored frame.
        segments: The ring count per full turn.
        sweep: The swept angle in degrees, in ``(0, 360]``.
        still: The array names copied unturned, such as principal values.
    """
    import pyvista as pv

    grid, points, polygons = _section(dataset)
    surface: Surface = swept(points, polygons, basis, segments=segments, sweep=sweep)
    if not surface.corners.size:
        return pv.PolyData()

    output = pv.PolyData(surface.points, faces=surface.faces)
    _dress(grid, surface, output, still)

    return output
