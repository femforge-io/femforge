"""Tests for telemetry.sink: the row it builds, the journal-then-parquet lifecycle, setup_logging integration and LogQuery."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import TYPE_CHECKING, cast

import polars as pl
import pytest
from rich.console import Console
from rich.tree import Tree

from femforge.common.log import RELAYED_ROUTE, TELEMETRY_ROUTE, LogLevel, RelayedRecord, _reset_for_tests, emit_relayed, log, log_to_stdout_json, setup_logging, telemetry_scope, telemetry_sink
from femforge.telemetry.context import sweep_point
from femforge.telemetry.hive import coerce
from femforge.telemetry.journal import journal_glob
from femforge.telemetry.paths import TELEMETRY_ENV, Telemetry, TelemetryDir
from femforge.telemetry.schemas import LOG_SCHEMA, LogColumn
from femforge.telemetry.sink import FLUSH_EVERY, FLUSH_INTERVAL_S, LogQuery, ParquetLogSink, default_process, sanitize_process, scan_logs


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from femforge.telemetry.hive import HiveStore


@pytest.fixture
def clean_logging() -> Iterator[None]:
    """Snapshot stdlib root logging and restore it plus common.log module state around each test."""
    prev_handlers = logging.root.handlers[:]
    prev_level = logging.root.level
    _reset_for_tests()
    yield
    _reset_for_tests()
    logging.root.handlers[:] = prev_handlers
    logging.root.setLevel(prev_level)


def _console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, width=300), buf


def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True

        time.sleep(0.01)

    return False


def _installed(root: Path) -> ParquetLogSink:
    console, _ = _console()
    setup_logging(
        level=LogLevel.INFO,
        console=console,
        telemetry=root,
        process="tests",
    )
    sink = telemetry_sink()
    assert sink is not None

    return sink


def test_sanitize_process_folds_to_the_partition_safe_alphabet() -> None:
    """A process name becomes a directory name, so only lowercase word characters survive."""
    assert sanitize_process("Run Sweep!") == "run-sweep"
    assert sanitize_process("solve_2d.v1") == "solve_2d.v1"
    assert sanitize_process("///") == "python"


def test_default_process_is_the_sanitized_argv_stem() -> None:
    """Records partition under the script that emitted them."""
    assert default_process(["/tools/Run Sweep.py"]) == "run-sweep"
    assert default_process([]) == "python"


@pytest.mark.usefixtures("clean_logging")
def test_a_record_reaches_the_journal_before_the_flush_and_parquet_after(tmp_path: Path) -> None:
    """The journal is the live copy and parquet the settled one; a record is in exactly one of them."""
    sink = _installed(tmp_path)
    log.info("first record")

    assert "first record" in sink.journal.path.read_text(encoding="utf-8")
    assert sink.store.shards() == []

    sink.flush()

    assert sink.journal.path.stat().st_size == 0
    assert sink.store.scan().collect().get_column(LogColumn.MESSAGE).to_list() == ["first record"]

    settled = sink.store.shards()
    sink.flush()

    assert sink.store.shards() == settled


@pytest.mark.usefixtures("clean_logging")
def test_the_row_carries_every_log_column(tmp_path: Path) -> None:
    """Location, level, host and thread all land as columns a query can filter on."""
    sink = _installed(tmp_path)
    log.info("shaped")
    sink.flush()

    row = sink.store.scan().collect().row(0, named=True)

    assert row[LogColumn.LEVEL] == LogLevel.INFO
    assert row[LogColumn.LEVEL_NO] == 20
    assert row[LogColumn.PROCESS] == "tests"
    assert row[LogColumn.FUNCTION] == "test_the_row_carries_every_log_column"
    assert row[LogColumn.THREAD] == "MainThread"
    assert row[LogColumn.EXTRA] is None


@pytest.mark.usefixtures("clean_logging")
def test_debug_records_reach_parquet_even_when_the_terminal_is_quiet(tmp_path: Path) -> None:
    """The store is the searchable record, so it is fed at DEBUG regardless of the terminal level."""
    console, buf = _console()
    setup_logging(level=LogLevel.INFO, console=console, telemetry=tmp_path)
    sink = telemetry_sink()
    assert sink is not None

    log.debug("quiet detail")
    sink.flush()

    assert "quiet detail" not in buf.getvalue()
    assert sink.store.scan().collect().get_column(LogColumn.MESSAGE).to_list() == ["quiet detail"]


@pytest.mark.usefixtures("clean_logging")
def test_bound_ids_become_columns_and_the_rest_becomes_extra(tmp_path: Path) -> None:
    """run_id, sweep_id and point_key get columns of their own; anything else is JSON in extra."""
    sink = _installed(tmp_path)

    with log.contextualize(run_id="brave-otter-1234"):
        log.bind(force_y=-8.7).info("bound")

    sink.flush()
    row = sink.store.scan().collect().row(0, named=True)
    extra = cast("dict[str, object]", json.loads(cast("str", row[LogColumn.EXTRA])))

    assert row[LogColumn.RUN_ID] == "brave-otter-1234"
    assert extra == {"force_y": -8.7}
    assert "run_id" not in extra


@pytest.mark.usefixtures("clean_logging")
def test_a_sweep_point_binds_both_ids_onto_the_record(tmp_path: Path) -> None:
    """Records emitted inside a sweep point are attributable to it without the caller binding anything."""
    sink = _installed(tmp_path)

    with sweep_point("sweep-a", "point-1"):
        log.info("solving")

    sink.flush()
    row = sink.store.scan().collect().row(0, named=True)

    assert row[LogColumn.SWEEP_ID] == "sweep-a"
    assert row[LogColumn.POINT_KEY] == "point-1"


@pytest.mark.usefixtures("clean_logging")
def test_an_exception_is_stored_as_its_traceback(tmp_path: Path) -> None:
    """A failure keeps its traceback in the store, so the GUI needs no run.log to explain it."""
    sink = _installed(tmp_path)

    try:
        raise ValueError("solver diverged")
    except ValueError:
        log.exception("stage failed")

    sink.flush()
    row = sink.store.scan().collect().row(0, named=True)
    traced = cast("str", row[LogColumn.EXCEPTION])

    assert "ValueError: solver diverged" in traced
    assert row[LogColumn.LEVEL] == LogLevel.ERROR


@pytest.mark.usefixtures("clean_logging")
def test_a_rich_renderable_in_extra_is_stored_as_text(tmp_path: Path) -> None:
    """A bound Rich tree is flattened rather than repr'd, so the store keeps what the terminal showed."""
    sink = _installed(tmp_path)

    tree = Tree("mesh")
    _ = tree.add("coil: 12000 elements")
    log.bind(summary=tree).info("meshed")

    sink.flush()
    row = sink.store.scan().collect().row(0, named=True)
    extra = cast("dict[str, str]", json.loads(cast("str", row[LogColumn.EXTRA])))

    assert "mesh" in extra["summary"]
    assert "coil: 12000 elements" in extra["summary"]


