"""Tests for telemetry.sweep: the launch and closing rows, journalled point transitions from several processes, and the progress summary."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast

import polars as pl

from femforge.telemetry.journal import JOURNAL_SUFFIX, journal_glob, json_default, recover_journals
from femforge.telemetry.paths import Telemetry, TelemetryDir
from femforge.telemetry.schemas import SweepColumn, SweepEventColumn, SweepState
from femforge.telemetry.sweep import SweepJournal, SweepQuery, sweep_runs


if TYPE_CHECKING:
    from pathlib import Path


DEAD_PID = 999999
"""A pid Windows can never allocate (pids are multiples of four), so its journal always reads as an orphan."""


def _worker_journal(live: Path, sweep_id: str, rows: list[tuple[str, SweepState, datetime]], *, error: str | None = None) -> Path:
    """The journal a second process leaves behind, written by hand so its event times are exact."""
    live.mkdir(parents=True, exist_ok=True)
    path: Path = live / f"{TelemetryDir.SWEEP_EVENTS}-box-{DEAD_PID}{JOURNAL_SUFFIX}"

    lines: list[str] = []
    for point_key, state, at in rows:
        record: dict[str, object] = {
            SweepEventColumn.SWEEP_ID: sweep_id,
            SweepEventColumn.POINT_KEY: point_key,
            SweepEventColumn.POINT_NAME: point_key.replace("-", " "),
            SweepEventColumn.STATE: state.value,
            SweepEventColumn.AT: at,
            SweepEventColumn.HOST: "box",
            SweepEventColumn.PID: DEAD_PID,
            SweepEventColumn.ORIGIN: "solved",
            SweepEventColumn.ERROR: error if state is SweepState.FAILED else None,
            SweepEventColumn.DURATION_S: 12.5,
        }
        lines.append(json.dumps(record, default=json_default))

    _ = path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return path


def _started(telemetry: Telemetry, sweep_id: str, *, total: int = 4) -> SweepJournal:
    journal = SweepJournal(telemetry, sweep_id=sweep_id, process="run_sweep")
    journal.start(
        "thermal limit sensitivity",
        total=total,
        skipped=1,
        max_workers=3,
    )

    return journal


def test_a_sweep_is_open_until_it_closes(tmp_path: Path) -> None:
    """The launch row appears at once so a reader sees the sweep while it runs, and the closing row supersedes it."""
    telemetry = Telemetry(tmp_path)
    journal = _started(telemetry, "sweep-a")

    open_row = cast("dict[str, object]", sweep_runs(telemetry).rows(named=True)[0])

    assert open_row[SweepColumn.DESCRIPTION] == "thermal limit sensitivity"
    assert open_row[SweepColumn.TOTAL] == 4
    assert open_row[SweepColumn.SKIPPED] == 1
    assert open_row[SweepColumn.MAX_WORKERS] == 3
    assert open_row[SweepColumn.PROCESS] == "run_sweep"
    assert open_row[SweepColumn.FINISHED_AT] is None

    journal.finish()
    closed = sweep_runs(telemetry)

    assert closed.height == 1
    assert closed.get_column(SweepColumn.FINISHED_AT).to_list()[0] is not None


def test_sweeps_list_newest_first(tmp_path: Path) -> None:
    """The listing collapses each sweep to its latest row and orders by launch."""
    telemetry = Telemetry(tmp_path)
    _started(telemetry, "sweep-a").finish()
    _started(telemetry, "sweep-b").finish()

    assert sweep_runs(telemetry).get_column(SweepColumn.SWEEP_ID).to_list() == ["sweep-b", "sweep-a"]


def test_events_from_two_processes_read_as_one_progress_view(tmp_path: Path) -> None:
    """The parent and each worker own a journal; a reader unions them and keeps the last state per point."""
    telemetry = Telemetry(tmp_path)
    journal = _started(telemetry, "sweep-a")
    now = datetime.now(timezone.utc)

    journal.event("p1", SweepState.QUEUED)
    journal.event("p2", SweepState.QUEUED)
    _ = _worker_journal(telemetry.live, "sweep-a", [("p1", SweepState.RUNNING, now), ("p1", SweepState.DONE, now + timedelta(seconds=1))])

    query = SweepQuery(telemetry, "sweep-a")
    latest = query.latest().sort(SweepEventColumn.POINT_KEY)

    assert query.events().height == 4
    assert latest.get_column(SweepEventColumn.POINT_KEY).to_list() == ["p1", "p2"]
    assert latest.get_column(SweepEventColumn.STATE).to_list() == [SweepState.DONE, SweepState.QUEUED]

    journal.finish()


def test_transitions_stamped_in_the_same_instant_keep_their_journal_order(tmp_path: Path) -> None:
    """A tie on ``at`` falls back to write order, so a point's last settled state is its state."""
    telemetry = Telemetry(tmp_path)
    journal = _started(telemetry, "sweep-a")
    now = datetime.now(timezone.utc)
    states = [*(SweepState.QUEUED, SweepState.RUNNING, SweepState.FAILED) * 5, SweepState.DONE]

    for rows in ([("p0", SweepState.DONE, now - timedelta(minutes=1))], [("p1", state, now) for state in states]):
        _ = _worker_journal(telemetry.live, "sweep-a", rows)
        _ = recover_journals(telemetry.live, telemetry.sweep_events, journal_glob(TelemetryDir.SWEEP_EVENTS))

    query = SweepQuery(telemetry, "sweep-a")

    assert query.events().get_column(SweepEventColumn.STATE).to_list() == [SweepState.DONE, *states]
    assert query.latest().get_column(SweepEventColumn.STATE).to_list() == [SweepState.DONE, SweepState.DONE]

    journal.finish()


