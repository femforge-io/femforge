"""The write-ahead half of the store: one JSONL journal per process, flushed per record and checkpointed into parquet."""

from __future__ import annotations

import io
import json
import os
import threading
from datetime import date, datetime
from typing import TYPE_CHECKING, cast

import polars as pl
import psutil

from femforge.common.log import log
from femforge.telemetry.hive import HOST, coerce


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from femforge.telemetry.hive import HiveStore


__all__ = ["Journal", "journal_glob", "journal_lines", "json_default", "records_frame", "recover_journals", "scan_journals"]

JOURNAL_SUFFIX = ".jsonl"
"""Journals are newline-delimited JSON so a partially written tail costs at most one record."""


def json_default(value: object) -> object:
    """Render what ``json.dumps`` cannot: timestamps as ISO-8601 (the form ``coerce`` parses back), anything else as its repr."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()

    return repr(value)


def journal_glob(name: str) -> str:
    """The glob matching every process's journal for the table ``name``."""
    return f"{name}-*{JOURNAL_SUFFIX}"


def journal_lines(path: Path) -> tuple[list[str], int]:
    """A journal's JSON-record lines, plus how many other non-blank lines it held (stray console output, a torn tail)."""
    try:
        text: str = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return [], 0

    records: list[str] = []
    skipped: int = 0

    for line in text.splitlines():
        if not line.strip():
            continue

        try:
            parsed: object = cast("object", json.loads(line))
        except ValueError:
            parsed = None

        if isinstance(parsed, dict):
            records.append(line)
        else:
            skipped += 1

    return records, skipped


def scan_journals(live_dir: Path, pattern: str, schema: pl.Schema) -> pl.LazyFrame:
    """A frame over every matching journal's records, coerced onto ``schema``; lines that are not JSON records are skipped.

    Args:
        live_dir: The directory holding the journals.
        pattern: The glob selecting the journals to read.
        schema: The table schema the records are coerced onto.
    """
    if not live_dir.is_dir():
        return pl.LazyFrame(schema=schema)

    records: list[str] = [line for path in sorted(live_dir.glob(pattern)) for line in journal_lines(path)[0]]
    return records_frame(records, schema)


def records_frame(records: list[str], schema: pl.Schema) -> pl.LazyFrame:
    """JSON record lines as a frame coerced onto ``schema``; a typed empty frame when there are none."""
    if not records:
        return pl.LazyFrame(schema=schema)

    text: bytes = "\n".join(records).encode("utf-8")
    raw: pl.DataFrame = pl.read_ndjson(io.BytesIO(text), ignore_errors=True)
    return coerce(raw.lazy(), schema)


def recover_journals(live_dir: Path, store: HiveStore, pattern: str, *, alive: Callable[[int], bool] = psutil.pid_exists) -> list[Path]:
    """Checkpoint and delete the journals of processes that are gone, so a crash costs no records; returns the journals recovered.

    Args:
        live_dir: The directory holding the journals.
        store: The table the recovered records are appended to.
        pattern: The glob selecting that table's journals.
        alive: Whether a process id is still running; the journal of a live writer is left alone.
    """
    if not live_dir.is_dir():
        return []

    def journal_pid(path: Path) -> int | None:
        _, _, tail = path.stem.rpartition("-")
        if not tail.isdigit():
            return None

        return int(tail)

    recovered: list[Path] = []
    for path in sorted(live_dir.glob(pattern)):
        pid = journal_pid(path)
        if pid is None or alive(pid):
            continue

        records, skipped = journal_lines(path)

        if skipped:
            log.bind(journal=path.name, skipped=skipped).warning("Recovered journal had lines that were not JSON records.")

        rows = records_frame(records, store.schema).collect()
        _ = store.append(rows)
        path.unlink()
        recovered.append(path)

    return recovered


class Journal:
    """A process's own append-only JSONL journal: every record is written and flushed immediately, then folded into parquet by ``checkpoint``."""

    def __init__(self, live_dir: Path, name: str) -> None:
        """Open this process's journal for the table ``name`` under ``live_dir``, creating the directory when needed."""
        live_dir.mkdir(parents=True, exist_ok=True)

        self.path: Path = live_dir / f"{name}-{HOST}-{os.getpid()}{JOURNAL_SUFFIX}"
        self._lock: threading.Lock = threading.Lock()
        self._handle = self.path.open("a", encoding="utf-8", newline="\n")

    def write(self, row: Mapping[str, object]) -> None:
        """Append one record and flush it, so a reader tailing the journal sees it before the next parquet flush."""
        line = json.dumps(row, default=json_default)

        with self._lock:
            if self._handle.closed:
                return

            _ = self._handle.write(line + "\n")
            self._handle.flush()

    def checkpoint(self, store: HiveStore) -> list[Path]:
        """Append the journal's records to ``store`` and truncate it; returns the shards written."""
        with self._lock:
            if self._handle.closed or self.path.stat().st_size == 0:
                return []

            rows = scan_journals(self.path.parent, self.path.name, store.schema).collect()
            written = store.append(rows)
            _ = self._handle.truncate(0)
            _ = self._handle.seek(0)

            return written

    def close(self) -> None:
        """Close the journal handle and delete the (now empty) file; idempotent."""
        with self._lock:
            if self._handle.closed:
                return

            self._handle.close()

        self.path.unlink(missing_ok=True)
