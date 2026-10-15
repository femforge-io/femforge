"""Tests for telemetry.schemas: the enum-derived vocabularies and the partition keys every table declares."""

from __future__ import annotations

from typing import Literal

import polars as pl

from femforge.common.log import LogLevel
from femforge.run.result import ManifestStatus
from femforge.telemetry.schemas import (
    ARTIFACT_INDEX_PARTITIONS,
    ARTIFACT_INDEX_SCHEMA,
    CONVERGENCE_SCHEMA,
    LIVE_RUN_SCHEMA,
    LOG_PARTITIONS,
    LOG_SCHEMA,
    RUN_INDEX_PARTITIONS,
    RUN_INDEX_SCHEMA,
    SWEEP_EVENT_PARTITIONS,
    SWEEP_EVENT_SCHEMA,
    SWEEP_RUN_PARTITIONS,
    SWEEP_RUN_SCHEMA,
    ConvergenceColumn,
    LogColumn,
    RunColumn,
    SweepState,
    categories,
    literal_categories,
)


def test_categories_are_plain_strings() -> None:
    """polars rejects enum members as categories, so a StrEnum is unwrapped to its values."""
    assert categories(SweepState) == ["queued", "running", "done", "failed", "skipped"]
    assert all(type(value) is str for value in categories(LogLevel))


def test_literal_categories_flattens_a_union_of_literals() -> None:
    """A status alias built as one Literal or-ed with another reads as a single vocabulary."""
    alias = Literal["running"] | Literal["ok", "error"]

    assert literal_categories(alias) == ["running", "ok", "error"]


def test_literal_categories_keeps_a_bare_union_member() -> None:
    """A union arm that is not itself a Literal contributes its own string."""
    assert literal_categories(Literal["ok", "error"]) == ["ok", "error"]


def test_the_run_status_vocabulary_is_the_manifest_status() -> None:
    """The stored status column can hold exactly what a manifest can say."""
    status = RUN_INDEX_SCHEMA[RunColumn.STATUS]

    assert isinstance(status, pl.Enum)
    assert status.categories.to_list() == literal_categories(ManifestStatus)


def test_every_partition_key_is_a_column_of_its_table() -> None:
    """A partition key names a real column, so its dtype is known when the hive schema types it back."""
    tables: list[tuple[pl.Schema, tuple[str, ...]]] = [
        (LOG_SCHEMA, LOG_PARTITIONS),
        (RUN_INDEX_SCHEMA, RUN_INDEX_PARTITIONS),
        (ARTIFACT_INDEX_SCHEMA, ARTIFACT_INDEX_PARTITIONS),
        (SWEEP_RUN_SCHEMA, SWEEP_RUN_PARTITIONS),
        (SWEEP_EVENT_SCHEMA, SWEEP_EVENT_PARTITIONS),
    ]

    for schema, partitions in tables:
        assert partitions
        assert all(key in schema for key in partitions)


def test_the_unpartitioned_tables_are_built_per_call() -> None:
    """Live runs come from lock owners and convergence from a bundle, so neither is a hive table."""
    assert LIVE_RUN_SCHEMA.names()
    assert list(CONVERGENCE_SCHEMA.names()) == list(ConvergenceColumn)


def test_every_timestamp_is_microsecond_utc() -> None:
    """Shards from different writers concatenate only if their timestamps agree on unit and zone."""
    stamps = [dtype for dtype in LOG_SCHEMA.values() if isinstance(dtype, pl.Datetime)]

    assert stamps
    assert all(dtype.time_unit == "us" and dtype.time_zone == "UTC" for dtype in stamps)
    assert LOG_SCHEMA[LogColumn.TIME] == pl.Datetime("us", "UTC")
