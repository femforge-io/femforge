"""Mesh-sizing logic (no gmsh): resolve_region_sizes picks the per-region overrides, apply_mesh_sizing emits them
through an injectable seam. The gmsh-backed setters are proven by test_mesh_sizing_integration.py.
"""

from __future__ import annotations

from collections.abc import Sequence

from femforge.common.vector import Direction
from femforge.physical.domain import EntityTag
from femforge.meshing.mesh_sizing import ResolvedRegionMeshSize, apply_mesh_sizing, resolve_region_sizes
from femforge.physical.conditions import Magnetization
from femforge.physical.units import U


class _FakeRegion:
    """Structural stand-in for a PhysicalGroup: the volume tags + EntityTags the sizing reads."""

    def __init__(self, entity_tags: list[int], tags: list[EntityTag]) -> None:
        self.entity_tags: list[int] = entity_tags
        self.tags: list[EntityTag] = tags


def test_resolve_skips_regions_with_no_requested_size() -> None:
    # A region whose tags request no mesh_size inherits the global size -> contributes no override.
    regions: list[_FakeRegion] = [_FakeRegion([1], [EntityTag(selector={"Region": "bulk"})])]
    assert resolve_region_sizes(regions) == []


def test_resolve_uses_the_region_volume_tags_and_size() -> None:
    regions: list[_FakeRegion] = [_FakeRegion([4, 5], [EntityTag(selector={"Region": "gap"}, mesh_size=0.2 * U.m)])]
    assert resolve_region_sizes(regions) == [ResolvedRegionMeshSize(volume_tags=(4, 5), size=0.2)]


def test_resolve_finest_requested_size_wins() -> None:
    # Multiple tags on one region requesting sizes -> the smallest (finest) is applied.
    tags: list[EntityTag] = [
        EntityTag(selector={"Region": "coarse"}, mesh_size=1.0 * U.m),
        EntityTag(selector={"Region": "fine"}, mesh_size=0.25 * U.m),
        EntityTag(selector={"Region": "oriented"}, conditions=[Magnetization(direction=Direction(0, 1, 0))]),  # no size -> ignored
    ]
    assert resolve_region_sizes([_FakeRegion([7], tags)]) == [ResolvedRegionMeshSize(volume_tags=(7,), size=0.25)]


def test_resolve_multiple_regions() -> None:
    regions: list[_FakeRegion] = [
        _FakeRegion([1], [EntityTag(selector={"Region": "bulk"})]),  # omitted
        _FakeRegion([2], [EntityTag(selector={"Region": "gap"}, mesh_size=0.5 * U.m)]),
    ]
    assert resolve_region_sizes(regions) == [ResolvedRegionMeshSize(volume_tags=(2,), size=0.5)]


def test_resolve_converts_the_requested_size_to_metres() -> None:
    regions: list[_FakeRegion] = [_FakeRegion([3], [EntityTag(selector={"Region": "gap"}, mesh_size=0.5 * U.mm)])]
    assert resolve_region_sizes(regions) == [ResolvedRegionMeshSize(volume_tags=(3,), size=0.0005)]


def test_apply_emits_global_then_each_region_override() -> None:
    calls: list[tuple[str, object]] = []

    def set_global(size: float) -> None:
        calls.append(("global", size))

    def set_region(volume_tags: Sequence[int], size: float) -> None:
        calls.append(("region", (tuple(volume_tags), size)))

    apply_mesh_sizing(
        1.0,
        [ResolvedRegionMeshSize(volume_tags=(2,), size=0.5), ResolvedRegionMeshSize(volume_tags=(3, 4), size=0.1)],
        set_global=set_global,
        set_region=set_region,
    )

    # Global first, then each region override in order.
    assert calls == [
        ("global", 1.0),
        ("region", ((2,), 0.5)),
        ("region", ((3, 4), 0.1)),
    ]


def test_apply_with_no_regions_still_sets_global() -> None:
    calls: list[float] = []
    apply_mesh_sizing(2.0, [], set_global=calls.append, set_region=lambda _tags, _size: None)
    assert calls == [2.0]