def test_sweep_rows_indexed_in_the_same_instant_collapse_to_the_one_written_last(tmp_path: Path) -> None:
    """Write order breaks a tie on ``indexed_at``, so the closing row still wins the listing and the launch lookup."""
    telemetry = Telemetry(tmp_path)
    _ = _started(telemetry, "sweep-a")
    launch = telemetry.sweeps.scan().collect()
    stamp = datetime.now(timezone.utc) + timedelta(minutes=1)
    closed = stamp + timedelta(hours=1)

    rewritten = [launch.with_columns(pl.lit(finished, pl.Datetime("us", "UTC")).alias(SweepColumn.FINISHED_AT)) for finished in (None, None, closed)]
    _ = telemetry.sweeps.append(pl.concat(rewritten).with_columns(pl.lit(stamp).alias(SweepColumn.INDEXED_AT)))

    assert sweep_runs(telemetry).get_column(SweepColumn.FINISHED_AT).to_list() == [closed]
    assert SweepQuery(telemetry, "sweep-a").launch().get_column(SweepColumn.FINISHED_AT).to_list() == [closed]


def test_another_sweeps_events_are_not_mixed_in(tmp_path: Path) -> None:
    """Events are partitioned by sweep, and the query filters the live journals the same way."""
    telemetry = Telemetry(tmp_path)
    journal = _started(telemetry, "sweep-a")
    journal.event("p1", SweepState.DONE)
    journal.sweep_id = "sweep-b"
    journal.event("p9", SweepState.DONE)

    assert SweepQuery(telemetry, "sweep-a").latest().get_column(SweepEventColumn.POINT_KEY).to_list() == ["p1"]

    journal.finish()


def test_a_failed_point_keeps_its_traceback(tmp_path: Path) -> None:
    """A failure is a durable record, not one ERROR line: the whole traceback rides on the event."""
    telemetry = Telemetry(tmp_path)
    journal = _started(telemetry, "sweep-a")
    trace = "Traceback (most recent call last):\n  RuntimeError: gmsh died"

    journal.event(
        "p3",
        SweepState.FAILED,
        point_name="stall at 3 A",
        error=trace,
        duration_s=4.0,
    )
    failures = SweepQuery(telemetry, "sweep-a").failures()

    assert failures.height == 1
    assert failures.get_column(SweepEventColumn.ERROR).to_list() == [trace]
    assert failures.get_column(SweepEventColumn.POINT_NAME).to_list() == ["stall at 3 A"]

    journal.finish()


