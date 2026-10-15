"""Map/HashExclude metadata and reflection: get_map_key reads a field's Map() key; mapped_items emits (key, value) for mapped, non-None fields; hash_excluded_fields names the HashExclude() fields."""

from __future__ import annotations

from typing import Annotated

from annotated_types import Gt
from pydantic import BaseModel, SerializeAsAny

from femforge.common.pydantic import HashExclude, Map, get_map_key, hash_exclude_spec, hash_excluded_fields, mapped_items


class _Model(BaseModel):
    mapped: Annotated[int, Map("ext.mapped")] = 1
    mapped_none: Annotated[int | None, Map("ext.none")] = None
    plain: int = 5  # no Map() -> never emitted
    with_other_meta: Annotated[int, Gt(0), Map("ext.other")] = 2  # Map after another metadata entry
    unhashed: Annotated[float, HashExclude()] = 0.5
    unhashed_mapped: Annotated[int, Map("ext.unhashed"), HashExclude()] = 3


def test_hash_excluded_fields_names_only_the_marked_fields() -> None:
    assert hash_excluded_fields(_Model) == {"unhashed", "unhashed_mapped"}


def test_get_map_key_returns_the_map_or_none() -> None:
    fields = _Model.model_fields
    assert get_map_key(fields["mapped"]) == "ext.mapped"
    # Iterates past the Gt(0) metadata to find the Map.
    assert get_map_key(fields["with_other_meta"]) == "ext.other"
    # No Map() metadata at all -> None.
    assert get_map_key(fields["plain"]) is None


def test_mapped_items_skips_unmapped_and_none() -> None:
    # plain has no Map (skipped); mapped_none is None (skipped); the rest emit at their current values.
    assert list(mapped_items(_Model())) == [("ext.mapped", 1), ("ext.other", 2), ("ext.unhashed", 3)]


def test_mapped_items_reflects_current_values() -> None:
    model: _Model = _Model(mapped=9, mapped_none=7)
    assert dict(mapped_items(model)) == {"ext.mapped": 9, "ext.none": 7, "ext.other": 2, "ext.unhashed": 3}


class _Leaf(BaseModel):
    depth: int = 1
    hidden: Annotated[int, HashExclude()] = 0


class _Base(BaseModel):
    depth: int = 1


class _Sub(_Base):
    secret: Annotated[int, HashExclude()] = 0


class _Tree(BaseModel):
    plain: int = 1
    top: Annotated[int, HashExclude()] = 0
    leaf: _Leaf = _Leaf()
    leaves: list[_Leaf] = [_Leaf(), _Leaf()]
    named: dict[str, _Leaf] = {"k": _Leaf()}
    poly: SerializeAsAny[_Base] = _Sub()
    numbers: list[int] = [1, 2]
    clean: _Base = _Base()


def test_hash_exclude_spec_walks_models_sequences_and_mappings() -> None:
    assert hash_exclude_spec(_Tree()) == {
        "top": True,
        "leaf": {"hidden": True},
        "leaves": {0: {"hidden": True}, 1: {"hidden": True}},
        "named": {"k": {"hidden": True}},
        "poly": {"secret": True},
    }
    assert hash_exclude_spec(_Base()) == {}
    dumped = _Tree().model_dump(exclude=hash_exclude_spec(_Tree()))  # pyright: ignore[reportArgumentType]  # pydantic accepts nested int/str keyed specs
    assert dumped["poly"] == {"depth": 1}
    assert dumped["leaves"] == [{"depth": 1}, {"depth": 1}]
