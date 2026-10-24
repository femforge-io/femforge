"""Append-only hive-partitioned parquet: one shard per writer per partition, never rewritten under a reader."""

from __future__ import annotations

import itertools
import os
import socket
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

import polars as pl

from femforge.common.files import TMP_SUFFIX, replace_atomic


if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


__all__ = ["HOST", "HiveStore", "coerce", "schema_runs", "shard_name", "write_shard"]

HOST = socket.gethostname()
"""This machine's name, stamped into every shard and journal filename and into every row that records its writer."""

SHARD_PREFIX = "part-"
"""Every shard filename starts with this; the rest is host, pid, a UTC stamp and a per-process sequence number."""

SHARD_SUFFIX = ".parquet"
"""The suffix of every shard filename."""

SETTLE_S = 300.0
"""`t` `[s]`: How long a shard must go untouched before ``compact`` may fold it into a merged shard."""

_STAMP_FORMAT = "%Y%m%dT%H%M%S"

_sequence = itertools.count()
_sequence_lock = threading.Lock()


def shard_name() -> str:
    """A shard filename unique across hosts, processes and writes: ``part-<host>-<pid>-<utcstamp>-<seq>.parquet``, the sequence zero-padded so names sort in write order."""
    with _sequence_lock:
        seq = next(_sequence)

    stamp = datetime.now(timezone.utc).strftime(_STAMP_FORMAT)

    return f"{SHARD_PREFIX}{HOST}-{os.getpid()}-{stamp}-{seq:09d}{SHARD_SUFFIX}"


def write_shard(directory: Path, body: pl.DataFrame) -> Path:
    """Write ``body`` into ``directory`` as a fresh shard, staged in a ``.tmp`` sibling so a reader never sees it half written."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / shard_name()

    tmp = path.with_name(path.name + TMP_SUFFIX)
    body.write_parquet(tmp)
    replace_atomic(tmp, path)

    return path


def schema_runs(shards: Sequence[Path]) -> list[list[str]]:
    """The shards split into consecutive runs that share one parquet schema, in their given order."""
    return [[shard.as_posix() for shard in run] for _, run in itertools.groupby(shards, key=pl.read_parquet_schema)]


def coerce(frame: pl.LazyFrame, schema: pl.Schema) -> pl.LazyFrame:
    """Cast a loosely-typed frame (JSON text, a partial row set) onto ``schema``, parsing date/time strings and filling missing columns with nulls."""
    present = frame.collect_schema()

    columns: list[pl.Expr] = []
    for name, dtype in schema.items():
        source = present.get(name)
        if source is None:
            columns.append(pl.lit(None, dtype).alias(name))
            continue

        column = pl.col(name)
        if isinstance(dtype, pl.Enum) and source != dtype:
            # An unrecognised category nulls rather than raising: a stray value must not kill the flush that carries it.
            columns.append(column.cast(dtype, strict=False).alias(name))
            continue

        if source == pl.String and isinstance(dtype, pl.Datetime):
            columns.append(column.str.to_datetime(time_unit=dtype.time_unit, time_zone=dtype.time_zone).alias(name))
            continue

        if source == pl.String and dtype == pl.Date:
            columns.append(column.str.to_date().alias(name))
            continue

        columns.append(column.cast(dtype).alias(name))

    return frame.select(columns)


class HiveStore:
    """One hive-partitioned parquet table: a typed ``scan``, an atomic ``append`` of one shard per partition, and a settling ``compact``."""

    def __init__(self, root: Path, schema: pl.Schema, partition_keys: Sequence[str]) -> None:
        """Address the table stored under ``root``; nothing touches disk until a read or write.

        Args:
            root: The table's directory, holding one directory level per partition key.
            schema: Every column of the table, partition columns included.
            partition_keys: The columns whose values name the partition directories, outermost first.
        """
        self.root: Path = root
        self.schema: pl.Schema = schema
        self.partition_keys: tuple[str, ...] = tuple(str(key) for key in partition_keys)

    @property
    def hive_schema(self) -> pl.Schema:
        """The partition columns, whose values live in the directory names rather than the file bodies."""
        return pl.Schema({key: self.schema[key] for key in self.partition_keys})

    @property
    def body_schema(self) -> pl.Schema:
        """The columns actually stored inside each shard: everything but the partition keys."""
        return pl.Schema({name: dtype for name, dtype in self.schema.items() if name not in self.partition_keys})

    def shards(self) -> list[Path]:
        """Every shard file under the root, in sorted order."""
        if not self.root.is_dir():
            return []

        return sorted(self.root.rglob("*" + SHARD_SUFFIX))

    def partitions(self) -> list[Path]:
        """The directories holding shards, one per distinct partition-key combination."""
        directories = {shard.parent for shard in self.shards()}

        return sorted(directories)

    def scan(self) -> pl.LazyFrame:
        """A lazy frame over every shard, each typed onto the schema, or a typed empty frame when nothing has been written."""
        shards = self.shards()
        if not shards:
            return pl.LazyFrame(schema=self.schema)

        # polars refuses to unify Enum columns whose categories differ, so only shards of one schema share a scan, and each run is typed before they meet.
        typed = [coerce(pl.scan_parquet(paths, hive_partitioning=True, hive_schema=self.hive_schema), self.schema) for paths in schema_runs(shards)]

        return pl.concat(typed, how="vertical")

    def append(self, frame: pl.DataFrame) -> list[Path]:
        """Write ``frame`` as one new shard per partition and return the shard paths; existing shards are untouched."""
        if frame.is_empty():
            return []

        typed = frame.cast(self.schema).select(self.schema.names())
        groups = cast("dict[tuple[object, ...], pl.DataFrame]", typed.partition_by(self.partition_keys, as_dict=True, include_key=False))

        written: list[Path] = []
        for values, body in groups.items():
            segments = (f"{key}={value}" for key, value in zip(self.partition_keys, values, strict=True))
            written.append(write_shard(self.root.joinpath(*segments), body))

        return written

    def compact(self, settle_s: float = SETTLE_S) -> list[Path]:
        """Fold each partition's settled shards into one merged shard and delete the originals; returns the merged shards."""
        cutoff = time.time() - settle_s

        merged: list[Path] = []
        for directory in self.partitions():
            shards = sorted(directory.glob("*" + SHARD_SUFFIX))
            settled = [shard for shard in shards if shard.stat().st_mtime <= cutoff]
            if len(settled) < 2:
                continue

            typed = [coerce(pl.scan_parquet(paths), self.body_schema) for paths in schema_runs(settled)]
            body = pl.concat(typed, how="vertical").collect()
            merged.append(write_shard(directory, body))

            for shard in settled:
                shard.unlink()

        return merged
