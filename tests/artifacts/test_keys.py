"""Artifact keys: typed, stable for equal specs, sensitive to kind and producer version, blind to HashExclude fields at any depth."""

from typing import Annotated

import pytest
from pydantic import BaseModel

from femforge.artifacts.keys import ArtifactKey, artifact_key, merge_exclude, sha256_json, spec_payload
from femforge.artifacts.kind import ArtifactKind, BuiltinKind
from femforge.common.names import bundle_name
from femforge.common.pydantic import HashExclude


class _Inner(BaseModel):
    depth: int = 1
    hidden: Annotated[int, HashExclude()] = 0


class _Spec(BaseModel):
    a: int = 1
    b: float = 2.5
    secret: Annotated[int, HashExclude()] = 0
    inner: _Inner = _Inner()
    items: list[_Inner] = [_Inner(), _Inner(depth=2)]


class _Twin(BaseModel):
    """Field-for-field the same as _Spec under a different name."""

    a: int = 1
    b: float = 2.5
    secret: Annotated[int, HashExclude()] = 0
    inner: _Inner = _Inner()
    items: list[_Inner] = [_Inner(), _Inner(depth=2)]


KIND = ArtifactKind(name="spec", spec_type=_Spec)


def test_artifact_key_is_a_validated_hex_string_with_a_readable_name() -> None:
    key = ArtifactKey[_Spec]("a" * 64)

    assert key == "a" * 64
    assert isinstance(key, str)
    assert key.short == "a" * 12
    assert key.name == bundle_name("a" * 64)
    assert ArtifactKey[_Spec]("0123456789abcdef01").name == bundle_name("0123456789abcdef01")

    for bad in ("", "xyz", "ABCDEF0123456789ab", "0" * 17, "0" * 65):
        with pytest.raises(ValueError, match="hex"):
            _ = ArtifactKey[_Spec](bad)


def test_sha256_json_is_order_independent() -> None:
    assert sha256_json({"b": 1, "a": [1, 2]}) == sha256_json({"a": [1, 2], "b": 1})
    assert sha256_json({"a": 1}) != sha256_json({"a": 2})


def test_keys_are_stable_and_ignore_hash_excluded_fields_at_any_depth() -> None:
    base = artifact_key(KIND, _Spec())

    assert isinstance(base, ArtifactKey)
    assert base == artifact_key(KIND, _Spec())
    assert base == artifact_key(KIND, _Spec(secret=99))
    assert base == artifact_key(KIND, _Spec(inner=_Inner(hidden=5)))
    assert base == artifact_key(KIND, _Spec(items=[_Inner(hidden=7), _Inner(depth=2, hidden=8)]))
    assert base != artifact_key(KIND, _Spec(a=2))
    assert base != artifact_key(KIND, _Spec(inner=_Inner(depth=3)))
    assert base != artifact_key(KIND, _Spec(items=[_Inner()]))


def test_kind_name_and_producer_version_enter_the_key() -> None:
    same_spec = _Spec()
    assert artifact_key(KIND, same_spec) != artifact_key(ArtifactKind(name="other", spec_type=_Spec), same_spec)
    versioned = ArtifactKind(name="spec", spec_type=_Spec, producer_version=2)
    assert artifact_key(KIND, same_spec) != artifact_key(versioned, same_spec)


def test_the_spec_class_name_does_not_enter_the_key() -> None:
    twin_kind = ArtifactKind(name="spec", spec_type=_Twin)
    assert artifact_key(KIND, _Spec()) == artifact_key(twin_kind, _Twin())


def test_a_key_override_wins() -> None:
    fixed = ArtifactKind(name="fixed", spec_type=_Spec, key=lambda spec: "f" * 64)
    assert artifact_key(fixed, _Spec(a=123)) == "f" * 64


def test_the_wrong_spec_type_is_rejected() -> None:
    with pytest.raises(TypeError, match="expected a _Spec spec"):
        _ = artifact_key(KIND, _Twin())  # pyright: ignore[reportArgumentType]  # the wrong type on purpose


def test_kind_names_are_lower_snake() -> None:
    with pytest.raises(ValueError, match="must match"):
        _ = ArtifactKind(name="Bad Name", spec_type=_Spec)
    assert [kind.value for kind in BuiltinKind] == ["step", "elmer_mesh", "run_bundle"]


def test_spec_payload_merges_extra_exclusions() -> None:
    payload = spec_payload(_Spec(), exclude={"inner": True, "items": {0: {"depth"}}})
    assert "secret" not in payload
    assert "inner" not in payload
    assert payload["items"] == [{}, {"depth": 2}]


def test_merge_exclude_lets_true_dominate_and_unions_subtrees() -> None:
    assert merge_exclude({"a": {"x": True}}, {"a": True}) == {"a": True}
    assert merge_exclude({"a": True}, {"a": {"x": True}}) == {"a": True}
    assert merge_exclude({"a": {"x": True}}, {"a": {"y"}}) == {"a": {"x": True, "y": True}}
    assert merge_exclude({}, {"b": 5}) == {"b": True}
    assert merge_exclude({}, {"b": {"x"}}) == {"b": {"x": True}}
    assert merge_exclude({"b": True}, {"b": {"x"}}) == {"b": True}
    assert merge_exclude({"a": {"x": True}}, {"a": False}) == {"a": {"x": True}}
    assert merge_exclude({}, None) == {}
