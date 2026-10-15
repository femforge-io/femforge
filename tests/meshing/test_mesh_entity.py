"""MeshEntity's gmsh-free logic: tag-based material matching and domain selector matching."""

from __future__ import annotations

from femforge.common.tags import HasTags, TagKey
from femforge.materials.copper import Copper
from femforge.meshing.mesh_entity import MeshEntity
from femforge.physical.domain import DomainConfig, EntityTag


def _entity(tags: dict[str, str], domain: DomainConfig) -> MeshEntity:
    # __init__ reads a live gmsh model (gated integration territory); build the object bare to exercise the
    # pure matching logic.
    entity = MeshEntity.__new__(MeshEntity)
    HasTags.__init__(entity)
    entity.domain = domain
    entity.set_tags(tags)
    return entity


def test_match_material_is_none_without_a_material_tag() -> None:
    domain = DomainConfig(materials=[Copper()])
    assert _entity({TagKey.PHASE: "0"}, domain)._match_material() is None


def test_match_material_resolves_case_insensitively() -> None:
    domain = DomainConfig(materials=[Copper()])
    material = _entity({TagKey.MATERIAL: "copper"}, domain)._match_material()
    assert material is not None and material.tag == Copper().tag
    assert _entity({TagKey.MATERIAL: "granite"}, domain)._match_material() is None


def test_entity_tags_lists_matching_selectors_in_domain_order() -> None:
    phase0 = EntityTag(selector={TagKey.PHASE: "0"})
    entry = EntityTag(selector={TagKey.PHASE: "0", TagKey.CURRENT_DIR: "Entry"})
    domain = DomainConfig(tags=[phase0, entry])
    entity = _entity({TagKey.PHASE: "0", TagKey.CURRENT_DIR: "Entry", TagKey.MATERIAL: "Copper"}, domain)
    assert entity.entity_tags == [phase0, entry]
    assert _entity({TagKey.PHASE: "1"}, domain).entity_tags == []
