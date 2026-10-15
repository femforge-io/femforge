"""Tests for telemetry.hive and telemetry.paths: shard naming, typed scans, partition bodies, compaction and root resolution."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

import polars as pl
import pytest

from femforge.telemetry.hive import SHARD_SUFFIX, HiveStore, coerce, schema_runs, shard_name, write_shard
from femforge.telemetry.paths import TELEMETRY_ENV, Telemetry, TelemetryDir, TelemetryRootMissing, require_root, resolve_root
from femforge.telemetry.schemas import LOG_PARTITIONS, LOG_SCHEMA, LogColumn


if TYPE_CHECKING:
    from pathlib import Path


def _rows(count: int, *, day: date = date(2026, 9, 5), process: str = "lab") -> pl.DataFrame:
    stamps: list[object] = [datetime(2026, 9, 5, 12, index, tzinfo=timezone.utc) for index in range(count)]
    body: dict[str, list[object]] = {
        LogColumn.DATE: [day] * count,
        LogColumn.PROCESS: [process] * count,
        LogColumn.TIME: stamps,
        LogColumn.LEVEL: ["INFO"] * count,
        LogColumn.LEVEL_NO: [20] * count,
        LogColumn.NAME: ["mod"] * count,
        LogColumn.FUNCTION: ["fn"] * count,
        LogColumn.LINE: [7] * count,
        LogColumn.MESSAGE: [f"row {index}" for index in range(count)],
        LogColumn.RUN_ID: [None] * count,
        LogColumn.SWEEP_ID: [None] * count,
        LogColumn.POINT_KEY: [None] * count,
        LogColumn.HOST: ["box"] * count,
        LogColumn.PID: [1] * count,
        LogColumn.THREAD: ["MainThread"] * count,
        LogColumn.EXCEPTION: [None] * count,
        LogColumn.EXTRA: [None] * count,
    }

    return pl.DataFrame(body, schema=LOG_SCHEMA)


def _store(root: Path) -> HiveStore:
    return HiveStore(root / TelemetryDir.LOGS, LOG_SCHEMA, LOG_PARTITIONS)


def test_scan_of_an_empty_store_is_typed_and_empty(tmp_path: Path) -> None:
    """A store with no shards still scans as a frame carrying the full schema."""
    store = _store(tmp_path)

    frame = store.scan().collect()

    assert frame.height == 0
    assert frame.schema == LOG_SCHEMA


def test_append_round_trips_every_column_and_dtype(tmp_path: Path) -> None:
    """What append writes, scan reads back with the declared dtypes and column order."""
    store = _store(tmp_path)

    _ = store.append(_rows(3))
    frame = store.scan().collect()

    assert frame.height == 3
    assert frame.schema == LOG_SCHEMA
    assert frame.get_column(LogColumn.MESSAGE).to_list() == ["row 0", "row 1", "row 2"]


def test_partition_columns_are_absent_from_the_shard_bodies(tmp_path: Path) -> None:
    """Partition values live in the directory names only; the hive schema types them back on read."""
    store = _store(tmp_path)

    shards = store.append(_rows(2))
    body = pl.read_parquet(shards[0])

    assert LogColumn.DATE not in body.columns
    assert LogColumn.PROCESS not in body.columns
    assert body.schema == store.body_schema
    assert shards[0].parent.name == "process=lab"
    assert shards[0].parent.parent.name == "date=2026-09-05"
    assert store.scan().collect().get_column(LogColumn.DATE).to_list() == [date(2026, 9, 5)] * 2


def test_one_shard_is_written_per_partition(tmp_path: Path) -> None:
    """Rows spanning two dates and two processes land in four partition directories."""
    store = _store(tmp_path)

    frame = pl.concat(
        [
            _rows(1, day=date(2026, 9, 5), process="lab"),
            _rows(1, day=date(2026, 9, 5), process="cli"),
            _rows(1, day=date(2026, 9, 6), process="lab"),
            _rows(1, day=date(2026, 9, 6), process="cli"),
        ]
    )
    shards = store.append(frame)

    assert len(shards) == 4
    assert len(store.partitions()) == 4


def test_appending_an_empty_frame_writes_nothing(tmp_path: Path) -> None:
    """An empty batch is a no-op, not an empty shard."""
    store = _store(tmp_path)

    assert store.append(_rows(0)) == []
    assert store.shards() == []


def test_each_append_writes_a_distinct_shard(tmp_path: Path) -> None:
    """Shard names carry host, pid and a sequence number, so concurrent writers never collide."""
    store = _store(tmp_path)

    first = store.append(_rows(1))
    second = store.append(_rows(1))

    assert first[0] != second[0]
    assert store.scan().collect().height == 2
    assert all(str(os.getpid()) in shard.name for shard in store.shards())


def test_shard_names_are_unique_within_a_process() -> None:
    """The sequence number separates two shards named inside the same second."""
    assert shard_name() != shard_name()
    assert shard_name().endswith(SHARD_SUFFIX)


def test_compact_merges_only_settled_shards(tmp_path: Path) -> None:
    """Shards older than the settle window fold into one; fresh ones are left alone."""
    store = _store(tmp_path)
    _ = store.append(_rows(1))
    _ = store.append(_rows(1))

    assert store.compact() == []

    merged = store.compact(settle_s=0.0)

    assert len(merged) == 1
    assert store.shards() == merged
    assert store.scan().collect().height == 2


def test_compact_skips_a_partition_holding_one_shard(tmp_path: Path) -> None:
    """A lone shard is already compact, so nothing is rewritten."""
    store = _store(tmp_path)
    original = store.append(_rows(1))

    assert store.compact(settle_s=0.0) == []
    assert store.shards() == original


def test_write_shard_creates_the_partition_directory(tmp_path: Path) -> None:
    """Staging a shard makes the directory it lands in."""
    body = _rows(1).drop(LOG_PARTITIONS)

    path = write_shard(tmp_path / "date=2026-09-05" / "process=lab", body)

    assert path.is_file()
    assert not list(path.parent.glob("*.tmp"))


def test_coerce_parses_text_timestamps_and_fills_missing_columns() -> None:
    """A loosely typed frame is cast onto the schema, with dates parsed and absent columns nulled."""
    loose = pl.LazyFrame(
        {
            LogColumn.DATE: ["2026-09-05"],
            LogColumn.TIME: ["2026-09-05T12:00:00+00:00"],
            LogColumn.LEVEL: ["INFO"],
            LogColumn.LINE: [7],
        }
    )

    frame = coerce(loose, LOG_SCHEMA).collect()

    assert frame.schema == LOG_SCHEMA
    assert frame.get_column(LogColumn.DATE).to_list() == [date(2026, 9, 5)]
    assert frame.get_column(LogColumn.TIME).to_list() == [datetime(2026, 9, 5, 12, tzinfo=timezone.utc)]
    assert frame.get_column(LogColumn.MESSAGE).to_list() == [None]


def test_coerce_nulls_an_unknown_enum_category() -> None:
    """A level the vocabulary does not carry must not fail the whole batch."""
    loose = pl.LazyFrame({LogColumn.LEVEL: ["NOTICE"]})

    frame = coerce(loose, LOG_SCHEMA).collect()

    assert frame.get_column(LogColumn.LEVEL).to_list() == [None]


def test_a_shard_written_under_an_older_vocabulary_still_scans_and_compacts(tmp_path: Path) -> None:
    """polars will not unify Enum columns whose categories differ, so each shard is typed onto the schema before they meet."""
    store = _store(tmp_path)
    _ = store.append(_rows(1))

    older = _rows(1).drop(LOG_PARTITIONS).drop(LogColumn.EXTRA)
    _ = write_shard(store.partitions()[0], older.cast({LogColumn.LEVEL: pl.Enum(["INFO"])}))

    frame = store.scan().collect()

    assert frame.height == 2
    assert frame.schema == LOG_SCHEMA

    merged = store.compact(settle_s=0.0)

    assert len(merged) == 1
    assert store.scan().collect().schema == LOG_SCHEMA
    assert store.scan().collect().height == 2


def test_shards_of_two_vocabularies_interleaved_scan_and_compact_in_shard_order(tmp_path: Path) -> None:
    """Only consecutive shards of one schema share a scan, so an older vocabulary between them keeps every row typed and in place."""
    store = _store(tmp_path)
    partition = tmp_path / TelemetryDir.LOGS / "date=2026-09-05" / "process=lab"
    partition.mkdir(parents=True)
    current = _rows(1).drop(LOG_PARTITIONS)
    older = current.drop(LogColumn.EXTRA).cast({LogColumn.LEVEL: pl.Enum(["INFO"])})

    for index, body in enumerate([current, current, older, current]):
        body.with_columns(pl.lit(f"shard {index}").alias(LogColumn.MESSAGE)).write_parquet(partition / f"part-{index}{SHARD_SUFFIX}")

    frame = store.scan().collect()
    order = [f"shard {index}" for index in range(4)]

    assert [len(run) for run in schema_runs(store.shards())] == [2, 1, 1]
    assert frame.schema == LOG_SCHEMA
    assert frame.get_column(LogColumn.MESSAGE).to_list() == order

    _ = store.compact(settle_s=0.0)

    assert store.scan().collect().get_column(LogColumn.MESSAGE).to_list() == order


def test_resolve_root_prefers_the_explicit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit root wins over the environment."""
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "from-env"))

    assert resolve_root(tmp_path / "explicit") == tmp_path / "explicit"


