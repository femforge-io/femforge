"""LocalArtifactStore browsing: kinds, scan, locate by key / prefix / name / path, materialize, and the directory helpers."""

import shutil
from pathlib import Path

import pytest
from pydantic import BaseModel

from femforge.artifacts.keys import ArtifactKey
from femforge.artifacts.kind import ArtifactKind
from femforge.artifacts.local import LocalArtifactStore, StoredArtifact, artifact_dir_of, store_root_of
from femforge.artifacts.sidecar import ARTIFACT_FILE, ArtifactRecord
from femforge.artifacts.store import ArtifactCorrupt


class _Spec(BaseModel):
    a: int = 1


THINGS = ArtifactKind(name="things", spec_type=_Spec)
OTHERS = ArtifactKind(name="others", spec_type=_Spec)
KEY = ArtifactKey[_Spec]("a" * 64)
OTHER = ArtifactKey[_Spec]("b" * 64)
SHARED_PREFIX = ArtifactKey[_Spec]("a" * 8 + "d" * 56)


def _publish(store: LocalArtifactStore, kind: ArtifactKind[_Spec], key: ArtifactKey[_Spec], content: bytes = b"alpha") -> Path:
    staging = store.staging_dir(kind, key)
    _ = (staging / "a.txt").write_bytes(content)

    record = ArtifactRecord.build(
        kind=kind.name,
        key=key,
        producer_version=1,
        spec_type="t:_Spec",
        spec={"a": 1},
        workdir=staging,
        files=["a.txt"],
    )
    assert store.put(kind, key, staging, record)

    return store.path(kind, key)


def _names(artifacts: list[StoredArtifact]) -> list[tuple[str, str]]:
    return [(artifact.kind, artifact.name) for artifact in artifacts]


def test_kinds_and_scan_list_committed_artifacts_by_kind_then_name(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    _ = _publish(store, THINGS, KEY)
    _ = _publish(store, THINGS, OTHER)
    _ = _publish(store, OTHERS, OTHER)

    (tmp_path / "things" / "held.lock").mkdir()
    (tmp_path / "things" / "old.corrupt-20260101T000000").mkdir()
    (tmp_path / ".hidden").mkdir()
    _ = (tmp_path / "stray.txt").write_text("x", encoding="utf-8")

    things = sorted([("things", KEY.name), ("things", OTHER.name)])
    assert store.kinds() == ["others", "things"]
    assert _names(list(store.scan())) == [("others", OTHER.name), *things]
    assert _names(list(store.scan("things"))) == things
    assert LocalArtifactStore(tmp_path / "nowhere").kinds() == []


def test_scan_skips_a_directory_whose_sidecar_names_another_kind(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    home = _publish(store, THINGS, KEY)

    (tmp_path / "others").mkdir()
    _ = shutil.copytree(home, tmp_path / "others" / home.name)

    assert _names(list(store.scan("others"))) == []
    assert store.read(tmp_path / "others" / "missing") is None


def test_locate_by_full_key_prefix_name_and_path(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    home = _publish(store, THINGS, KEY)
    _ = _publish(store, THINGS, SHARED_PREFIX)

    for ref in (str(KEY), str(KEY).upper(), str(KEY)[:12], KEY.name, str(home)):
        matches = store.locate(ref)
        assert _names(matches) == [("things", KEY.name)], ref
        assert matches[0].key == KEY and matches[0].path == home

    assert _names(store.locate("a" * 8)) == [("things", KEY.name), ("things", SHARED_PREFIX.name)]
    assert store.locate("c" * 64) == []
    assert store.locate("nope-nope-00000000") == []
    assert store.locate("0" * 8) == []


def test_locate_a_directory_without_a_readable_sidecar_is_empty(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    broken = tmp_path / "things" / "broken-dir-00000000"
    broken.mkdir(parents=True)
    _ = (broken / ARTIFACT_FILE).write_text("{not json", encoding="utf-8")

    assert store.locate(str(broken)) == []


def test_materialize_copies_listed_files_and_verifies_them(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    home = _publish(store, THINGS, KEY)
    artifact = store.locate(str(home))[0]

    into = tmp_path / "out"
    assert store.materialize(artifact, into, verify=True) == [into / "a.txt"]
    assert (into / "a.txt").read_bytes() == b"alpha"

    _ = (home / "a.txt").write_bytes(b"alpha!")
    with pytest.raises(ArtifactCorrupt, match="wrong size"):
        _ = store.materialize(artifact, into)

    _ = (home / "a.txt").write_bytes(b"alphb")
    with pytest.raises(ArtifactCorrupt, match="sha256"):
        _ = store.materialize(artifact, into, verify=True)


def test_artifact_dir_of_and_store_root_of_walk_up_to_the_sidecar(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    home = _publish(store, THINGS, KEY)

    assert artifact_dir_of(home) == home
    assert artifact_dir_of(home / "a.txt") == home
    assert artifact_dir_of(home / "deeper" / "missing.txt") == home
    assert artifact_dir_of(tmp_path) is None

    assert store_root_of(home / "a.txt") == tmp_path / "store"
    assert store_root_of(tmp_path / "elsewhere") is None
