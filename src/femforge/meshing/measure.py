"""Geometry facts of the mesh groups: centroid, bounding box, measure, length and mean outward normal, read from a meshed gmsh model."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import numpy as np
from numpy.typing import NDArray

from femforge.common.vector import Direction, Vec3
from femforge.meshing.boundary import BoundaryGroup
from femforge.meshing.geometry_utils import EntityGeometry, extract_entity_geometry
from femforge.meshing.groups import Bounds, PhysicalGroup


type FloatArray = NDArray[np.float64]

NORMAL_FLOOR = 0.1
"""`[-]`: The smallest share of the measure of a group its summed area vector must reach for the group to have a mean normal."""

ORIENTATION_SAMPLES = 5
"""The largest elements of each face entity that vote on which side its body lies."""

ORIENTATION_STEP = 1e-4
"""`[-]`: How far past an element each side probe lands, as a share of the bounding-box diagonal of the body."""


def _vec3(values: FloatArray) -> Vec3:
    """The three components of ``values`` as a Vec3."""
    x, y, z = cast("list[float]", values.tolist())

    return Vec3(x, y, z)


def combine(pieces: Sequence[EntityGeometry]) -> tuple[Vec3, float]:
    """The measure-weighted centroid and the summed measure of ``pieces``; the plain mean centroid when they measure nothing."""
    measures: FloatArray = np.array([piece.volume for piece in pieces], dtype=np.float64)
    centres: FloatArray = np.array([piece.center_of_mass for piece in pieces], dtype=np.float64).reshape(-1, 3)
    total: float = float(measures.sum())
    weights: FloatArray = measures / total if total > 0.0 else np.full(len(pieces), 1.0 / len(pieces))

    return _vec3(weights @ centres), total


def bounds_of(points: FloatArray) -> Bounds:
    """The axis-aligned bounds of the ``(n, 3)`` ``points``."""
    return Bounds(low=_vec3(points.min(axis=0)), high=_vec3(points.max(axis=0)))


def area_vectors(corners: FloatArray) -> FloatArray:
    """The ``(m, 3)`` area vectors of ``m`` elements given by their ``(m, k, 3)`` corners: a segment chord turned −90° about +Z, a polygon ½Σ pᵢ × pᵢ₊₁."""
    if corners.shape[1] == 2:
        chords: FloatArray = corners[:, 1] - corners[:, 0]

        return np.stack([chords[:, 1], -chords[:, 0], np.zeros(len(chords))], axis=1)

    following: FloatArray = np.roll(corners, -1, axis=1)

    return 0.5 * np.cross(corners, following).sum(axis=1)


def mean_normal(vectors: FloatArray, sides: Sequence[int], measure: float) -> Direction | None:
    """The unit sum of the face-entity area vectors, each turned out by its side; None when a side is unknown or the sum stays below ``NORMAL_FLOOR`` of ``measure``.

    Args:
        vectors: The ``(n, 3)`` summed area vector of each face entity.
        sides: Each entity side: +1 when its vector points out of the body, -1 when in, 0 when unknown.
        measure: The summed area or length of the entities.
    """
    if 0 in sides or measure <= 0.0:
        return None

    signs: FloatArray = np.array(sides, dtype=np.float64)
    total: FloatArray = (vectors * signs[:, None]).sum(axis=0)

    if float(np.linalg.norm(total)) < NORMAL_FLOOR * measure:
        return None

    outward: Vec3 = _vec3(total)

    return Direction(outward.x, outward.y, outward.z)


def measure_groups(physical_groups: Sequence[PhysicalGroup], boundary_groups: Sequence[BoundaryGroup], body_dim: int) -> None:  # pragma: no cover - needs a live meshed gmsh model
    """Set the centroid, bounds and measure of each body group, and the centroid, bounds, length and mean outward normal of each boundary group, from the meshed gmsh model.

    Args:
        physical_groups: The body groups to measure.
        boundary_groups: The boundary groups to measure.
        body_dim: The topological dimension of the bodies.
    """
    import gmsh

    boundary_dim: int = body_dim - 1
    node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
    points: FloatArray = coordinates.reshape(-1, 3)
    lookup: NDArray[np.int64] = np.zeros(int(node_tags.max()) + 1, dtype=np.int64)
    lookup[node_tags.astype(np.int64)] = np.arange(len(node_tags))

    def element_corners(dim: int, tag: int) -> FloatArray:
        element_types, _, element_nodes = gmsh.model.mesh.getElements(dim, tag)
        blocks: list[FloatArray] = []

        for element_type, nodes in zip(element_types, element_nodes):
            _, _, _, node_count, _, primary = gmsh.model.mesh.getElementProperties(element_type)
            indices: NDArray[np.int64] = lookup[np.asarray(nodes, dtype=np.int64)].reshape(-1, node_count)[:, :primary]
            blocks.append(points[indices])

        return np.concatenate(blocks) if blocks else np.zeros((0, 2, 3))

    def group_bounds(dim: int, tags: Sequence[int]) -> Bounds | None:
        corners: list[FloatArray] = [element_corners(dim, tag).reshape(-1, 3) for tag in tags]
        stacked: FloatArray = np.concatenate(corners) if corners else np.zeros((0, 3))

        return bounds_of(stacked) if len(stacked) else None

    def side(face: int, corners: FloatArray, vectors: FloatArray, body: int, step: float) -> int:
        norms: FloatArray = np.linalg.norm(vectors, axis=1)
        sizes: list[float] = cast("list[float]", norms.tolist())
        largest: list[int] = cast("list[int]", np.argsort(norms)[::-1][:ORIENTATION_SAMPLES].tolist())
        votes: int = 0

        for index in largest:
            size: float = sizes[index]

            if size == 0.0:
                continue

            unit: FloatArray = cast("FloatArray", vectors[index]) / size
            element: FloatArray = cast("FloatArray", corners[index])
            centre: list[float] = cast("list[float]", element.mean(axis=0).tolist())
            closest, _ = gmsh.model.getClosestPoint(boundary_dim, face, centre)
            inside: list[float] = cast("list[float]", (closest - step * unit).tolist())
            outside: list[float] = cast("list[float]", (closest + step * unit).tolist())
            inner: bool = bool(gmsh.model.isInside(body_dim, body, inside))
            outer: bool = bool(gmsh.model.isInside(body_dim, body, outside))

            if inner and not outer:
                votes += 1

            if outer and not inner:
                votes -= 1

        return (votes > 0) - (votes < 0)

    entities_of: dict[str, list[int]] = {}
    diagonal_of: dict[str, float] = {}

    for group in physical_groups:
        group.centroid, group.measure = combine([extract_entity_geometry(body_dim, tag) for tag in group.entity_tags])
        group.bbox = group_bounds(body_dim, group.entity_tags)
        entities_of[group.name] = list(group.entity_tags)
        diagonal_of[group.name] = group.bbox.diagonal if group.bbox is not None else 0.0

    for boundary in boundary_groups:
        pieces: list[EntityGeometry] = [extract_entity_geometry(boundary_dim, tag) for tag in boundary.entity_tags]

        if not pieces:
            continue

        boundary.centroid, extent = combine(pieces)
        boundary.bbox = group_bounds(boundary_dim, boundary.entity_tags)
        boundary.length = extent if boundary_dim == 1 else None

        owner: list[int] = entities_of.get(boundary.bodies[0], [])
        step: float = ORIENTATION_STEP * diagonal_of.get(boundary.bodies[0], 0.0)
        vectors: list[FloatArray] = []
        sides: list[int] = []

        for face in boundary.entity_tags:
            corners: FloatArray = element_corners(boundary_dim, face)
            element_vectors: FloatArray = area_vectors(corners)
            upward: Sequence[int] = gmsh.model.getAdjacencies(boundary_dim, face)[0]
            body: int | None = next((tag for tag in upward if tag in owner), None)
            vectors.append(element_vectors.sum(axis=0))
            sides.append(0 if body is None else side(face, corners, element_vectors, body, step))

        boundary.normal = mean_normal(np.array(vectors).reshape(-1, 3), sides, extent)