def test_checkpoint_moves_journalled_events_into_parquet(tmp_path: Path) -> None:
    """A long sweep folds its events down as it goes, so the journal never becomes the whole record."""
    telemetry = Telemetry(tmp_path)
    journal = _started(telemetry, "sweep-a")
    journal.event("p1", SweepState.DONE, origin="solved", duration_s=1.0)

    journal.checkpoint()

    assert journal.journal.path.stat().st_size == 0
    assert telemetry.sweep_events.scan().collect().get_column(SweepEventColumn.POINT_KEY).to_list() == ["p1"]

    journal.finish()


def test_finish_recovers_orphans_and_folds_the_shards(tmp_path: Path) -> None:
    """A worker killed mid-sweep loses nothing: its journal is recovered at the close, and the shards fold into one."""
    telemetry = Telemetry(tmp_path)
    journal = _started(telemetry, "sweep-a")
    journal.event("p1", SweepState.DONE)
    journal.checkpoint()
    journal.event("p2", SweepState.DONE)

    orphan = _worker_journal(telemetry.live, "sweep-a", [("p3", SweepState.DONE, datetime.now(timezone.utc))])

    journal.finish()

    assert not orphan.exists()
    assert not journal.journal.path.exists()
    assert len(telemetry.sweep_events.shards()) == 1
    assert SweepQuery(telemetry, "sweep-a").latest().height == 3


def test_the_summary_counts_states_and_extrapolates_an_eta(tmp_path: Path) -> None:
    """Throughput is measured over the trailing window, and the outstanding points are divided by it."""
    telemetry = Telemetry(tmp_path)
    journal = _started(telemetry, "sweep-a", total=6)
    start: datetime = journal.started_at

    settled: list[tuple[str, SweepState, datetime]] = [
        ("p1", SweepState.DONE, start + timedelta(minutes=5)),
        ("p2", SweepState.DONE, start + timedelta(minutes=20)),
        ("p3", SweepState.FAILED, start + timedelta(minutes=25)),
        ("p4", SweepState.SKIPPED, start + timedelta(minutes=26)),
        ("p5", SweepState.RUNNING, start + timedelta(minutes=27)),
        ("p6", SweepState.QUEUED, start + timedelta(minutes=28)),
    ]
    _ = _worker_journal(telemetry.live, "sweep-a", settled)

    summary = SweepQuery(telemetry, "sweep-a").summary(now=start + timedelta(minutes=40))

    assert (summary.done, summary.failed, summary.skipped, summary.running, summary.queued) == (2, 1, 1, 1, 1)
    assert summary.elapsed_s == 2400.0
    # Two of the three settled points landed inside the trailing 30 minutes, measured over the full window.
    assert summary.rate_per_h == 4.0
    assert summary.eta_s == 1800.0

    journal.finish()


def test_the_summary_of_a_sweep_with_nothing_settled_has_no_eta(tmp_path: Path) -> None:
    """Nothing has landed, so there is no rate to extrapolate from and the ETA stays unknown."""
    telemetry = Telemetry(tmp_path)
    journal = _started(telemetry, "sweep-a")
    journal.event("p1", SweepState.QUEUED)

    summary = SweepQuery(telemetry, "sweep-a").summary()

    assert summary.queued == 1
    assert summary.rate_per_h == 0.0
    assert summary.eta_s is None

    journal.finish()


def test_an_unknown_sweep_summarizes_as_empty(tmp_path: Path) -> None:
    """A sweep id nothing was ever written for reads as zeroes rather than raising."""
    summary = SweepQuery(Telemetry(tmp_path), "never-ran").summary()

    assert (summary.queued, summary.done, summary.failed) == (0, 0, 0)
    assert summary.elapsed_s == 0.0
    assert summary.eta_s is None
