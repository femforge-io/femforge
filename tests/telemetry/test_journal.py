"""Tests for telemetry.journal: write-ahead records, checkpointing into parquet, and recovery of dead processes' journals."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, cast

import pytest

from femforge.common.log import log
from femforge.telemetry.hive import HOST, HiveStore
from femforge.telemetry.journal import JOURNAL_SUFFIX, Journal, journal_glob, journal_lines, json_default, recover_journals, scan_journals
from femforge.telemetry.paths import Telemetry, TelemetryDir
from femforge.telemetry.schemas import LOG_SCHEMA, LogColumn


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _row(message: str) -> dict[str, object]:
    return {
        LogColumn.DATE: date(2026, 9, 5),
        LogColumn.PROCESS: "lab",
        LogColumn.TIME: datetime(2026, 9, 5, 12, tzinfo=timezone.utc),
        LogColumn.LEVEL: "INFO",
        LogColumn.LEVEL_NO: 20,
        LogColumn.MESSAGE: message,
    }


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[Journal]:
    """A journal on a temporary live directory, closed however the test ends."""
    opened = Journal(tmp_path / TelemetryDir.LIVE, TelemetryDir.LOGS)
    yield opened
    opened.close()


def test_a_written_record_is_readable_immediately(journal: Journal) -> None:
    """Each record is flushed as it is written, so a tailing reader never waits for a batch."""
    journal.write(_row("first"))

    lines = journal.path.read_text(encoding="utf-8").splitlines()
    parsed = cast("dict[str, object]", json.loads(lines[0]))

    assert len(lines) == 1
    assert parsed[LogColumn.MESSAGE] == "first"
    assert parsed[LogColumn.TIME] == "2026-09-05T12:00:00+00:00"


def test_the_journal_name_carries_the_table_host_and_pid(journal: Journal) -> None:
    """The filename identifies the writer, so another process can tell live journals from orphans."""
    assert journal.path.name == f"{TelemetryDir.LOGS}-{HOST}-{os.getpid()}{JOURNAL_SUFFIX}"
    assert journal.path.match(journal_glob(TelemetryDir.LOGS))


def test_checkpoint_moves_records_into_parquet_and_empties_the_journal(tmp_path: Path, journal: Journal) -> None:
    """The journal is a write-ahead buffer: once its records are shards, it holds nothing."""
    store = Telemetry(tmp_path).logs
    journal.write(_row("first"))
    journal.write(_row("second"))

    shards = journal.checkpoint(store)

    assert len(shards) == 1
    assert journal.path.stat().st_size == 0
    assert store.scan().collect().get_column(LogColumn.MESSAGE).to_list() == ["first", "second"]


def test_checkpointing_an_empty_journal_writes_nothing(tmp_path: Path, journal: Journal) -> None:
    """Nothing buffered means no shard and no empty parquet file."""
    store = Telemetry(tmp_path).logs

    assert journal.checkpoint(store) == []
    assert store.shards() == []


def test_writing_after_close_is_ignored(tmp_path: Path, journal: Journal) -> None:
    """A closed journal drops records instead of raising inside a logging call."""
    store = Telemetry(tmp_path).logs
    journal.close()
    journal.write(_row("late"))

    assert not journal.path.exists()
    assert journal.checkpoint(store) == []


def test_close_is_idempotent(journal: Journal) -> None:
    """Closing twice is safe, so atexit and an explicit close can both run."""
    journal.close()
    journal.close()

    assert not journal.path.exists()


def test_scan_journals_is_typed_and_empty_without_records(tmp_path: Path) -> None:
    """An absent live directory and an empty journal both read as a typed empty frame."""
    live = tmp_path / TelemetryDir.LIVE

    assert scan_journals(live, journal_glob(TelemetryDir.LOGS), LOG_SCHEMA).collect().schema == LOG_SCHEMA

    live.mkdir()
    (live / f"{TelemetryDir.LOGS}-box-1.jsonl").write_text("", encoding="utf-8")

    assert scan_journals(live, journal_glob(TelemetryDir.LOGS), LOG_SCHEMA).collect().height == 0


def test_scan_journals_coerces_records_onto_the_schema(tmp_path: Path, journal: Journal) -> None:
    """JSON text comes back with the store's dtypes, so live and settled rows concatenate."""
    journal.write(_row("live"))

    frame = scan_journals(journal.path.parent, journal_glob(TelemetryDir.LOGS), LOG_SCHEMA).collect()

    assert frame.schema == LOG_SCHEMA
    assert frame.get_column(LogColumn.MESSAGE).to_list() == ["live"]
    assert frame.get_column(LogColumn.TIME).to_list() == [datetime(2026, 9, 5, 12, tzinfo=timezone.utc)]


