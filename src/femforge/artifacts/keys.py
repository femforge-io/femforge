"""Artifact identity: a typed content key, one canonical JSON serialization, one hash, ``HashExclude()`` honoured at any depth."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Set
from typing import TYPE_CHECKING, Self, cast

from pydantic import BaseModel

from femforge.common.names import bundle_name
from femforge.common.pydantic import hash_exclude_spec


if TYPE_CHECKING:
    from femforge.artifacts.kind import ArtifactKind


type ExcludeSpec = dict[str | int, object]
"""A pydantic-style exclude as nested dicts, where ``True`` drops the whole subtree under a key."""

_HEX_KEY = re.compile(r"[0-9a-f]{18,64}")


class ArtifactKey[S: BaseModel](str):
    """A content key (hex SHA-256), phantom-typed by the spec model it was hashed from; ``name`` is its readable directory form."""

    __slots__ = ()

    def __new__(cls, value: str) -> Self:
        if not _HEX_KEY.fullmatch(value):
            raise ValueError(f"an artifact key is 18-64 lowercase hex chars, got {value!r}")

        return str.__new__(cls, value)

    @property
    def name(self) -> str:
        """The ``<adjective>-<noun>-<hash8>`` directory name derived from this key."""
        return bundle_name(self)

    @property
    def short(self) -> str:
        """The first 12 hex chars, for logs."""
        return self[:12]


def sha256_json(payload: object) -> str:
    """Hex SHA-256 of ``json.dumps(payload, sort_keys=True)`` — the one serialization every artifact key shares."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _as_spec(exclude: object) -> ExcludeSpec | bool:
    """Normalize a pydantic-style exclude (``True``/``False``, a set of names, or a nested mapping) into nested dicts."""
    if isinstance(exclude, bool):
        return exclude

    if isinstance(exclude, Mapping):
        entries = cast("Mapping[str | int, object]", exclude)
        return {key: _as_spec(value) for key, value in entries.items()}

    if isinstance(exclude, Set):
        names = cast("Set[str]", exclude)
        return {name: True for name in names}

    return True


def merge_exclude(left: ExcludeSpec, right: Mapping[str | int, object] | None) -> ExcludeSpec:
    """Union two exclude specs; ``True`` on either side excludes the whole subtree, ``False`` on the right adds nothing."""
    merged: ExcludeSpec = dict(left)
    if right is None:
        return merged

    for key, value in right.items():
        incoming: ExcludeSpec | bool = _as_spec(value)
        existing: object = merged.get(key)
        if incoming is False:
            continue

        if incoming is True or existing is True:
            merged[key] = True
        elif isinstance(existing, dict):
            merged[key] = merge_exclude(cast("ExcludeSpec", existing), incoming)
        else:
            merged[key] = incoming
    return merged


def spec_payload(
    spec: BaseModel,
    *,
    exclude: Mapping[str | int, object] | None = None,
    context: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """``spec.model_dump(mode="json")`` with every ``HashExclude()`` field (any depth) plus ``exclude`` removed — the dict that is hashed and recorded.

    Args:
        spec: The model to dump.
        exclude: A pydantic-style exclude merged with the ``HashExclude()`` fields.
        context: The serialization context, as in ``{CANONICAL_UNITS: True}`` for quantities in base units.
    """
    excluded: ExcludeSpec = merge_exclude(hash_exclude_spec(spec), exclude)
    return spec.model_dump(mode="json", exclude=excluded or None, context=context)  # pyright: ignore[reportArgumentType]  # pydantic accepts nested int/str keyed specs


def artifact_key[S: BaseModel](kind: ArtifactKind[S], spec: S) -> ArtifactKey[S]:
    """The content key of one artifact: the kind's own identity when it overrides one, else the hash of the kind name, its producer version and the spec payload."""
    if not isinstance(spec, kind.spec_type):
        raise TypeError(f"{kind.name}: expected a {kind.spec_type.__name__} spec, got {type(spec).__name__}")

    if kind.key is not None:
        return ArtifactKey[S](kind.key(spec))

    return ArtifactKey[S](sha256_json({"kind": kind.name, "producer_version": kind.producer_version, "spec": spec_payload(spec)}))
