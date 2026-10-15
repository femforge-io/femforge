"""LocalArtifactStore: named directories, staged publish, first-wins races, name collisions, corrupt sidecars, verified fetches, and the key listing."""

import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from femforge.artifacts import local as local_module
from femforge.artifacts.keys import ArtifactKey
from femforge.artifacts.kind import ArtifactKind
from femforge.artifacts.local import STAGING_DIR, LocalArtifactStore
from femforge.artifacts.sidecar import ARTIFACT_FILE, ArtifactRecord
from femforge.artifacts.store import ArtifactCorrupt, ArtifactError, ArtifactMissing, ArtifactStore
from femforge.common.names import bundle_name


class _Spec(BaseModel):
    a: int = 1


KIND = ArtifactKind(name="things", spec_type=_Spec)
KEY = ArtifactKey[_Spec]("a" * 64)
OTHER = ArtifactKey[_Spec]("b" * 64)
SAME_NAME = ArtifactKey[_Spec]("a" * 18 + "c" * 46)


def _record(workdir: Path, files: list[str], key: ArtifactKey[_Spec] = KEY) -> ArtifactRecord:
    return ArtifactRecord.build(
        kind="things",
        key=key,
        producer_version=1,
        spec_type="t:_Spec",
        spec={"a": 1},
        workdir=workdir,
        files=files,
    )


def _stage(store: LocalArtifactStore, *, junk: bool = False, key: ArtifactKey[_Spec] = KEY) -> tuple[Path, ArtifactRecord]:
    staging = store.staging_dir(KIND, key)
    _ = (staging / "a.txt").write_bytes(b"alpha")
    (staging / "sub").mkdir()
    _ = (staging / "sub" / "b.txt").write_bytes(b"beta")
    if junk:
        _ = (staging / "junk.txt").write_bytes(b"junk")
        (staging / "empty").mkdir()
    return staging, _record(staging, ["a.txt", "sub/b.txt"], key)


