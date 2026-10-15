"""The artifact index: store sidecars read into parquet by directory mtime, with a fluent query over them."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

import polars as pl

from femforge.telemetry.schemas import ARTIFACT_INDEX_SCHEMA, TIMESTAMP, ArtifactColumn


if TYPE_CHECKING:
    from collections.abc import Sequence

    from femforge.artifacts.local import LocalArtifactStore, StoredArtifact
    from femforge.telemetry.paths import Telemetry


__all__ = ["ArtifactIndex", "ArtifactQuery", "artifact_row"]

CANDIDATE_SCHEMA = pl.Schema({ArtifactColumn.KIND: pl.String, ArtifactColumn.NAME: pl.String, ArtifactColumn.DIR_MTIME: TIMESTAMP})
"""The columns the incremental pass anti-joins on: a sidecar is re-read exactly when its directory mtime is unknown."""


def artifact_row(artifact: StoredArtifact, *, store_root: Path, dir_mtime: datetime) -> dict[str, object]:
    """One artifact index row from a committed sidecar plus the mtime of the directory holding it.

    Args:
        artifact: The committed artifact and its sidecar.
        store_root: The root of the store holding it.
        dir_mtime: The artifact directory's modification time.
    """
    record = artifact.record
    tools = record.tools

    return {
        ArtifactColumn.KIND: record.kind,
        ArtifactColumn.KEY: record.key,
        ArtifactColumn.NAME: artifact.name,
        ArtifactColumn.STORE_ROOT: str(store_root),
        ArtifactColumn.CREATED_AT: record.created_at.astimezone(timezone.utc),
        ArtifactColumn.SPEC_TYPE: record.spec_type,
        ArtifactColumn.PRODUCER_VERSION: record.producer_version,
        ArtifactColumn.PRODUCER_HOST: record.producer_host,
        ArtifactColumn.PRODUCER_PID: record.producer_pid,
        ArtifactColumn.FILE_COUNT: len(record.files),
        ArtifactColumn.TOTAL_BYTES: record.total_bytes,
        ArtifactColumn.FILES: list(record.file_names),
        ArtifactColumn.SPEC: json.dumps(record.spec, default=repr),
        ArtifactColumn.FEMFORGE: tools.femforge,
        ArtifactColumn.CODE_SHA: tools.code_sha,
        ArtifactColumn.GMSH: tools.gmsh,
        ArtifactColumn.ELMER: tools.elmer,
        ArtifactColumn.DIR_MTIME: dir_mtime,
        ArtifactColumn.INDEXED_AT: datetime.now(timezone.utc),
    }


@dataclass(frozen=True)
class ArtifactQuery:
    """A fluent filter over the artifact index; each method returns a new query and nothing runs until ``frame``."""

    lazy: pl.LazyFrame
    """The artifact index rows the query filters."""

    store: LocalArtifactStore
    """The store ``records`` reads the matching artifacts back from."""

    limit: int | None = None
    """How many of the newest artifacts to keep; None keeps them all."""

    def where(self, predicate: pl.Expr) -> ArtifactQuery:
        """A new query narrowed by ``predicate``; the other filters are named shorthands for this."""
        return replace(self, lazy=self.lazy.filter(predicate))

    def kind(self, *kinds: str) -> ArtifactQuery:
        """Artifacts of any of ``kinds``."""
        return self.where(pl.col(ArtifactColumn.KIND).is_in(list(kinds)))

    def spec_type(self, name: str) -> ArtifactQuery:
        """Artifacts whose spec model's ``module:QualName`` ends with ``name``, so the bare class name is enough."""
        return self.where(pl.col(ArtifactColumn.SPEC_TYPE).str.ends_with(name))

    def since(self, when: datetime) -> ArtifactQuery:
        """Artifacts published at or after ``when``."""
        return self.where(pl.col(ArtifactColumn.CREATED_AT) >= when)

    def key_prefix(self, prefix: str) -> ArtifactQuery:
        """Artifacts whose key starts with ``prefix``."""
        return self.where(pl.col(ArtifactColumn.KEY).str.starts_with(prefix))

    def newest(self, count: int) -> ArtifactQuery:
        """Keep only the ``count`` most recently published artifacts, applied after the duplicate rows collapse."""
        return replace(self, limit=count)

    def frame(self) -> pl.DataFrame:
        """The matching rows, newest first, one per stored artifact (the latest row wins)."""
        deduped = self.lazy.sort(ArtifactColumn.INDEXED_AT, maintain_order=True).unique(subset=[ArtifactColumn.KIND, ArtifactColumn.KEY], keep="last", maintain_order=True)
        ordered = deduped.sort(ArtifactColumn.CREATED_AT, descending=True, nulls_last=True, maintain_order=True)

        return (ordered.head(self.limit) if self.limit is not None else ordered).collect()

    def records(self) -> list[StoredArtifact]:
        """The matching artifacts read back from the store, skipping any whose directory is gone."""
        rows: pl.DataFrame = self.frame()
        roots: list[str] = cast("list[str]", rows[ArtifactColumn.STORE_ROOT].to_list())
        kinds: list[str] = cast("list[str]", rows[ArtifactColumn.KIND].to_list())
        names: list[str] = cast("list[str]", rows[ArtifactColumn.NAME].to_list())

        found: list[StoredArtifact] = []
        for root, kind, name in zip(roots, kinds, names, strict=True):
            artifact: StoredArtifact | None = self.store.read(Path(root) / kind / name)
            if artifact is None:
                continue

            found.append(artifact)

        return found


