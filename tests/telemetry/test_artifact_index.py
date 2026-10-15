"""Tests for telemetry.artifacts: the incremental scan of a local store's sidecars and the fluent query over them."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from femforge.artifacts.keys import ArtifactKey
from femforge.artifacts.kind import ArtifactKind
from femforge.artifacts.local import LocalArtifactStore
from femforge.artifacts.sidecar import ArtifactRecord, ToolVersions
from femforge.telemetry.artifacts import ArtifactIndex
from femforge.telemetry.paths import Telemetry
from femforge.telemetry.schemas import ArtifactColumn


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest


class _Spec(BaseModel):
    a: int = 1


THINGS = ArtifactKind(name="things", spec_type=_Spec)


def _publish(store: LocalArtifactStore, key: str, *, gmsh: str | None = None) -> Path:
    """Commit one artifact under ``key`` and return its store directory."""
    typed = ArtifactKey[_Spec](key)
    staging = store.staging_dir(THINGS, typed)
    _ = (staging / "a.txt").write_bytes(b"alpha")

    record = ArtifactRecord.build(
        kind=THINGS.name,
        key=key,
        producer_version=1,
        spec_type="tests.telemetry.test_artifact_index:_Spec",
        spec={"a": 1},
        workdir=staging,
        files=["a.txt"],
        tools=ToolVersions(code_sha="sha1", gmsh=gmsh),
    )
    assert store.put(THINGS, typed, staging, record)

    return store.path(THINGS, typed)


def _index(tmp_path: Path) -> tuple[ArtifactIndex, LocalArtifactStore]:
    store = LocalArtifactStore(tmp_path / "store")

    return ArtifactIndex(Telemetry(tmp_path / "telemetry"), store), store


def test_a_committed_artifact_lands_with_its_record_and_tool_versions(tmp_path: Path) -> None:
    """One pass over the store fills the sidecar, file and provenance columns of the index."""
    index, store = _index(tmp_path)
    directory = _publish(store, "a" * 64, gmsh="4.13.1")

    assert index.update() == 1

    row = cast("dict[str, object]", index.query().frame().rows(named=True)[0])

    assert row[ArtifactColumn.KIND] == THINGS.name
    assert row[ArtifactColumn.KEY] == "a" * 64
    assert row[ArtifactColumn.NAME] == directory.name
    assert row[ArtifactColumn.STORE_ROOT] == str(store.root)
    assert row[ArtifactColumn.FILE_COUNT] == 1
    assert row[ArtifactColumn.TOTAL_BYTES] == 5
    assert row[ArtifactColumn.FILES] == ["a.txt"]
    assert row[ArtifactColumn.SPEC] == '{"a": 1}'
    assert row[ArtifactColumn.CODE_SHA] == "sha1"
    assert row[ArtifactColumn.GMSH] == "4.13.1"
    assert row[ArtifactColumn.ELMER] is None


def test_a_second_pass_reads_only_the_directories_whose_mtime_moved(tmp_path: Path) -> None:
    """The mtime anti-join is the watermark, so an unchanged store costs no sidecar read."""
    index, store = _index(tmp_path)
    _ = _publish(store, "a" * 64)

    assert index.update() == 1
    assert index.update() == 0

    _ = _publish(store, "b" * 64)

    assert index.update() == 1
    assert index.query().frame().height == 2


def test_rebuild_re_reads_every_directory(tmp_path: Path) -> None:
    """A rebuild ignores the watermark, so a schema change can be replayed over the whole store."""
    index, store = _index(tmp_path)
    _ = _publish(store, "a" * 64)
    _ = index.update()

    assert index.update(rebuild=True) == 1
    assert index.query().frame().height == 1


def test_an_empty_store_indexes_nothing(tmp_path: Path) -> None:
    """A store root that does not exist yet is not an error, it is simply no rows."""
    index, _ = _index(tmp_path)

    assert index.update() == 0


def test_a_directory_without_a_sidecar_is_not_indexed(tmp_path: Path) -> None:
    """The sidecar is the commit marker: a directory without one is not an artifact yet."""
    index, store = _index(tmp_path)
    (store.root / THINGS.name / "half-published-0000").mkdir(parents=True)

    assert index.update() == 0


def test_a_directory_that_vanishes_mid_pass_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retention can delete an artifact while the pass runs; the missing stat drops that candidate, not the pass."""
    index, store = _index(tmp_path)
    _ = _publish(store, "a" * 64)

    def _vanished(store: LocalArtifactStore, kind: str) -> Iterator[Path]:
        yield store.root / kind / "gone"

    monkeypatch.setattr(LocalArtifactStore, "committed", _vanished)

    assert index.update() == 0


def test_filters_narrow_by_kind_spec_type_key_prefix_and_age(tmp_path: Path) -> None:
    """Each filter is one predicate over the frame, and they compose."""
    index, store = _index(tmp_path)
    _ = _publish(store, "a" * 64)
    _ = _publish(store, "b" * 64)
    _ = index.update()

    query = index.query()

    assert query.kind(THINGS.name).frame().height == 2
    assert query.kind("nothing").frame().height == 0
    assert query.spec_type("_Spec").frame().height == 2
    assert query.key_prefix("a").frame().get_column(ArtifactColumn.KEY).to_list() == ["a" * 64]
    assert query.since(datetime.now(timezone.utc) - timedelta(hours=1)).frame().height == 2
    assert query.since(datetime.now(timezone.utc) + timedelta(hours=1)).frame().height == 0
    assert query.newest(1).frame().height == 1


def test_records_reads_back_only_the_artifacts_still_in_the_store(tmp_path: Path) -> None:
    """The index answers off parquet; asking for the records is what touches the store, and a deleted one is dropped."""
    index, store = _index(tmp_path)
    kept = _publish(store, "a" * 64)
    removed = _publish(store, "b" * 64)
    _ = index.update()

    shutil.rmtree(removed)

    assert [artifact.path for artifact in index.query().records()] == [kept]
