"""Where telemetry lives: the root resolution rule and the typed hive stores under it."""

from __future__ import annotations

import os
from enum import StrEnum
from functools import cached_property
from pathlib import Path

from femforge.common.log import TELEMETRY_ENV
from femforge.telemetry.hive import HiveStore
from femforge.telemetry.schemas import (
    ARTIFACT_INDEX_PARTITIONS,
    ARTIFACT_INDEX_SCHEMA,
    LOG_PARTITIONS,
    LOG_SCHEMA,
    RUN_INDEX_PARTITIONS,
    RUN_INDEX_SCHEMA,
    SWEEP_EVENT_PARTITIONS,
    SWEEP_EVENT_SCHEMA,
    SWEEP_RUN_PARTITIONS,
    SWEEP_RUN_SCHEMA,
)


__all__ = ["TELEMETRY_ENV", "Telemetry", "TelemetryDir", "TelemetryRootMissing", "require_root", "resolve_root"]


class TelemetryRootMissing(ValueError):
    """Neither an explicit path nor ``FEMFORGE_TELEMETRY`` names a telemetry root."""


class TelemetryDir(StrEnum):
    """The subdirectories of a telemetry root: one per table, plus the journal directory the live tables are tailed from."""

    LOGS = "logs"
    """The log table."""

    RUNS = "runs"
    """The run index."""

    ARTIFACTS = "artifacts"
    """The artifact index."""

    SWEEPS = "sweeps"
    """The sweep table: a launch row per sweep, plus a closing row once it finishes."""

    SWEEP_EVENTS = "sweep_events"
    """The sweep event table: one row per point transition."""

    LIVE = "live"
    """The write-ahead journals the live tables are tailed from."""


def resolve_root(explicit: str | Path | None = None) -> Path | None:
    """The telemetry root: ``explicit`` if given, else ``FEMFORGE_TELEMETRY``, else None because telemetry is off."""
    if explicit is not None:
        return Path(explicit)

    from_env = os.environ.get(TELEMETRY_ENV)
    if not from_env:
        return None

    return Path(from_env)


def require_root(explicit: str | Path | None = None) -> Path:
    """The telemetry root a reader needs, or [`TelemetryRootMissing`][] naming both ways to provide one."""
    root: Path | None = resolve_root(explicit)
    if root is None:
        raise TelemetryRootMissing(f"no telemetry root: pass --telemetry or set {TELEMETRY_ENV}")

    return root


class Telemetry:
    """A telemetry root and the typed hive store for each table under it."""

    def __init__(self, root: str | Path) -> None:
        """Address the tables under the telemetry root ``root``."""
        self.root: Path = Path(root)

    @property
    def live(self) -> Path:
        """The directory holding each process's write-ahead JSONL journal."""
        return self.root / TelemetryDir.LIVE

    @cached_property
    def logs(self) -> HiveStore:
        """Every log record, partitioned by date and process."""
        return HiveStore(self.root / TelemetryDir.LOGS, LOG_SCHEMA, LOG_PARTITIONS)

    @cached_property
    def runs(self) -> HiveStore:
        """The run index, partitioned by the month a run was created in."""
        return HiveStore(self.root / TelemetryDir.RUNS, RUN_INDEX_SCHEMA, RUN_INDEX_PARTITIONS)

    @cached_property
    def artifacts(self) -> HiveStore:
        """The artifact index, partitioned by artifact kind."""
        return HiveStore(self.root / TelemetryDir.ARTIFACTS, ARTIFACT_INDEX_SCHEMA, ARTIFACT_INDEX_PARTITIONS)

    @cached_property
    def sweeps(self) -> HiveStore:
        """A launch row per sweep plus a closing row once it finishes, partitioned by the month it started in."""
        return HiveStore(self.root / TelemetryDir.SWEEPS, SWEEP_RUN_SCHEMA, SWEEP_RUN_PARTITIONS)

    @cached_property
    def sweep_events(self) -> HiveStore:
        """Per-point state transitions, partitioned by sweep so one sweep's progress reads one directory."""
        return HiveStore(self.root / TelemetryDir.SWEEP_EVENTS, SWEEP_EVENT_SCHEMA, SWEEP_EVENT_PARTITIONS)

    def stores(self) -> list[HiveStore]:
        """Every table under this root, so a maintenance pass reaches them all without naming them one by one."""
        return [self.logs, self.runs, self.artifacts, self.sweeps, self.sweep_events]
