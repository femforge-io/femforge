"""Sweep progress: one row per launch, one journalled event per point transition, and the query that reads them back."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast

import polars as pl

from femforge.telemetry.hive import HOST
from femforge.telemetry.journal import Journal, journal_glob, recover_journals, scan_journals
from femforge.telemetry.paths import TelemetryDir
from femforge.telemetry.schemas import SWEEP_EVENT_SCHEMA, SWEEP_RUN_SCHEMA, SweepColumn, SweepEventColumn, SweepState
from femforge.telemetry.sink import default_process, sanitize_process


if TYPE_CHECKING:
    from pathlib import Path

    from femforge.telemetry.paths import Telemetry


__all__ = ["RATE_WINDOW", "SweepJournal", "SweepQuery", "SweepSummary", "sweep_runs"]

RATE_WINDOW = timedelta(minutes=30)
"""How far back the throughput a sweep's ETA extrapolates from is measured; a shorter sweep is rated over its own life."""


@dataclass(frozen=True)
class SweepSummary:
    """Where a sweep stands: how many points sit in each state, how fast they are landing, and how long the rest should take."""

    queued: int
    """Points waiting for a worker."""

    running: int
    """Points a worker is solving."""

    done: int
    """Points that finished successfully."""

    failed: int
    """Points that finished with an error."""

    skipped: int
    """Points already stored when the sweep started."""

    rate_per_h: float
    """`ṅ` `[1/h]`: Points settled per hour over the trailing window."""

    eta_s: float | None
    """`t_eta` `[s]`: Seconds the outstanding points should take at that rate; None while nothing has settled."""

    elapsed_s: float
    """`t_elapsed` `[s]`: Seconds since the sweep started, up to its finish when it has one."""


def sweep_runs(telemetry: Telemetry) -> pl.DataFrame:
    """Every sweep the root knows, newest first and one row each (a finished sweep's closing row wins)."""
    deduped = telemetry.sweeps.scan().sort(SweepColumn.INDEXED_AT, maintain_order=True).unique(subset=SweepColumn.SWEEP_ID, keep="last", maintain_order=True)

    return deduped.sort(SweepColumn.STARTED_AT, descending=True, nulls_last=True, maintain_order=True).collect()


class SweepJournal:
    """One sweep's writer: the launch and closing rows in the sweeps table, every point transition through this process's own journal."""

    def __init__(self, telemetry: Telemetry, *, sweep_id: str, process: str | None = None) -> None:
        """Recover the event journals that dead workers left behind and open this process's own.

        Args:
            telemetry: The telemetry root the sweep is recorded under.
            sweep_id: The sweep this writer records.
            process: The process name stamped on the sweep row; None uses the script's name.
        """
        self.telemetry: Telemetry = telemetry
        self.sweep_id: str = sweep_id
        self.process: str = sanitize_process(process) if process is not None else default_process()

        self.started_at: datetime = datetime.now(timezone.utc)
        self.description: str = ""
        self.total: int = 0
        self.skipped: int = 0
        self.max_workers: int = 1

        _ = self._recover()
        self.journal: Journal = Journal(telemetry.live, TelemetryDir.SWEEP_EVENTS)

    def _recover(self) -> list[Path]:
        """Fold the sweep-event journals of processes that are gone into the store, so a killed worker's events are not lost."""
        return recover_journals(self.telemetry.live, self.telemetry.sweep_events, journal_glob(TelemetryDir.SWEEP_EVENTS))

    def _write_sweep(self, finished_at: datetime | None) -> None:
        from femforge.run.manifest import git_sha

        row: dict[str, object] = {
            SweepColumn.MONTH: self.started_at.date().replace(day=1),
            SweepColumn.SWEEP_ID: self.sweep_id,
            SweepColumn.DESCRIPTION: self.description,
            SweepColumn.STARTED_AT: self.started_at,
            SweepColumn.FINISHED_AT: finished_at,
            SweepColumn.TOTAL: self.total,
            SweepColumn.SKIPPED: self.skipped,
            SweepColumn.MAX_WORKERS: self.max_workers,
            SweepColumn.HOST: HOST,
            SweepColumn.PID: os.getpid(),
            SweepColumn.PROCESS: self.process,
            SweepColumn.GIT_SHA: git_sha(),
            SweepColumn.INDEXED_AT: datetime.now(timezone.utc),
        }

        _ = self.telemetry.sweeps.append(pl.DataFrame([row], schema=SWEEP_RUN_SCHEMA))

    def start(self, description: str, total: int, skipped: int = 0, max_workers: int = 1) -> None:
        """Open the sweep: stamp its launch row so a reader sees it the moment the first point starts.

        Args:
            description: What the sweep runs.
            total: How many points the sweep holds, skipped ones included.
            skipped: How many points were already stored and will not run.
            max_workers: How many worker processes run the points.
        """
        self.started_at = datetime.now(timezone.utc)
        self.description = description
        self.total = total
        self.skipped = skipped
        self.max_workers = max_workers

        self._write_sweep(finished_at=None)

    def event(self, point_key: str, state: SweepState, *, point_name: str | None = None, origin: str | None = None, error: str | None = None, duration_s: float | None = None) -> None:
        """Journal one point transition; the parent and each worker write through a journal of their own.

        Args:
            point_key: The point's key within the sweep.
            state: The state the point enters.
            point_name: A readable label for the point.
            origin: Where a finished point's result came from, such as an artifact origin.
            error: The traceback of a failed point.
            duration_s: Seconds the point took up to this transition.
        """
        self.journal.write(
            {
                SweepEventColumn.SWEEP_ID: self.sweep_id,
                SweepEventColumn.POINT_KEY: point_key,
                SweepEventColumn.POINT_NAME: point_name,
                SweepEventColumn.STATE: state.value,
                SweepEventColumn.AT: datetime.now(timezone.utc),
                SweepEventColumn.HOST: HOST,
                SweepEventColumn.PID: os.getpid(),
                SweepEventColumn.ORIGIN: origin,
                SweepEventColumn.ERROR: error,
                SweepEventColumn.DURATION_S: duration_s,
            }
        )

    def checkpoint(self) -> None:
        """Fold this process's journalled events into parquet without closing the sweep."""
        _ = self.journal.checkpoint(self.telemetry.sweep_events)

    def finish(self) -> None:
        """Close the sweep: the row gains a finish time, every journal lands in parquet, and the shards fold into one per sweep."""
        self._write_sweep(finished_at=datetime.now(timezone.utc))

        self.checkpoint()
        self.journal.close()
        _ = self._recover()
        _ = self.telemetry.sweep_events.compact(settle_s=0.0)


