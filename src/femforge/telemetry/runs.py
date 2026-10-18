"""The run index: bundles read into parquet by manifest mtime, a fluent query over them, and the live overlay from the run locks."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

import polars as pl
from pydantic import TypeAdapter, ValidationError

from femforge.artifacts.lock import LOCK_SUFFIX, STALE_AFTER_S, DirLock
from femforge.common.files import BundleFile, tree_size
from femforge.frames import Frame, FrameUnion
from femforge.telemetry.context import current_sweep_point
from femforge.telemetry.schemas import LIVE_RUN_SCHEMA, RUN_INDEX_SCHEMA, TIMESTAMP, LiveRunColumn, RunColumn, RunSource


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from femforge.run.inspect import Run
    from femforge.run.manifest import RunManifest
    from femforge.telemetry.paths import Telemetry


__all__ = ["RunIndex", "RunQuery", "live_runs", "run_row"]

CANDIDATE_SCHEMA = pl.Schema({RunColumn.RUN_ID: pl.String, RunColumn.MANIFEST_MTIME: TIMESTAMP})
"""The two columns the incremental pass anti-joins on: a bundle is re-read exactly when its manifest mtime is unknown."""

FRAME: TypeAdapter[FrameUnion] = TypeAdapter(FrameUnion)
"""Reads a manifest's recorded ``config.frame``."""

RECORDED = "_recorded"
"""The suffix the driver's sweep columns carry while a scanned row inherits them."""


def _section(value: object) -> Mapping[str, object]:
    """A nested JSON object, or an empty mapping when it is absent or holds something else."""
    return cast("Mapping[str, object]", value) if isinstance(value, dict) else {}


