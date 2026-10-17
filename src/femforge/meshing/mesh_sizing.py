"""Mesh element sizing: the domain-wide size plus per-region overrides, decided without gmsh and applied through injectable setters."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from femforge.physical.domain import EntityTag


class _SizedRegion(Protocol):
    """A meshed region the sizing reads: its volume (entity) tags and the EntityTags that may request a size."""

    entity_tags: list[int]
    tags: list[EntityTag]


@dataclass(frozen=True)
class ResolvedRegionMeshSize:
    """A resolved per-region target element size and the volume tags it applies to."""

    volume_tags: tuple[int, ...]
    """The gmsh tags of the region's bodies (volumes in 3D, surfaces in 2D)."""

    size: float
    """`h` `[m]`: Target element size."""


def resolve_region_sizes(groups: Iterable[_SizedRegion]) -> list[ResolvedRegionMeshSize]:
    """The per-region size overrides to apply, in metres: each group's finest requested ``mesh_size``, leaving out groups that request none."""
    assignments: list[ResolvedRegionMeshSize] = []
    for group in groups:
        requested: list[float] = [tag.mesh_size.m_as("m") for tag in group.tags if tag.mesh_size is not None]
        if not requested:
            continue

        region_size = ResolvedRegionMeshSize(
            volume_tags=tuple(group.entity_tags),
            size=min(requested),
        )
        assignments.append(region_size)

    return assignments


def apply_mesh_sizing(
    global_size: float,
    region_sizes: Iterable[ResolvedRegionMeshSize],
    *,
    set_global: Callable[[float], None],
    set_region: Callable[[Sequence[int], float], None],
) -> None:
    """Apply the global element size to the whole domain, then each region override on top of it.

    Args:
        global_size: Domain-wide target element size in metres.
        region_sizes: The per-region overrides, applied in order.
        set_global: Applies the domain-wide size.
        set_region: Applies one size to the given body tags.
    """
    set_global(global_size)
    for region in region_sizes:
        set_region(region.volume_tags, region.size)


def gmsh_set_global_size(size: float) -> None:  # pragma: no cover - drives live gmsh, exercised by the gated integration test
    """Set the characteristic length at every geometry point -- the domain-wide target element size."""
    import gmsh

    gmsh.model.mesh.setSize(gmsh.model.getEntities(0), size)


def gmsh_set_region_size(volume_tags: Sequence[int], size: float, body_dim: int = 3) -> None:  # pragma: no cover - drives live gmsh, exercised by the gated integration test
    """Set the characteristic length on the points of the given bodies, overriding the global size there.

    Args:
        volume_tags: The gmsh tags of the bodies to size.
        size: Target element size in metres.
        body_dim: Topological dimension of the bodies.
    """
    import gmsh

    body_dimtags = [(body_dim, tag) for tag in volume_tags]
    points: list[tuple[int, int]] = gmsh.model.getBoundary(body_dimtags, combined=False, oriented=False, recursive=True)
    gmsh.model.mesh.setSize(points, size)