def test_the_store_is_an_artifact_store_and_names_directories_readably(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    assert ArtifactStore in type(store).__mro__
    assert store.path(KIND, KEY) == tmp_path / "things" / bundle_name("a" * 64)
    assert store.path(KIND, KEY).name == KEY.name


def test_put_from_staging_prunes_unlisted_files_and_commits_atomically(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    staging, record = _stage(store, junk=True)

    assert (
        store.put(
            KIND,
            KEY,
            staging,
            record,
        )
        is True
    )

    home = store.path(KIND, KEY)

    assert (home / "a.txt").read_bytes() == b"alpha"
    assert (home / "sub" / "b.txt").read_bytes() == b"beta"
    assert not (home / "junk.txt").exists()
    assert not (home / "empty").exists()
    assert (home / ARTIFACT_FILE).is_file()
    assert not staging.exists()
    assert list((tmp_path / "things" / STAGING_DIR).iterdir()) == []

    stored = store.record(KIND, KEY)
    assert stored is not None

    assert stored.file_names == ("a.txt", "sub/b.txt")
    assert stored.total_bytes == 9
    assert store.exists(KIND, KEY)
    assert list(store.keys(KIND)) == [KEY]
    assert isinstance(next(iter(store.keys(KIND))), ArtifactKey)


def test_put_from_an_external_directory_copies_the_listed_files(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    external = tmp_path / "run"
    external.mkdir()
    _ = (external / "a.txt").write_bytes(b"alpha")
    _ = (external / "other.txt").write_bytes(b"other")

    assert (
        store.put(
            KIND,
            KEY,
            external,
            _record(external, ["a.txt"]),
        )
        is True
    )
    assert (store.path(KIND, KEY) / "a.txt").read_bytes() == b"alpha"
    assert not (store.path(KIND, KEY) / "other.txt").exists()
    assert (external / "a.txt").is_file()

    # A second publish of an existing key from outside the store is refused without touching the source.
    assert (
        store.put(
            KIND,
            KEY,
            external,
            _record(external, ["a.txt"]),
        )
        is False
    )
    assert (external / "a.txt").is_file()


def test_two_publishers_racing_on_one_key(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    first, first_record = _stage(store)
    second, second_record = _stage(store)

    assert (
        store.put(
            KIND,
            KEY,
            first,
            first_record,
        )
        is True
    )
    assert (
        store.put(
            KIND,
            KEY,
            second,
            second_record,
        )
        is False
    )
    assert not second.exists()
    assert list(store.keys(KIND)) == [KEY]


def test_a_lost_rename_discards_the_staging_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LocalArtifactStore(tmp_path)
    staging, record = _stage(store)

    def taken(_src: Path, _dst: Path) -> None:
        raise PermissionError("target exists")

    monkeypatch.setattr(local_module, "_rename_dir", taken)
    assert (
        store.put(
            KIND,
            KEY,
            staging,
            record,
        )
        is False
    )
    assert not staging.exists()


def test_two_keys_sharing_a_name_never_serve_each_other(tmp_path: Path) -> None:
    assert SAME_NAME.name == KEY.name
    store = LocalArtifactStore(tmp_path)
    staging, record = _stage(store)
    assert store.put(
        KIND,
        KEY,
        staging,
        record,
    )

    assert store.record(KIND, SAME_NAME) is None
    assert not store.exists(KIND, SAME_NAME)
    with pytest.raises(ArtifactMissing):
        _ = store.fetch(KIND, SAME_NAME, tmp_path / "into")

    other_staging, other_record = _stage(store, key=SAME_NAME)
    with pytest.raises(ArtifactError, match="name collision"):
        _ = store.put(
            KIND,
            SAME_NAME,
            other_staging,
            other_record,
        )
    assert list(store.keys(KIND)) == [KEY]


def test_a_corrupt_sidecar_is_quarantined_and_republishable(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    staging, record = _stage(store)
    assert store.put(
        KIND,
        KEY,
        staging,
        record,
    )
    _ = (store.path(KIND, KEY) / ARTIFACT_FILE).write_text("{garbage", encoding="utf-8")

    assert store.record(KIND, KEY) is None
    assert not store.exists(KIND, KEY)
    leftovers = [p.name for p in (tmp_path / "things").iterdir() if p.name.startswith(KEY.name)]
    assert [name.startswith(f"{KEY.name}.corrupt-") for name in leftovers] == [True]
    assert list(store.keys(KIND)) == []

    staging, record = _stage(store)
    assert (
        store.put(
            KIND,
            KEY,
            staging,
            record,
        )
        is True
    )
    assert store.record(KIND, KEY) is not None


def test_a_quarantine_that_cannot_move_is_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LocalArtifactStore(tmp_path)
    staging, record = _stage(store)
    assert store.put(
        KIND,
        KEY,
        staging,
        record,
    )
    _ = (store.path(KIND, KEY) / ARTIFACT_FILE).write_text("{garbage", encoding="utf-8")

    def stuck(_src: Path, _dst: Path) -> None:
        raise PermissionError("in use")

    monkeypatch.setattr(local_module, "_rename_dir", stuck)
    assert store.record(KIND, KEY) is None
    assert store.path(KIND, KEY).is_dir()


def test_record_verify_reports_missing_resized_and_rewritten_files(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    staging, record = _stage(store)
    assert record.verify(staging) == []
    assert record.verify(staging, hashes=True) == []

    _ = (staging / "a.txt").write_bytes(b"ALPHA")
    assert record.verify(staging) == []
    assert record.verify(staging, hashes=True) == ["a.txt"]

    _ = (staging / "a.txt").write_bytes(b"longer than before")
    (staging / "sub" / "b.txt").unlink()
    assert record.verify(staging) == ["a.txt", "sub/b.txt"]


def test_fetch_materializes_checks_sizes_and_verifies_hashes(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    staging, record = _stage(store)
    assert store.put(
        KIND,
        KEY,
        staging,
        record,
    )

    into = tmp_path / "into"
    into.mkdir()
    _ = (into / "a.txt").write_bytes(b"stale")

    fetched = store.fetch(
        KIND,
        KEY,
        into,
        verify=True,
    )

    assert fetched.key == KEY
    assert (into / "a.txt").read_bytes() == b"alpha"
    assert (into / "sub" / "b.txt").read_bytes() == b"beta"

    with pytest.raises(ArtifactMissing):
        _ = store.fetch(KIND, OTHER, into)

    home = store.path(KIND, KEY)
    _ = (home / "a.txt").write_bytes(b"ALPHA")
    with pytest.raises(ArtifactCorrupt, match="sha256"):
        _ = store.fetch(
            KIND,
            KEY,
            into,
            verify=True,
        )
    _ = store.fetch(KIND, KEY, into)

    _ = (home / "a.txt").write_bytes(b"short")
    _ = store.fetch(KIND, KEY, into)
    (home / "sub" / "b.txt").unlink()
    with pytest.raises(ArtifactCorrupt, match="missing or the wrong size"):
        _ = store.fetch(KIND, KEY, into)


def test_link_mode_hardlinks_when_the_volume_allows(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store", link=True)
    staging, record = _stage(store)
    assert store.put(
        KIND,
        KEY,
        staging,
        record,
    )

    into = tmp_path / "into"
    _ = store.fetch(KIND, KEY, into)
    src = store.path(KIND, KEY) / "a.txt"

    assert (into / "a.txt").read_bytes() == b"alpha"
    assert os.stat(src).st_ino == os.stat(into / "a.txt").st_ino or os.stat(src).st_nlink == 1


def test_keys_lists_only_committed_artifacts_of_the_kind(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    assert list(store.keys(KIND)) == []

    staging, record = _stage(store)
    assert store.put(
        KIND,
        KEY,
        staging,
        record,
    )

    (tmp_path / "things" / "orphan-dir-00000000").mkdir()
    foreign = tmp_path / "things" / OTHER.name
    foreign.mkdir()
    foreign_record = _record(staging if staging.exists() else tmp_path, [], OTHER).model_copy(update={"kind": "elsewhere"})
    _ = (foreign / ARTIFACT_FILE).write_text(foreign_record.model_dump_json(), encoding="utf-8")

    claim = store.try_claim(KIND, OTHER)
    assert claim is not None
    assert list(store.keys(KIND)) == [KEY]

    store.delete(KIND, KEY)
    assert list(store.keys(KIND)) == []
    assert store.try_claim(KIND, OTHER) is None
    claim.release()
    assert store.try_claim(KIND, OTHER) is not None