def _items(value: object) -> Sequence[object]:
    """A nested JSON array, or an empty sequence when it is absent or holds something else."""
    return cast("Sequence[object]", value) if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _count(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _magnitude(value: object) -> float | None:
    """The number in a serialized scalar pint quantity (``"370.9 K"``), so a shallow read needs no unit registry."""
    text: str | None = _text(value)
    if text is None:
        return None

    number, _, _ = text.partition(" ")
    try:
        return float(number)
    except ValueError:
        return None


def _moment(value: object) -> datetime | None:
    """An ISO-8601 timestamp out of JSON, normalized to UTC; None when absent or unparseable."""
    text: str | None = _text(value)
    if text is None:
        return None

    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None

    return when.astimezone(timezone.utc)


def _shallow(path: Path) -> Mapping[str, object]:
    """One bundle JSON file parsed but never validated: the index reads a dozen keys, not the 26 KB config."""
    try:
        data = cast("object", json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}

    return _section(data)


def run_row(manifest: Mapping[str, object], sim_dir: Path, *, source: RunSource, manifest_mtime: datetime) -> dict[str, object]:
    """One run index row from a bundle's parsed (never validated) manifest plus the facts only its directory carries.

    Args:
        manifest: The bundle's ``manifest.json`` parsed as plain JSON.
        sim_dir: The bundle directory.
        source: How the row was produced.
        manifest_mtime: The manifest's modification time; it also picks the partition month when ``created_at`` is unreadable.
    """
    # Imported here: femforge.run pulls in the whole solve stack, and telemetry is imported by the log sink.
    from femforge.run.parse import result_vtu

    def model_dim(value: object) -> int | None:
        """The mesh dimension of a recorded frame, or None when it is absent or unreadable."""
        if value is None:
            return None

        try:
            frame: Frame = FRAME.validate_python(value)
        except ValidationError:
            return None

        return frame.model_dim

    config: Mapping[str, object] = _section(manifest.get("config"))
    meta: Mapping[str, object] = _section(config.get("meta"))
    stats: Mapping[str, object] = _section(manifest.get("mesh_stats"))
    result: Mapping[str, object] = _shallow(sim_dir / BundleFile.RESULT)

    events: list[Mapping[str, object]] = [_section(event) for event in _items(manifest.get("stage_history"))]
    running: list[Mapping[str, object]] = [event for event in events if event.get("finished_at") is None]
    finishes: list[datetime] = [when for event in events if (when := _moment(event.get("finished_at"))) is not None]

    artifact_keys: list[str] = []
    for ref in _items(manifest.get("artifacts")):
        key: str | None = _text(_section(ref).get("key"))
        if key is None:
            continue

        artifact_keys.append(key)

    created: datetime | None = _moment(manifest.get("created_at"))
    mesh_key: str = _text(manifest.get("mesh_key")) or ""
    tags: object = meta.get("tags")
    point = current_sweep_point()

    return {
        # The partition key, so never null: a bundle whose manifest carries no readable date is filed under the mtime that got it read.
        RunColumn.MONTH: (created if created is not None else manifest_mtime).date().replace(day=1),
        RunColumn.RUN_ID: _text(manifest.get("run_id")) or sim_dir.name,
        RunColumn.CACHE_KEY: _text(manifest.get("cache_key")),
        RunColumn.RUNS_DIR: str(sim_dir.parent),
        RunColumn.STATUS: _text(manifest.get("status")),
        RunColumn.CREATED_AT: created,
        RunColumn.FINISHED_AT: max(finishes) if finishes else None,
        RunColumn.CURRENT_STAGE: _text(running[0].get("stage")) if running else None,
        RunColumn.STAGES_DONE: len(finishes),
        RunColumn.GIT_SHA: _text(manifest.get("git_sha")),
        RunColumn.ELMER_VERSION: _text(manifest.get("elmer_version")),
        RunColumn.PHYSICS: _text(manifest.get("physics")),
        RunColumn.ELEMENT_COUNT: _count(stats.get("element_count")),
        RunColumn.MIN_ELEMENT_SIZE: _number(stats.get("min_element_size")),
        RunColumn.MAX_ELEMENT_SIZE: _number(stats.get("max_element_size")),
        RunColumn.DESCRIPTION: _text(meta.get("description")),
        RunColumn.TAGS: json.dumps(tags) if isinstance(tags, dict) else None,
        RunColumn.NOTES: _text(manifest.get("notes")),
        RunColumn.MESH_NAME: _text(manifest.get("mesh_name")),
        RunColumn.MESH_KEY: mesh_key or None,
        RunColumn.PUBLISHED: manifest.get("published") is not None,
        RunColumn.HAS_RESULT: bool(result),
        RunColumn.HAS_VTU: result_vtu(sim_dir) is not None,
        RunColumn.CLEANUPS: len(_items(manifest.get("cleanups"))),
        RunColumn.BUNDLE_BYTES: tree_size(sim_dir),
        RunColumn.SWEEP_ID: point.sweep_id if point is not None else None,
        RunColumn.POINT_KEY: point.point_key if point is not None else None,
        RunColumn.MANIFEST_MTIME: manifest_mtime,
        RunColumn.INDEXED_AT: datetime.now(timezone.utc),
        RunColumn.SOURCE: source.value,
        RunColumn.MODEL_DIM: model_dim(config.get("frame")),
        RunColumn.ARTIFACT_KEYS: artifact_keys,
        RunColumn.FORCE_Y: _number(_section(result.get("force")).get("y")),
        RunColumn.TEMPERATURE_MAX: _magnitude(result.get("temperature_max")),
        RunColumn.WALL_TIME: _number(result.get("wall_time")),
    }


def live_runs(runs_dir: str | Path) -> pl.DataFrame:
    """The runs claimed right now under ``runs_dir``, read from each ``<run_id>.lock/owner.json`` with the age of its heartbeat."""
    directory = Path(runs_dir)
    if not directory.is_dir():
        return pl.DataFrame(schema=LIVE_RUN_SCHEMA)

    now = datetime.now(timezone.utc)

    rows: list[dict[str, object]] = []
    for lock in sorted(directory.glob("*" + LOCK_SUFFIX)):
        owner = DirLock(lock).owner()
        if owner is None:
            continue

        age: float = (now - owner.heartbeat_at).total_seconds()
        rows.append(
            {
                LiveRunColumn.RUN_ID: lock.name.removesuffix(LOCK_SUFFIX),
                LiveRunColumn.HOST: owner.host,
                LiveRunColumn.PID: owner.pid,
                LiveRunColumn.STARTED_AT: owner.started_at,
                LiveRunColumn.HEARTBEAT_AT: owner.heartbeat_at,
                LiveRunColumn.HEARTBEAT_AGE_S: age,
                LiveRunColumn.STALE: age > STALE_AFTER_S,
            }
        )

    return pl.DataFrame(rows, schema=LIVE_RUN_SCHEMA)


@dataclass(frozen=True)
class RunQuery:
    """A fluent filter over the run index; each method returns a new query and nothing runs until ``frame``."""

    lazy: pl.LazyFrame
    """The run index rows the query filters."""

    predicates: tuple[pl.Expr, ...] = ()
    """The filters each run's latest row must pass."""

    limit: int | None = None
    """How many of the newest runs to keep; None keeps them all."""

    live: bool = False
    """Whether ``frame`` joins the lock-owner columns."""

    def where(self, predicate: pl.Expr) -> RunQuery:
        """A new query narrowed by ``predicate``, judged on each run's latest row; the other filters are named shorthands for this."""
        return replace(self, predicates=(*self.predicates, predicate))

    def status(self, *statuses: str) -> RunQuery:
        """Runs in any of ``statuses``."""
        return self.where(pl.col(RunColumn.STATUS).is_in(list(statuses)))

    def tags(self, **pairs: str) -> RunQuery:
        """Runs whose manifest metadata carries every one of these tag pairs."""
        query = self
        for key, value in pairs.items():
            query = query.where(pl.col(RunColumn.TAGS).str.json_path_match(f"$.{key}") == value)

        return query

    def text(self, needle: str) -> RunQuery:
        """Runs whose id, description or notes contain ``needle``, case-insensitively."""
        haystack = pl.concat_str(
            [
                pl.col(RunColumn.RUN_ID),
                pl.col(RunColumn.DESCRIPTION).fill_null(""),
                pl.col(RunColumn.NOTES).fill_null(""),
            ],
            separator=" ",
        )

        return self.where(haystack.str.to_lowercase().str.contains(needle.casefold(), literal=True))

    def since(self, when: datetime) -> RunQuery:
        """Runs created at or after ``when``."""
        return self.where(pl.col(RunColumn.CREATED_AT) >= when)

    def sweep(self, sweep_id: str) -> RunQuery:
        """Runs solved under the sweep ``sweep_id``."""
        return self.where(pl.col(RunColumn.SWEEP_ID) == sweep_id)

    def newest(self, count: int) -> RunQuery:
        """Keep only the ``count`` most recently created runs, applied after the duplicate rows collapse."""
        return replace(self, limit=count)

    def with_live(self) -> RunQuery:
        """Also carry the lock-owner columns, so a listing can mark which runs are in flight."""
        return replace(self, live=True)

    def frame(self) -> pl.DataFrame:
        """The matching rows, newest first, one per run (the latest row wins), joined with the live claims when asked."""
        deduped = self.lazy.sort(RunColumn.INDEXED_AT, maintain_order=True).unique(subset=RunColumn.RUN_ID, keep="last", maintain_order=True)

        narrowed = deduped
        for predicate in self.predicates:
            narrowed = narrowed.filter(predicate)

        ordered = narrowed.sort(RunColumn.CREATED_AT, descending=True, nulls_last=True, maintain_order=True)
        rows: pl.DataFrame = (ordered.head(self.limit) if self.limit is not None else ordered).collect()

        if not self.live:
            return rows

        directories: list[str] = cast("list[str]", rows[RunColumn.RUNS_DIR].unique().to_list())
        claims = pl.concat([live_runs(directory) for directory in directories]) if directories else pl.DataFrame(schema=LIVE_RUN_SCHEMA)

        return rows.join(claims, on=RunColumn.RUN_ID, how="left", maintain_order="left")

    def run_ids(self) -> list[str]:
        """The ids of the matching runs, newest first."""
        return cast("list[str]", self.frame()[RunColumn.RUN_ID].to_list())

    def runs(self) -> list[Run]:
        """The matching bundles as ``Run`` facades, skipping the ones retention has since removed."""
        from femforge.run.inspect import Run

        rows: pl.DataFrame = self.frame()
        directories: list[str] = cast("list[str]", rows[RunColumn.RUNS_DIR].to_list())
        names: list[str] = cast("list[str]", rows[RunColumn.RUN_ID].to_list())

        found: list[Run] = []
        for directory, name in zip(directories, names, strict=True):
            path = Path(directory) / name
            if not path.is_dir():
                continue

            found.append(Run(path))

        return found


class RunIndex:
    """The parquet index of one runs directory: an incremental ``update`` over its bundles, the ``record`` a driver appends per run, and ``query``."""

    def __init__(self, telemetry: Telemetry, runs_dir: str | Path) -> None:
        """Track the bundles under ``runs_dir`` in the run table of ``telemetry``."""
        self.telemetry: Telemetry = telemetry
        self.runs_dir: Path = Path(runs_dir)
        self.store = telemetry.runs

    def _candidates(self) -> list[tuple[Path, datetime]]:
        """Every bundle under the runs directory with its manifest's mtime, from one ``scandir`` pass and one stat per bundle."""
        if not self.runs_dir.is_dir():
            return []

        found: list[tuple[Path, datetime]] = []
        with os.scandir(self.runs_dir) as entries:
            for entry in entries:
                if not entry.is_dir() or entry.name.endswith(LOCK_SUFFIX):
                    continue

                try:
                    mtime: float = (Path(entry.path) / BundleFile.MANIFEST).stat().st_mtime
                except OSError:
                    continue

                found.append((Path(entry.path), datetime.fromtimestamp(mtime, tz=timezone.utc)))

        return found

    def _changed(self, candidates: Sequence[tuple[Path, datetime]]) -> list[tuple[Path, datetime]]:
        """The candidates whose (run_id, manifest mtime) pair the index does not already hold."""
        indexed = self.store.scan().filter(pl.col(RunColumn.RUNS_DIR) == str(self.runs_dir)).select(RunColumn.RUN_ID, RunColumn.MANIFEST_MTIME)
        offered = pl.LazyFrame(
            {
                RunColumn.RUN_ID: [sim_dir.name for sim_dir, _ in candidates],
                RunColumn.MANIFEST_MTIME: [mtime for _, mtime in candidates],
            },
            schema=CANDIDATE_SCHEMA,
        )

        fresh: set[str] = set(cast("list[str]", offered.join(indexed, on=list(CANDIDATE_SCHEMA.names()), how="anti").collect()[RunColumn.RUN_ID].to_list()))

        return [entry for entry in candidates if entry[0].name in fresh]

    def update(self, *, rebuild: bool = False) -> int:
        """Index every bundle whose ``manifest.json`` moved since the last pass — all of them when ``rebuild`` — and return the rows appended."""
        candidates: list[tuple[Path, datetime]] = self._candidates()
        if not candidates:
            return 0

        pending = candidates if rebuild else self._changed(candidates)

        rows: list[dict[str, object]] = []
        for sim_dir, mtime in pending:
            rows.append(
                run_row(
                    _shallow(sim_dir / BundleFile.MANIFEST),
                    sim_dir,
                    source=RunSource.SCAN,
                    manifest_mtime=mtime,
                )
            )

        if not rows:
            return 0

        _ = self.store.append(self._inherit_sweep(pl.DataFrame(rows, schema=RUN_INDEX_SCHEMA)))

        return len(rows)

    def _inherit_sweep(self, frame: pl.DataFrame) -> pl.DataFrame:
        """The scanned rows with the sweep and point each run was filed under carried forward from the driver's row, since only the worker that solved it knew them."""
        ids = cast("list[str]", frame[RunColumn.RUN_ID].to_list())
        recorded = (
            self.store.scan()
            .filter(pl.col(RunColumn.RUNS_DIR) == str(self.runs_dir), pl.col(RunColumn.RUN_ID).is_in(ids), pl.col(RunColumn.SWEEP_ID).is_not_null())
            .sort(RunColumn.INDEXED_AT, maintain_order=True)
            .unique(subset=RunColumn.RUN_ID, keep="last", maintain_order=True)
            .select(RunColumn.RUN_ID, RunColumn.SWEEP_ID, RunColumn.POINT_KEY)
            .collect()
        )
        if recorded.is_empty():
            return frame

        joined = frame.join(recorded, on=RunColumn.RUN_ID, how="left", suffix=RECORDED)
        inherited = joined.with_columns(
            pl.coalesce(RunColumn.SWEEP_ID, RunColumn.SWEEP_ID + RECORDED),
            pl.coalesce(RunColumn.POINT_KEY, RunColumn.POINT_KEY + RECORDED),
        )

        return inherited.select(RUN_INDEX_SCHEMA.names())

    def record(self, manifest: RunManifest, sim_dir: Path, source: RunSource = RunSource.DRIVER) -> None:
        """Append one row for a run the driver just wrote, so the index is current without waiting for a scan.

        Args:
            manifest: The run's manifest.
            sim_dir: The bundle directory.
            source: How the row was produced.
        """
        try:
            mtime: float = (sim_dir / BundleFile.MANIFEST).stat().st_mtime
            written = datetime.fromtimestamp(mtime, tz=timezone.utc)
        except OSError:
            written = datetime.now(timezone.utc)

        row = run_row(
            cast("Mapping[str, object]", manifest.model_dump(mode="json")),
            sim_dir,
            source=source,
            manifest_mtime=written,
        )

        _ = self.store.append(pl.DataFrame([row], schema=RUN_INDEX_SCHEMA))

    def query(self) -> RunQuery:
        """A fluent query over the whole index — every runs directory the root knows, not only this one — with the live claims one ``with_live`` away."""
        return RunQuery(self.store.scan())