def test_recover_journals_folds_in_dead_processes_and_leaves_live_ones(tmp_path: Path, journal: Journal) -> None:
    """An orphaned journal's records reach parquet; a running process keeps its own."""
    store = Telemetry(tmp_path).logs
    journal.write(_row("mine"))

    orphan = journal.path.parent / f"{TelemetryDir.LOGS}-box-999999.jsonl"
    orphan.write_text(json.dumps(_row("orphan"), default=json_default) + "\n", encoding="utf-8")

    recovered = recover_journals(
        journal.path.parent,
        store,
        journal_glob(TelemetryDir.LOGS),
        alive=lambda pid: pid == os.getpid(),
    )

    assert recovered == [orphan]
    assert not orphan.exists()
    assert journal.path.stat().st_size > 0
    assert store.scan().collect().get_column(LogColumn.MESSAGE).to_list() == ["orphan"]


def test_recover_journals_skips_files_that_are_not_journals(tmp_path: Path) -> None:
    """A file whose name carries no pid is left where it is."""
    store = Telemetry(tmp_path).logs
    live = tmp_path / TelemetryDir.LIVE
    live.mkdir()
    stray = live / f"{TelemetryDir.LOGS}-notes.jsonl"
    stray.write_text("{}\n", encoding="utf-8")

    assert recover_journals(live, store, journal_glob(TelemetryDir.LOGS)) == []
    assert stray.exists()


def test_recover_journals_tolerates_a_missing_live_directory(tmp_path: Path) -> None:
    """A root that has never been written to has nothing to recover."""
    store = HiveStore(tmp_path / "logs", LOG_SCHEMA, [LogColumn.DATE, LogColumn.PROCESS])

    assert recover_journals(tmp_path / "live", store, journal_glob(TelemetryDir.LOGS)) == []


def test_json_default_falls_back_to_a_repr() -> None:
    """A value json cannot render is stored as its repr rather than failing the write."""
    assert json_default(object) == repr(object)


def test_recovering_a_journal_that_opens_with_console_text_keeps_its_records(tmp_path: Path) -> None:
    """Stray console lines ahead of the records (the gmsh output seen in the wild) cost a warning, never the setup."""
    store = Telemetry(tmp_path).logs
    live = tmp_path / TelemetryDir.LIVE
    live.mkdir()
    orphan = live / f"{TelemetryDir.LOGS}-box-999999.jsonl"
    records = [json.dumps(_row(message), default=json_default) for message in ("one", "two")]
    lines = ["Info    : Clearing all models and views...", "Info    : Done clearing all models and views", "", *records, "[1, 2]", '{"torn": ']
    orphan.write_text("\n".join(lines) + "\n", encoding="utf-8")

    warned: list[str] = []
    sink_id = log.add(warned.append, level="WARNING", format="{message} | {extra}")
    try:
        recovered = recover_journals(live, store, journal_glob(TelemetryDir.LOGS), alive=lambda _: False)
    finally:
        log.remove(sink_id)

    assert len(warned) == 1
    assert "not JSON records" in warned[0] and "'skipped': 4" in warned[0]
    assert recovered == [orphan]
    assert store.scan().collect().get_column(LogColumn.MESSAGE).to_list() == ["one", "two"]


def test_journal_lines_reads_a_vanished_journal_as_empty(tmp_path: Path) -> None:
    assert journal_lines(tmp_path / "gone.jsonl") == ([], 0)
