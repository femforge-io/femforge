"""Memorable bundle names: format, determinism (golden-pinned), hash derivation, and input validation."""

import hashlib
import re

import pytest

from femforge.common.names import _ADJECTIVES, _NOUNS, bundle_name


def test_name_is_deterministic_and_golden_pinned() -> None:
    # Golden pin: the word lists and index derivation are FROZEN — a failure here means an edit reshuffled every existing bundle name.
    key = hashlib.sha256(b"x").hexdigest()
    assert bundle_name(key) == "white-avocet-2d711642"
    assert bundle_name(key) == bundle_name(key)


def test_vocabularies_are_wellformed() -> None:
    # Directory-safe words only, and no duplicates (a duplicate silently shrinks the name space).
    for words in (_ADJECTIVES, _NOUNS):
        assert len(set(words)) == len(words)
        assert all(re.fullmatch(r"[a-z]+", word) for word in words)


def test_name_format_keeps_the_key_prefix_and_ignores_case() -> None:
    key = hashlib.sha256(b"anything").hexdigest()
    name = bundle_name(key)
    assert re.fullmatch(r"[a-z]+-[a-z]+-[0-9a-f]{8}", name)
    assert name.endswith(key[:8])
    assert bundle_name(key.upper()) == name


def test_distinct_keys_name_distinctly() -> None:
    # 32 suffix bits + ~18 word bits: 64 draws colliding would be a ~1e-12 event.
    names = {bundle_name(hashlib.sha256(str(index).encode()).hexdigest()) for index in range(64)}
    assert len(names) == 64


def test_non_hex_or_short_keys_are_rejected() -> None:
    for bad in ("", "abc", "not-a-hash-at-all", "12345678"):
        with pytest.raises(ValueError, match="hex content key"):
            _ = bundle_name(bad)