@pytest.mark.usefixtures("clean_logging")
def test_an_unrenderable_value_in_extra_falls_back_to_a_repr(tmp_path: Path) -> None:
    """Binding an arbitrary object must never fail the log call."""
    sink = _installed(tmp_path)
    log.bind(path=tmp_path).info("bound a path")

    sink.flush()
    row = sink.store.scan().collect().row(0, named=True)
    extra = cast("dict[str, str]", json.loads(cast("str", row[LogColumn.EXTRA])))

    assert extra["path"] == repr(tmp_path)


def _attached(root: Path, *, process: str, flush_every: int = FLUSH_EVERY, flush_interval_s: float = FLUSH_INTERVAL_S) -> ParquetLogSink:
    """A sink built directly (not through setup_logging) and added to loguru at DEBUG."""
    console, _ = _console()
    setup_logging(level=LogLevel.INFO, console=console)

    sink = ParquetLogSink(
        Telemetry(root),
        process=process,
        flush_every=flush_every,
        flush_interval_s=flush_interval_s,
    )
    _ = log.add(
        sink,
        format="{message}",
        colorize=False,
        level=LogLevel.DEBUG,
    )

    return sink


@pytest.mark.usefixtures("clean_logging")
def test_the_batch_size_triggers_a_flush(tmp_path: Path) -> None:
    """A busy process settles records without waiting for the timer."""
    sink = _attached(tmp_path, process="batch", flush_every=3)
    try:
        for index in range(3):
            log.info("record {}", index)

        assert sink.store.scan().collect().height == 3
    finally:
        sink.close()


@pytest.mark.usefixtures("clean_logging")
def test_the_timer_flushes_without_a_full_batch(tmp_path: Path) -> None:
    """A quiet process still settles its records, on the flush interval."""
    sink = _attached(tmp_path, process="timed", flush_every=10_000, flush_interval_s=0.05)
    try:
        log.info("waiting for the timer")

        assert _wait_for(lambda: sink.store.scan().collect().height == 1)
    finally:
        sink.close()