def test_resolve_root_falls_back_to_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no explicit root the env var names it."""
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path))

    assert resolve_root() == tmp_path


def test_resolve_root_is_none_when_telemetry_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither argument nor environment means telemetry is not configured at all."""
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)

    assert resolve_root() is None


def test_require_root_returns_the_resolved_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reader that needs a root gets the same answer resolve_root gives."""
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path))

    assert require_root() == tmp_path
    assert require_root(tmp_path / "explicit") == tmp_path / "explicit"


def test_require_root_names_both_ways_to_provide_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a root the failure tells the user about the flag and the variable."""
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)

    with pytest.raises(TelemetryRootMissing, match=f"--telemetry.*{TELEMETRY_ENV}"):
        _ = require_root()


def test_telemetry_exposes_one_cached_store_per_area(tmp_path: Path) -> None:
    """Each table gets its own store under its own directory, built once."""
    telemetry = Telemetry(tmp_path)

    stores = [telemetry.logs, telemetry.runs, telemetry.artifacts, telemetry.sweeps, telemetry.sweep_events]

    assert telemetry.logs is stores[0]
    assert [store.root.name for store in stores] == [TelemetryDir.LOGS, TelemetryDir.RUNS, TelemetryDir.ARTIFACTS, TelemetryDir.SWEEPS, TelemetryDir.SWEEP_EVENTS]
    assert telemetry.live == tmp_path / TelemetryDir.LIVE
    assert telemetry.logs.hive_schema.names() == list(LOG_PARTITIONS)
