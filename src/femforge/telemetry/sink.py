"""The loguru sink that lands every record in parquet, plus the query surface over what it wrote."""

from __future__ import annotations

import atexit
import json
import re
import sys
import threading
from datetime import timezone
from io import StringIO
from pathlib import Path
from traceback import format_exception
from typing import TYPE_CHECKING, Self, cast

import polars as pl
from rich.console import Console

from femforge.common.log import TELEMETRY_ROUTE
from femforge.common.rich_sink import ContextFormatter
from femforge.telemetry.hive import HOST
from femforge.telemetry.journal import Journal, journal_glob, recover_journals, scan_journals
from femforge.telemetry.paths import Telemetry, TelemetryDir
from femforge.telemetry.schemas import LOG_SCHEMA, LogColumn


if TYPE_CHECKING:
    from datetime import datetime

    from loguru import Message, Record
    from rich.console import RenderableType

    from femforge.common.log import LogLevel


__all__ = ["LogQuery", "ParquetLogSink", "default_process", "log_row", "sanitize_process", "scan_logs"]

CONTEXT_COLUMNS = frozenset({LogColumn.RUN_ID, LogColumn.SWEEP_ID, LogColumn.POINT_KEY})
"""Bound keys promoted out of ``extra`` into columns of their own, so a query can push them down."""

EXTRA_WIDTH = 120
"""The width Rich renderables found in ``extra`` are rendered at before being stored as text."""

FLUSH_EVERY = 500
"""How many records a sink takes before it checkpoints its journal into parquet."""

FLUSH_INTERVAL_S = 5.0
"""`t` `[s]`: How often a sink checkpoints whatever records are pending."""

_UNSAFE_PROCESS = re.compile(r"[^a-z0-9_.-]+")


def sanitize_process(raw: str) -> str:
    """Fold a process name down to ``[a-z0-9_.-]`` so it is safe as a hive partition directory name."""
    folded = _UNSAFE_PROCESS.sub("-", raw.lower()).strip("-")

    return folded or "python"


def default_process(argv: list[str] | None = None) -> str:
    """The sanitized stem of ``sys.argv[0]``: the name the process's records are partitioned under."""
    args = sys.argv if argv is None else argv
    stem = Path(args[0]).stem if args and args[0] else ""

    return sanitize_process(stem)


def log_row(record: Record, *, process: str, console: Console) -> dict[str, object]:
    """One loguru record flattened onto the log schema, with Rich renderables in ``extra`` rendered down to text.

    Args:
        record: The loguru record.
        process: The process name the row is partitioned under.
        console: The console Rich renderables in ``extra`` are rendered on.
    """

    def encode(value: object) -> object:
        if ContextFormatter.is_renderable(value):
            return ContextFormatter.flatten(cast("RenderableType", value), console, EXTRA_WIDTH).plain

        return repr(value)

    when = record["time"].astimezone(timezone.utc)
    bound = cast("dict[str, object]", record["extra"])
    rest = {key: value for key, value in bound.items() if key not in CONTEXT_COLUMNS and key != TELEMETRY_ROUTE}

    failure = record["exception"]
    traced = "".join(format_exception(failure.type, failure.value, failure.traceback)) if failure is not None and failure.type is not None else None

    return {
        LogColumn.DATE: when.date(),
        LogColumn.PROCESS: process,
        LogColumn.TIME: when,
        LogColumn.LEVEL: record["level"].name,
        LogColumn.LEVEL_NO: record["level"].no,
        LogColumn.NAME: record["name"],
        LogColumn.FUNCTION: record["function"],
        LogColumn.LINE: record["line"],
        LogColumn.MESSAGE: record["message"],
        LogColumn.RUN_ID: bound.get(LogColumn.RUN_ID),
        LogColumn.SWEEP_ID: bound.get(LogColumn.SWEEP_ID),
        LogColumn.POINT_KEY: bound.get(LogColumn.POINT_KEY),
        LogColumn.HOST: HOST,
        LogColumn.PID: record["process"].id,
        LogColumn.THREAD: record["thread"].name,
        LogColumn.EXCEPTION: traced,
        LogColumn.EXTRA: json.dumps(rest, default=encode) if rest else None,
    }


class FlushGuard(threading.local):
    """Per-thread marker for the thread currently inside a flush, so records the flush itself provokes never re-enter the sink."""

    busy: bool = False