@pytest.mark.usefixtures("clean_logging")
def test_close_settles_the_last_records_and_removes_the_journal(tmp_path: Path) -> None:
    """Closing is the last checkpoint, so nothing buffered is lost and no orphan is left behind."""
    sink = _installed(tmp_path)
    log.info("final")

    sink.close()

    assert not sink.journal.path.exists()
    assert sink.store.scan().collect().get_column(LogColumn.MESSAGE).to_list() == ["final"]


@pytest.mark.usefixtures("clean_logging")
def test_close_is_idempotent_and_a_closed_sink_drops_records(tmp_path: Path) -> None:
    """atexit and an explicit close can both run, and a record after either is discarded rather than raising."""
    sink = _installed(tmp_path)
    sink.close()
    sink.close()

    log.info("after close")

    assert sink.store.scan().collect().height == 0


@pytest.mark.usefixtures("clean_logging")
def test_a_record_emitted_during_a_flush_does_not_re_enter_the_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A library logging from inside the parquet write would otherwise recurse into the sink that provoked it."""
    sink = _installed(tmp_path)
    original = sink.journal.checkpoint

    def checkpoint_and_log(store: HiveStore) -> list[Path]:
        log.info("written by the writer")
        return original(store)

    monkeypatch.setattr(sink.journal, "checkpoint", checkpoint_and_log)
    log.info("real record")
    sink.flush()

    assert sink.store.scan().collect().get_column(LogColumn.MESSAGE).to_list() == ["real record"]


@pytest.mark.usefixtures("clean_logging")
def test_an_orphaned_journal_is_recovered_when_a_sink_starts(tmp_path: Path) -> None:
    """A crashed process leaves records in its journal; the next sink on that root folds them into parquet."""
    telemetry = Telemetry(tmp_path)
    live = telemetry.live
    live.mkdir(parents=True)
    orphan = live / f"{TelemetryDir.LOGS}-box-999999.jsonl"
    orphan.write_text(json.dumps({LogColumn.MESSAGE: "from the crash", LogColumn.DATE: "2026-09-05", LogColumn.PROCESS: "gone"}) + "\n", encoding="utf-8")

    sink = ParquetLogSink(telemetry, process="fresh")
    try:
        assert not orphan.exists()
        assert sink.store.scan().collect().get_column(LogColumn.MESSAGE).to_list() == ["from the crash"]
    finally:
        sink.close()


@pytest.mark.usefixtures("clean_logging")
def test_setup_logging_installs_swaps_and_resets_the_telemetry_sink(tmp_path: Path) -> None:
    """One root at a time: a second root closes the first sink, and a reset closes whatever is left."""
    first = _installed(tmp_path / "first")

    assert telemetry_sink() is first

    console, _ = _console()
    setup_logging(level=LogLevel.INFO, console=console, telemetry=tmp_path / "first")

    assert telemetry_sink() is first

    setup_logging(level=LogLevel.INFO, console=console, telemetry=tmp_path / "second")
    second = telemetry_sink()

    assert second is not None
    assert second is not first
    assert not first.journal.path.exists()

    _reset_for_tests()

    assert telemetry_sink() is None


@pytest.mark.usefixtures("clean_logging")
def test_telemetry_stays_off_without_a_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No argument and no env var means no sink and no directories created."""
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)
    console, _ = _console()
    setup_logging(level=LogLevel.INFO, console=console)
    log.info("terminal only")

    assert telemetry_sink() is None
    assert not (tmp_path / TelemetryDir.LOGS).exists()