class SweepQuery:
    """One sweep read back: its launch row, every point transition (settled shards plus live journals), and the progress summary."""

    def __init__(self, telemetry: Telemetry, sweep_id: str) -> None:
        """Query the sweep ``sweep_id`` in the tables of ``telemetry``."""
        self.telemetry: Telemetry = telemetry
        self.sweep_id: str = sweep_id

    def launch(self) -> pl.DataFrame:
        """The sweep's own row: its closing one when it has finished, else the one its launch wrote; empty when unknown."""
        rows = self.telemetry.sweeps.scan().filter(pl.col(SweepColumn.SWEEP_ID) == self.sweep_id)

        return rows.sort(SweepColumn.INDEXED_AT, maintain_order=True).unique(subset=SweepColumn.SWEEP_ID, keep="last", maintain_order=True).collect()

    def events(self) -> pl.DataFrame:
        """Every transition of this sweep's points, oldest first, unioning what is still only in the live journals."""
        live = scan_journals(self.telemetry.live, journal_glob(TelemetryDir.SWEEP_EVENTS), SWEEP_EVENT_SCHEMA)
        union = pl.concat([self.telemetry.sweep_events.scan(), live])

        return union.filter(pl.col(SweepEventColumn.SWEEP_ID) == self.sweep_id).sort(SweepEventColumn.AT, maintain_order=True).collect()

    def latest(self) -> pl.DataFrame:
        """The state each point is in now: its last transition, one row per ``point_key``."""
        return self.events().unique(subset=SweepEventColumn.POINT_KEY, keep="last", maintain_order=True)

    def failures(self) -> pl.DataFrame:
        """The points whose last transition failed, each carrying the traceback the worker recorded."""
        return self.latest().filter(pl.col(SweepEventColumn.STATE) == SweepState.FAILED)

    def summary(self, now: datetime | None = None) -> SweepSummary:
        """Where the sweep stands at ``now``: the per-state counts, the trailing-window rate, and the ETA that rate implies."""
        moment: datetime = now if now is not None else datetime.now(timezone.utc)
        latest: pl.DataFrame = self.latest()
        states: list[str] = cast("list[str]", latest[SweepEventColumn.STATE].to_list())

        def count(state: SweepState) -> int:
            return sum(1 for value in states if value == state)

        launch: pl.DataFrame = self.launch()
        started: datetime = cast("datetime", launch[SweepColumn.STARTED_AT][0]) if launch.height else moment
        finished = cast("datetime | None", launch[SweepColumn.FINISHED_AT][0]) if launch.height else None
        elapsed_s: float = ((finished if finished is not None else moment) - started).total_seconds()

        settled = latest.filter(pl.col(SweepEventColumn.STATE).is_in([SweepState.DONE, SweepState.FAILED]))
        recent: int = settled.filter(pl.col(SweepEventColumn.AT) >= moment - RATE_WINDOW).height

        window_s: float = min(RATE_WINDOW.total_seconds(), elapsed_s)
        rate_per_h: float = recent / (window_s / 3600.0) if window_s > 0 else 0.0
        outstanding: int = count(SweepState.QUEUED) + count(SweepState.RUNNING)

        return SweepSummary(
            queued=count(SweepState.QUEUED),
            running=count(SweepState.RUNNING),
            done=count(SweepState.DONE),
            failed=count(SweepState.FAILED),
            skipped=count(SweepState.SKIPPED),
            rate_per_h=rate_per_h,
            eta_s=outstanding / rate_per_h * 3600.0 if rate_per_h > 0 else None,
            elapsed_s=elapsed_s,
        )