class ParquetLogSink:
    """Loguru sink writing every record to a JSONL journal at once and to hive-partitioned parquet in batches."""

    def __init__(self, telemetry: Telemetry, *, process: str | None = None, flush_every: int = FLUSH_EVERY, flush_interval_s: float = FLUSH_INTERVAL_S) -> None:
        """Open this process's journal, recover the ones dead processes left behind, and start the flush timer.

        Args:
            telemetry: The telemetry root the records land under.
            process: The process name the records are partitioned under; None uses the script's name.
            flush_every: How many records trigger a checkpoint into parquet.
            flush_interval_s: Seconds between timed checkpoints.
        """
        self.telemetry: Telemetry = telemetry
        self.process: str = sanitize_process(process) if process is not None else default_process()
        self.flush_every: int = flush_every
        self.flush_interval_s: float = flush_interval_s

        self.store = telemetry.logs
        self.journal: Journal = Journal(telemetry.live, TelemetryDir.LOGS)

        self._console: Console = Console(file=StringIO(), width=EXTRA_WIDTH)
        self._guard: FlushGuard = FlushGuard()
        self._lock: threading.RLock = threading.RLock()
        self._pending: int = 0
        self._closed: bool = False

        _ = recover_journals(telemetry.live, self.store, journal_glob(TelemetryDir.LOGS))

        self._stop: threading.Event = threading.Event()
        self._timer: threading.Thread = threading.Thread(target=self._tick, name="femforge-telemetry-flush", daemon=True)
        self._timer.start()
        _ = atexit.register(self.close)

    def _tick(self) -> None:
        while not self._stop.wait(self.flush_interval_s):
            self.flush()

    def __call__(self, message: Message) -> None:
        """The loguru sink protocol; add this callable with ``format="{message}"`` at DEBUG."""
        if self._guard.busy:
            return

        row = log_row(message.record, process=self.process, console=self._console)

        with self._lock:
            if self._closed:
                return

            self.journal.write(row)
            self._pending += 1
            due = self._pending >= self.flush_every

        if due:
            self.flush()

    def flush(self) -> None:
        """Checkpoint the journal's records into the parquet store; a no-op when nothing is pending."""
        with self._lock:
            if self._pending == 0:
                return

            self._guard.busy = True
            try:
                _ = self.journal.checkpoint(self.store)
                self._pending = 0
            finally:
                self._guard.busy = False

    def close(self) -> None:
        """Stop the timer, checkpoint whatever is left and drop the journal; idempotent."""
        with self._lock:
            if self._closed:
                return

            self._closed = True

        self._stop.set()
        self._timer.join(timeout=self.flush_interval_s)
        _ = self.journal.checkpoint(self.store)
        self.journal.close()
        _ = atexit.unregister(self.close)


def scan_logs(telemetry: Telemetry) -> pl.LazyFrame:
    """Every log record: the settled parquet shards unioned with what is still only in the live journals."""
    live = scan_journals(telemetry.live, journal_glob(TelemetryDir.LOGS), LOG_SCHEMA)

    return pl.concat([telemetry.logs.scan(), live])


class LogQuery:
    """A fluent filter over the log frame; each method returns a new query and nothing runs until ``page``."""

    def __init__(self, frame: pl.LazyFrame) -> None:
        """Query ``frame``, a lazy frame on the log schema."""
        self.frame: pl.LazyFrame = frame

    @classmethod
    def over(cls, telemetry: Telemetry) -> Self:
        """A query spanning one telemetry root's parquet shards and live journals."""
        return cls(scan_logs(telemetry))

    def where(self, predicate: pl.Expr) -> Self:
        """A new query narrowed by ``predicate``; the other methods are named shorthands for this."""
        return type(self)(self.frame.filter(predicate))

    def levels(self, *levels: LogLevel | str) -> Self:
        """Records at any of ``levels``."""
        return self.where(pl.col(LogColumn.LEVEL).is_in([str(level) for level in levels]))

    def processes(self, *names: str) -> Self:
        """Records written by any of the processes ``names``."""
        return self.where(pl.col(LogColumn.PROCESS).is_in(list(names)))

    def run(self, run_id: str) -> Self:
        """Records bound to the run ``run_id``."""
        return self.where(pl.col(LogColumn.RUN_ID) == run_id)

    def sweep(self, sweep_id: str) -> Self:
        """Records bound to the sweep ``sweep_id``."""
        return self.where(pl.col(LogColumn.SWEEP_ID) == sweep_id)

    def since(self, when: datetime) -> Self:
        """Records logged at or after ``when``."""
        return self.where(pl.col(LogColumn.TIME) >= when)

    def until(self, when: datetime) -> Self:
        """Records logged at or before ``when``."""
        return self.where(pl.col(LogColumn.TIME) <= when)

    def text(self, needle: str) -> Self:
        """Records whose message contains ``needle``, case-sensitively."""
        return self.where(pl.col(LogColumn.MESSAGE).str.contains(needle, literal=True))

    def page(self, offset: int = 0, limit: int = 200) -> pl.DataFrame:
        """Collect one slice of the result, oldest record first."""
        return self.frame.sort(LogColumn.TIME, maintain_order=True).slice(offset, limit).collect()

    def tail(self, limit: int = 200) -> pl.DataFrame:
        """Collect the newest ``limit`` records, oldest of them first."""
        return self.frame.sort(LogColumn.TIME, maintain_order=True).tail(limit).collect()