@pytest.mark.usefixtures("clean_logging")
def test_the_environment_variable_turns_telemetry_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker inherits the root through the environment without changing its setup call."""
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path))
    console, _ = _console()
    setup_logging(level=LogLevel.INFO, console=console)
    sink = telemetry_sink()

    assert sink is not None
    assert sink.telemetry.root == tmp_path


@pytest.mark.usefixtures("clean_logging")
def test_scan_logs_unions_the_journal_with_the_shards(tmp_path: Path) -> None:
    """A live record and a settled one read back as one frame."""
    sink = _installed(tmp_path)
    log.info("settled")
    sink.flush()
    log.info("still live")

    frame = scan_logs(sink.telemetry).collect()

    assert frame.schema == LOG_SCHEMA
    assert sorted(frame.get_column(LogColumn.MESSAGE).to_list()) == ["settled", "still live"]


@pytest.mark.usefixtures("clean_logging")
def test_log_query_filters_and_pages(tmp_path: Path) -> None:
    """Every named filter narrows the same frame, and page returns an ordered slice."""
    sink = _installed(tmp_path)

    with log.contextualize(run_id="brave-otter-1234"):
        log.info("mesh built")

    with sweep_point("sweep-a", "point-1"):
        log.warning("mesh coarse")

    log.error("solver diverged")
    sink.flush()

    query = LogQuery.over(sink.telemetry)
    now = datetime.now(timezone.utc)

    assert query.page().height == 3
    assert query.levels(LogLevel.ERROR).page().height == 1
    assert query.processes("tests").page().height == 3
    assert query.processes("other").page().height == 0
    assert query.run("brave-otter-1234").page().height == 1
    assert query.sweep("sweep-a").page().height == 1
    assert query.text("mesh").page().height == 2
    assert query.since(now - timedelta(minutes=1)).until(now + timedelta(minutes=1)).page().height == 3
    assert query.since(now + timedelta(minutes=1)).page().height == 0

    page = query.page(offset=1, limit=1)

    assert page.height == 1
    assert page.get_column(LogColumn.MESSAGE).to_list() == ["mesh coarse"]


@pytest.mark.usefixtures("clean_logging")
def test_log_query_tail_keeps_the_newest_records_oldest_first(tmp_path: Path) -> None:
    """A viewer pinned to the end asks for the last records and still reads them in time order."""
    sink = _installed(tmp_path)
    for index in range(5):
        log.info("record {}", index)

    sink.flush()
    query = LogQuery.over(sink.telemetry)

    assert query.tail(2).get_column(LogColumn.MESSAGE).to_list() == ["record 3", "record 4"]
    assert query.tail(10).height == 5
    assert query.levels(LogLevel.ERROR).tail(2).height == 0


def test_log_records_stamped_in_the_same_instant_keep_their_write_order() -> None:
    """A tie on ``time`` falls back to write order, so a burst reads back in the order it was logged."""
    stamp = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    messages = [f"record {index}" for index in range(8)]
    query = LogQuery(coerce(pl.LazyFrame({LogColumn.TIME: [stamp] * len(messages), LogColumn.MESSAGE: messages}), LOG_SCHEMA))

    assert query.page().get_column(LogColumn.MESSAGE).to_list() == messages
    assert query.tail(3).get_column(LogColumn.MESSAGE).to_list() == messages[-3:]


@pytest.mark.usefixtures("clean_logging")
def test_log_query_reads_an_empty_root(tmp_path: Path) -> None:
    """Querying a telemetry root nothing has written yields the typed empty frame."""
    page = LogQuery.over(Telemetry(tmp_path)).levels(LogLevel.INFO).page()

    assert page.height == 0
    assert page.schema == LOG_SCHEMA


def test_the_log_journal_glob_matches_only_log_journals() -> None:
    """Recovery of one table's journals must not touch another's."""
    assert journal_glob(TelemetryDir.LOGS) == "logs-*.jsonl"


def test_the_empty_frame_and_a_written_frame_concatenate() -> None:
    """Identical enum categories on both sides keep the union cheap and lossless."""
    empty = pl.LazyFrame(schema=LOG_SCHEMA)

    assert pl.concat([empty, empty]).collect().schema == LOG_SCHEMA


def _messages(root: Path) -> list[str]:
    return scan_logs(Telemetry(root)).collect().get_column(LogColumn.MESSAGE).to_list()


@pytest.mark.usefixtures("clean_logging")
def test_a_telemetry_scope_routes_its_records_to_its_own_root_only(tmp_path: Path) -> None:
    """Inside a scope records reach the scope's root and never the default one; outside, the reverse."""
    default = _installed(tmp_path / "default")

    with telemetry_scope(tmp_path / "own"):
        log.info("inside")

        with telemetry_scope(tmp_path / "own"):
            log.info("nested")

        log.bind(note="still routed").info("after nested")

    log.info("outside")
    default.flush()

    assert _messages(tmp_path / "own") == ["inside", "nested", "after nested"]
    assert _messages(tmp_path / "default") == ["outside"]
    assert "telemetry_root" not in str(scan_logs(Telemetry(tmp_path / "own")).collect().get_column(LogColumn.EXTRA).to_list())


@pytest.mark.usefixtures("clean_logging")
def test_a_scope_on_the_default_root_shares_the_default_sink(tmp_path: Path) -> None:
    default = _installed(tmp_path / "default")

    with telemetry_scope(tmp_path / "default"):
        log.info("same folder")

    default.flush()

    assert _messages(tmp_path / "default") == ["same folder"]