class ArtifactIndex:
    """The parquet index of one local artifact store: an incremental ``update`` over its committed directories, plus ``query``."""

    def __init__(self, telemetry: Telemetry, store: LocalArtifactStore) -> None:
        """Track the committed artifacts of ``store`` in the artifact table of ``telemetry``."""
        self.telemetry: Telemetry = telemetry
        self.store: LocalArtifactStore = store
        self.hive = telemetry.artifacts

    def _candidates(self) -> list[tuple[str, Path, datetime]]:
        """Every committed artifact directory with its kind and mtime, one stat per directory and not one sidecar read."""
        found: list[tuple[str, Path, datetime]] = []
        for kind in self.store.kinds():
            for directory in self.store.committed(kind):
                try:
                    mtime: float = directory.stat().st_mtime
                except OSError:
                    continue

                found.append((kind, directory, datetime.fromtimestamp(mtime, tz=timezone.utc)))

        return found

    def _changed(self, candidates: Sequence[tuple[str, Path, datetime]]) -> list[tuple[str, Path, datetime]]:
        """The candidates whose (kind, name, directory mtime) triple the index does not already hold."""
        indexed = self.hive.scan().filter(pl.col(ArtifactColumn.STORE_ROOT) == str(self.store.root)).select(*CANDIDATE_SCHEMA.names())
        offered = pl.LazyFrame(
            {
                ArtifactColumn.KIND: [kind for kind, _, _ in candidates],
                ArtifactColumn.NAME: [directory.name for _, directory, _ in candidates],
                ArtifactColumn.DIR_MTIME: [mtime for _, _, mtime in candidates],
            },
            schema=CANDIDATE_SCHEMA,
        )

        stale: pl.DataFrame = offered.join(indexed, on=list(CANDIDATE_SCHEMA.names()), how="anti").collect()
        kinds: list[str] = cast("list[str]", stale[ArtifactColumn.KIND].to_list())
        names: list[str] = cast("list[str]", stale[ArtifactColumn.NAME].to_list())
        fresh: set[tuple[str, str]] = set(zip(kinds, names, strict=True))

        return [entry for entry in candidates if (entry[0], entry[1].name) in fresh]

    def update(self, *, rebuild: bool = False) -> int:
        """Index every artifact directory whose mtime moved since the last pass — all of them when ``rebuild`` — and return the rows appended."""
        candidates: list[tuple[str, Path, datetime]] = self._candidates()
        if not candidates:
            return 0

        pending = candidates if rebuild else self._changed(candidates)

        rows: list[dict[str, object]] = []
        for _, directory, mtime in pending:
            artifact: StoredArtifact | None = self.store.read(directory)
            if artifact is None:
                continue

            rows.append(artifact_row(artifact, store_root=self.store.root, dir_mtime=mtime))

        if not rows:
            return 0

        _ = self.hive.append(pl.DataFrame(rows, schema=ARTIFACT_INDEX_SCHEMA))

        return len(rows)

    def query(self) -> ArtifactQuery:
        """A fluent query over the index, reading no sidecar until ``records`` asks for one."""
        return ArtifactQuery(self.hive.scan(), self.store)