@pytest.mark.usefixtures("clean_logging")
def test_the_default_sink_takes_over_a_root_a_scope_opened(tmp_path: Path) -> None:
    """One sink per folder: moving the default onto a scoped root closes the scope's sink rather than writing the folder twice."""
    console, _ = _console()

    with telemetry_scope(tmp_path / "shared"):
        log.info("scoped")
        setup_logging(level=LogLevel.INFO, console=console, telemetry=tmp_path / "shared", process="tests")
        log.info("handed over")

    sink = telemetry_sink()
    assert sink is not None
    sink.flush()

    assert _messages(tmp_path / "shared") == ["scoped", "handed over"]


def test_a_scope_without_a_root_changes_nothing() -> None:
    with telemetry_scope(None):
        pass


@pytest.mark.usefixtures("clean_logging")
def test_a_reset_inside_a_scope_closes_the_scope_sink(tmp_path: Path) -> None:
    with telemetry_scope(tmp_path / "own"):
        log.info("before reset")
        _reset_for_tests()

    assert _messages(tmp_path / "own") == ["before reset"]


@pytest.mark.usefixtures("clean_logging")
def test_concurrent_scopes_on_one_root_share_one_sink_for_the_process(tmp_path: Path) -> None:
    """Overlapping scopes on one root write each record once through one sink, which outlives the scopes."""
    import threading

    from femforge.common.log import _scoped_telemetry

    def evaluate(worker: int) -> None:
        for round_ in range(4):
            with telemetry_scope(tmp_path / "shared"):
                for index in range(10):
                    log.bind(worker=worker, round=round_, index=index).info("record")

    _ = _installed(tmp_path / "default")
    workers = [threading.Thread(target=evaluate, args=(worker,)) for worker in range(8)]
    for worker in workers:
        worker.start()

    for worker in workers:
        worker.join()

    assert len(_scoped_telemetry) == 1
    assert len(_messages(tmp_path / "shared")) == 320


@pytest.mark.usefixtures("clean_logging")
def test_scoped_sinks_partition_under_the_setup_process_name(tmp_path: Path) -> None:
    console, _ = _console()
    setup_logging(level=LogLevel.INFO, console=console, process="lab-proc")

    with telemetry_scope(tmp_path / "own"):
        log.info("named")

    # A later name reaches the sinks already open.
    setup_logging(level=LogLevel.INFO, console=console, process="renamed")

    with telemetry_scope(tmp_path / "own"):
        log.info("renamed")

    rows = scan_logs(Telemetry(tmp_path / "own")).collect().sort(LogColumn.TIME)
    assert rows.get_column(LogColumn.PROCESS).to_list() == ["lab-proc", "renamed"]


@pytest.mark.usefixtures("clean_logging")
def test_run_log_lines_leave_out_the_telemetry_route(tmp_path: Path) -> None:
    from femforge.common.log import run_log_context

    with telemetry_scope(tmp_path / "own"), run_log_context("run-1", tmp_path):
        log.bind(width=3).info("inside the run")

        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log.exception("failed")

    text = (tmp_path / "run.log").read_text(encoding="utf-8")
    inside = next(line for line in text.splitlines() if "inside the run" in line)
    assert "'width': 3" in inside and "'run_id': 'run-1'" in inside
    assert "telemetry_root" not in text
    assert "RuntimeError: boom" in text


@pytest.mark.usefixtures("clean_logging")
def test_a_relayed_record_reaches_neither_the_default_nor_a_scoped_sink(tmp_path: Path) -> None:
    """A record a child already stored is relayed to the terminal only, inside a scope or outside it."""
    default = _installed(tmp_path / "default")
    child: RelayedRecord = {"level": "INFO", "message": "from the child", "name": "main", "function": "solve", "line": 1, "extra": {}}

    with telemetry_scope(tmp_path / "own"):
        log.info("scoped")
        emit_relayed(child, **{TELEMETRY_ROUTE: RELAYED_ROUTE})

    emit_relayed(child, **{TELEMETRY_ROUTE: RELAYED_ROUTE})
    log.info("outside")
    default.flush()

    assert _messages(tmp_path / "own") == ["scoped"]
    assert _messages(tmp_path / "default") == ["outside"]


@pytest.mark.usefixtures("clean_logging")
def test_a_json_child_stores_its_records_under_the_environment_root_as_its_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path))

    log_to_stdout_json(process="Solve 3D")
    log.info("from the child")

    sink = telemetry_sink()
    assert sink is not None and sink.process == sanitize_process("Solve 3D")
    sink.flush()
    assert _messages(tmp_path) == ["from the child"]
    assert json.loads(capsys.readouterr().out.splitlines()[0])["message"] == "from the child"
