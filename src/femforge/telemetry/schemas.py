"""The polars schemas of every telemetry table, with column names single-sourced as StrEnums."""

from __future__ import annotations

from enum import StrEnum
from typing import cast, get_args

import polars as pl

from femforge.common.log import LogLevel
from femforge.run.result import ManifestStatus


__all__ = [
    "ARTIFACT_INDEX_PARTITIONS",
    "ARTIFACT_INDEX_SCHEMA",
    "CONVERGENCE_SCHEMA",
    "LIVE_RUN_SCHEMA",
    "LOG_PARTITIONS",
    "LOG_SCHEMA",
    "RUN_INDEX_PARTITIONS",
    "RUN_INDEX_SCHEMA",
    "SWEEP_EVENT_PARTITIONS",
    "SWEEP_EVENT_SCHEMA",
    "SWEEP_RUN_PARTITIONS",
    "SWEEP_RUN_SCHEMA",
    "ArtifactColumn",
    "ConvergenceColumn",
    "ConvergenceLoop",
    "LiveRunColumn",
    "LogColumn",
    "RunColumn",
    "RunSource",
    "SweepColumn",
    "SweepEventColumn",
    "SweepState",
    "categories",
    "literal_categories",
]

TIMESTAMP = pl.Datetime("us", "UTC")
"""Every telemetry timestamp column: microsecond precision, always UTC."""


def categories[E: StrEnum](members: type[E]) -> list[str]:
    """The values of a StrEnum as the plain strings ``pl.Enum`` accepts (it rejects enum members)."""
    return [member.value for member in members]


def literal_categories(alias: object) -> list[str]:
    """The strings of a ``Literal`` union, flattened one level so a union of Literals reads as one vocabulary."""
    values: list[str] = []
    for arg in cast("tuple[object, ...]", get_args(alias)):
        nested = cast("tuple[object, ...]", get_args(arg))
        if not nested:
            values.append(str(arg))
            continue

        values.extend(str(value) for value in nested)

    return values


class RunSource(StrEnum):
    """How a run index row was produced: a filesystem scan of the bundles, or the Driver reporting live."""

    SCAN = "scan"
    """Read from the bundle on disk by an index pass."""

    DRIVER = "driver"
    """Recorded by the Driver as it wrote the bundle."""


class SweepState(StrEnum):
    """The lifecycle states a sweep point moves through; each transition is one sweep event row."""

    QUEUED = "queued"
    """Waiting for a worker."""

    RUNNING = "running"
    """Being solved by a worker."""

    DONE = "done"
    """Finished successfully."""

    FAILED = "failed"
    """Finished with an error; its event carries the traceback."""

    SKIPPED = "skipped"
    """Already stored when the sweep started, so never queued."""


class ConvergenceLoop(StrEnum):
    """Which Elmer iteration loop a convergence sample belongs to: nonlinear or steady state."""

    NS = "NS"
    """The nonlinear inner loop."""

    SS = "SS"
    """The steady-state outer loop."""


class LogColumn(StrEnum):
    """The log table's columns; the sink emits these names and every query selects by them."""

    DATE = "date"
    """The UTC day the record was logged on; a partition key."""

    PROCESS = "process"
    """The sanitized name of the process that wrote the record; a partition key."""

    TIME = "time"
    """When the record was logged, in UTC."""

    LEVEL = "level"
    """The record's level name, such as ``INFO``."""

    LEVEL_NO = "level_no"
    """The record's numeric severity."""

    NAME = "name"
    """The module that logged the record."""

    FUNCTION = "function"
    """The function that logged the record."""

    LINE = "line"
    """The source line that logged the record."""

    MESSAGE = "message"
    """The formatted message."""

    RUN_ID = "run_id"
    """The run the record was bound to; null outside a run."""

    SWEEP_ID = "sweep_id"
    """The sweep the record was bound to; null outside a sweep."""

    POINT_KEY = "point_key"
    """The sweep point the record was bound to; null outside a sweep."""

    HOST = "host"
    """The machine that wrote the record."""

    PID = "pid"
    """The id of the process that wrote the record."""

    THREAD = "thread"
    """The name of the thread that logged the record."""

    EXCEPTION = "exception"
    """The formatted traceback of the exception the record carried; null when it carried none."""

    EXTRA = "extra"
    """Every other bound key as JSON; null when there are none."""


LOG_SCHEMA = pl.Schema(
    {
        LogColumn.DATE: pl.Date,
        LogColumn.PROCESS: pl.String,
        LogColumn.TIME: TIMESTAMP,
        LogColumn.LEVEL: pl.Enum(categories(LogLevel)),
        LogColumn.LEVEL_NO: pl.Int16,
        LogColumn.NAME: pl.String,
        LogColumn.FUNCTION: pl.String,
        LogColumn.LINE: pl.Int32,
        LogColumn.MESSAGE: pl.String,
        LogColumn.RUN_ID: pl.String,
        LogColumn.SWEEP_ID: pl.String,
        LogColumn.POINT_KEY: pl.String,
        LogColumn.HOST: pl.String,
        LogColumn.PID: pl.Int32,
        LogColumn.THREAD: pl.String,
        LogColumn.EXCEPTION: pl.String,
        LogColumn.EXTRA: pl.String,
    }
)
"""One row per log record; ``extra`` is the JSON of everything bound that has no column of its own."""

LOG_PARTITIONS = (LogColumn.DATE, LogColumn.PROCESS)
"""The log table's partition columns: one directory per day, then per process."""


class RunColumn(StrEnum):
    """The run index's columns: one row per run bundle, keyed by ``run_id``."""

    MONTH = "month"
    """The first day of the month the run was created in; the partition key."""

    RUN_ID = "run_id"
    """The run's bundle directory name."""

    CACHE_KEY = "cache_key"
    """The full content hash of the run's result-affecting inputs."""

    RUNS_DIR = "runs_dir"
    """The runs directory holding the bundle."""

    STATUS = "status"
    """The manifest's lifecycle status."""

    CREATED_AT = "created_at"
    """When the run was assembled."""

    FINISHED_AT = "finished_at"
    """When the run's latest stage finished; null before any has."""

    CURRENT_STAGE = "current_stage"
    """The stage running when the row was read; null when none was."""

    STAGES_DONE = "stages_done"
    """How many stages had finished when the row was read."""

    GIT_SHA = "git_sha"
    """The commit of the checkout the run was made in, with ``-dirty`` when it had uncommitted changes."""

    ELMER_VERSION = "elmer_version"
    """The ElmerSolver version parsed from the solver log."""

    PHYSICS = "physics"
    """The physics preset the run solved."""

    ELEMENT_COUNT = "element_count"
    """Total number of volume elements in the solved mesh."""

    MIN_ELEMENT_SIZE = "min_element_size"
    """`h_min` `[m]`: Smallest element edge length."""

    MAX_ELEMENT_SIZE = "max_element_size"
    """`h_max` `[m]`: Largest element edge length."""

    DESCRIPTION = "description"
    """The description in the run's metadata."""

    TAGS = "tags"
    """The tags in the run's metadata, as a JSON object."""

    NOTES = "notes"
    """The manifest's free-form provenance note."""

    MESH_NAME = "mesh_name"
    """The mesh artifact's store directory name."""

    MESH_KEY = "mesh_key"
    """The mesh artifact's content key."""

    PUBLISHED = "published"
    """Whether the bundle was published to the artifact store."""

    HAS_RESULT = "has_result"
    """Whether the bundle holds a readable ``result.json``."""

    HAS_VTU = "has_vtu"
    """Whether the bundle holds a result VTU file."""

    CLEANUPS = "cleanups"
    """How many retention passes the manifest records."""

    BUNDLE_BYTES = "bundle_bytes"
    """The bundle directory's total size in bytes."""

    SWEEP_ID = "sweep_id"
    """The sweep the run was solved under; null outside a sweep."""

    POINT_KEY = "point_key"
    """The sweep point the run was solved for; null outside a sweep."""

    MANIFEST_MTIME = "manifest_mtime"
    """The manifest's modification time when the row was read."""

    INDEXED_AT = "indexed_at"
    """When the row was written; the latest row per run wins."""

    SOURCE = "source"
    """How the row was produced."""

    MODEL_DIM = "model_dim"
    """The mesh dimension the run's frame solves on: 2 or 3."""

    ARTIFACT_KEYS = "artifact_keys"
    """The content keys of the store artifacts the run consumed."""

    FORCE_Y = "force_y"
    """`F_y` `[N]`: Y component of the net force on the moving array."""

    TEMPERATURE_MAX = "temperature_max"
    """`T_max` `[K]`: Peak temperature in the domain."""

    WALL_TIME = "wall_time"
    """`t_wall` `[s]`: Solver wall-clock time."""


RUN_INDEX_SCHEMA = pl.Schema(
    {
        RunColumn.MONTH: pl.Date,
        RunColumn.RUN_ID: pl.String,
        RunColumn.CACHE_KEY: pl.String,
        RunColumn.RUNS_DIR: pl.String,
        RunColumn.STATUS: pl.Enum(literal_categories(ManifestStatus)),
        RunColumn.CREATED_AT: TIMESTAMP,
        RunColumn.FINISHED_AT: TIMESTAMP,
        RunColumn.CURRENT_STAGE: pl.String,
        RunColumn.STAGES_DONE: pl.Int32,
        RunColumn.GIT_SHA: pl.String,
        RunColumn.ELMER_VERSION: pl.String,
        RunColumn.PHYSICS: pl.String,
        RunColumn.ELEMENT_COUNT: pl.Int64,
        RunColumn.MIN_ELEMENT_SIZE: pl.Float64,
        RunColumn.MAX_ELEMENT_SIZE: pl.Float64,
        RunColumn.DESCRIPTION: pl.String,
        RunColumn.TAGS: pl.String,
        RunColumn.NOTES: pl.String,
        RunColumn.MESH_NAME: pl.String,
        RunColumn.MESH_KEY: pl.String,
        RunColumn.PUBLISHED: pl.Boolean,
        RunColumn.HAS_RESULT: pl.Boolean,
        RunColumn.HAS_VTU: pl.Boolean,
        RunColumn.CLEANUPS: pl.Int32,
        RunColumn.BUNDLE_BYTES: pl.Int64,
        RunColumn.SWEEP_ID: pl.String,
        RunColumn.POINT_KEY: pl.String,
        RunColumn.MANIFEST_MTIME: TIMESTAMP,
        RunColumn.INDEXED_AT: TIMESTAMP,
        RunColumn.SOURCE: pl.Enum(categories(RunSource)),
        RunColumn.MODEL_DIM: pl.Int8,
        RunColumn.ARTIFACT_KEYS: pl.List(pl.String),
        RunColumn.FORCE_Y: pl.Float64,
        RunColumn.TEMPERATURE_MAX: pl.Float64,
        RunColumn.WALL_TIME: pl.Float64,
    }
)
"""One row per run bundle; the index re-reads a bundle only when ``manifest_mtime`` moves, and dedupes on ``run_id``."""

RUN_INDEX_PARTITIONS = (RunColumn.MONTH,)
"""The run index's partition column: one directory per creation month."""


class LiveRunColumn(StrEnum):
    """The columns of the in-flight run table built from the run lock owners; never persisted."""

    RUN_ID = "run_id"
    """The run the claim is held on."""

    HOST = "host"
    """The machine holding the claim."""

    PID = "pid"
    """The id of the process holding the claim."""

    STARTED_AT = "started_at"
    """When the claim was taken."""

    HEARTBEAT_AT = "heartbeat_at"
    """When the holder last refreshed the claim."""

    HEARTBEAT_AGE_S = "heartbeat_age_s"
    """`t_beat` `[s]`: Time since the holder last refreshed the claim."""

    STALE = "stale"
    """Whether the claim has gone without a heartbeat long enough for the next claimant to break it."""


LIVE_RUN_SCHEMA = pl.Schema(
    {
        LiveRunColumn.RUN_ID: pl.String,
        LiveRunColumn.HOST: pl.String,
        LiveRunColumn.PID: pl.Int32,
        LiveRunColumn.STARTED_AT: TIMESTAMP,
        LiveRunColumn.HEARTBEAT_AT: TIMESTAMP,
        LiveRunColumn.HEARTBEAT_AGE_S: pl.Float64,
        LiveRunColumn.STALE: pl.Boolean,
    }
)
"""Built per call from the ``owner.json`` of each ``runs/<run_id>.lock``, so it is always current rather than stored."""


class ArtifactColumn(StrEnum):
    """The artifact index's columns: one row per stored artifact, keyed by ``key``."""

    KIND = "kind"
    """The artifact kind name; the partition key."""

    KEY = "key"
    """The content key the artifact is stored under."""

    NAME = "name"
    """The ``<adjective>-<noun>-<hash8>`` store directory name."""

    STORE_ROOT = "store_root"
    """The root of the store holding the artifact."""

    CREATED_AT = "created_at"
    """When the artifact was published."""

    SPEC_TYPE = "spec_type"
    """``module:QualName`` of the spec model."""

    PRODUCER_VERSION = "producer_version"
    """The kind's producer version at publish time."""

    PRODUCER_HOST = "producer_host"
    """The machine that produced the artifact."""

    PRODUCER_PID = "producer_pid"
    """The id of the process that produced the artifact."""

    FILE_COUNT = "file_count"
    """How many files the artifact holds."""

    TOTAL_BYTES = "total_bytes"
    """The total size of the artifact's files in bytes."""

    FILES = "files"
    """The artifact-relative names of its files."""

    SPEC = "spec"
    """The hashed spec payload, as JSON."""

    FEMFORGE = "femforge"
    """The femforge release that produced the artifact."""

    CODE_SHA = "code_sha"
    """The producing checkout's commit."""

    GMSH = "gmsh"
    """The gmsh version, when meshing was involved."""

    ELMER = "elmer"
    """The ElmerSolver version, when a solve was involved."""

    DIR_MTIME = "dir_mtime"
    """The artifact directory's modification time when the row was read."""

    INDEXED_AT = "indexed_at"
    """When the row was written; the latest row per artifact wins."""


ARTIFACT_INDEX_SCHEMA = pl.Schema(
    {
        ArtifactColumn.KIND: pl.String,
        ArtifactColumn.KEY: pl.String,
        ArtifactColumn.NAME: pl.String,
        ArtifactColumn.STORE_ROOT: pl.String,
        ArtifactColumn.CREATED_AT: TIMESTAMP,
        ArtifactColumn.SPEC_TYPE: pl.String,
        ArtifactColumn.PRODUCER_VERSION: pl.Int32,
        ArtifactColumn.PRODUCER_HOST: pl.String,
        ArtifactColumn.PRODUCER_PID: pl.Int32,
        ArtifactColumn.FILE_COUNT: pl.Int32,
        ArtifactColumn.TOTAL_BYTES: pl.Int64,
        ArtifactColumn.FILES: pl.List(pl.String),
        ArtifactColumn.SPEC: pl.String,
        ArtifactColumn.FEMFORGE: pl.String,
        ArtifactColumn.CODE_SHA: pl.String,
        ArtifactColumn.GMSH: pl.String,
        ArtifactColumn.ELMER: pl.String,
        ArtifactColumn.DIR_MTIME: TIMESTAMP,
        ArtifactColumn.INDEXED_AT: TIMESTAMP,
    }
)
"""One row per ``artifact.json`` sidecar; the tool-version columns mirror ``ToolVersions``."""

ARTIFACT_INDEX_PARTITIONS = (ArtifactColumn.KIND,)
"""The artifact index's partition column: one directory per kind."""


class SweepColumn(StrEnum):
    """The sweep table's columns: one row per sweep launch."""

    MONTH = "month"
    """The first day of the month the sweep started in; the partition key."""

    SWEEP_ID = "sweep_id"
    """The sweep's id."""

    DESCRIPTION = "description"
    """What the sweep runs, as its launcher described it."""

    STARTED_AT = "started_at"
    """When the sweep started."""

    FINISHED_AT = "finished_at"
    """When the sweep finished; null on its launch row."""

    TOTAL = "total"
    """How many points the sweep holds, skipped ones included."""

    SKIPPED = "skipped"
    """How many points were already stored and did not run."""

    MAX_WORKERS = "max_workers"
    """How many worker processes run the points."""

    HOST = "host"
    """The machine that launched the sweep."""

    PID = "pid"
    """The id of the process that launched the sweep."""

    PROCESS = "process"
    """The sanitized name of the process that launched the sweep."""

    GIT_SHA = "git_sha"
    """The commit of the checkout the sweep ran in."""

    INDEXED_AT = "indexed_at"
    """When the row was written; the closing row supersedes the launch row."""


SWEEP_RUN_SCHEMA = pl.Schema(
    {
        SweepColumn.MONTH: pl.Date,
        SweepColumn.SWEEP_ID: pl.String,
        SweepColumn.DESCRIPTION: pl.String,
        SweepColumn.STARTED_AT: TIMESTAMP,
        SweepColumn.FINISHED_AT: TIMESTAMP,
        SweepColumn.TOTAL: pl.Int32,
        SweepColumn.SKIPPED: pl.Int32,
        SweepColumn.MAX_WORKERS: pl.Int32,
        SweepColumn.HOST: pl.String,
        SweepColumn.PID: pl.Int32,
        SweepColumn.PROCESS: pl.String,
        SweepColumn.GIT_SHA: pl.String,
        SweepColumn.INDEXED_AT: TIMESTAMP,
    }
)
"""One row per sweep; a row is rewritten (and deduped on ``sweep_id``) when the sweep finishes."""

SWEEP_RUN_PARTITIONS = (SweepColumn.MONTH,)
"""The sweep table's partition column: one directory per start month."""


class SweepEventColumn(StrEnum):
    """The sweep event table's columns: one row per point state transition."""

    SWEEP_ID = "sweep_id"
    """The sweep the point belongs to; the partition key."""

    POINT_KEY = "point_key"
    """The point's key within the sweep."""

    POINT_NAME = "point_name"
    """A readable label for the point."""

    STATE = "state"
    """The state the point entered."""

    AT = "at"
    """When the point entered the state."""

    HOST = "host"
    """The machine that recorded the transition."""

    PID = "pid"
    """The id of the process that recorded the transition."""

    ORIGIN = "origin"
    """Where a finished point's result came from, such as an artifact origin."""

    ERROR = "error"
    """The traceback a failed point recorded."""

    DURATION_S = "duration_s"
    """`t_point` `[s]`: How long the point took up to this transition."""


SWEEP_EVENT_SCHEMA = pl.Schema(
    {
        SweepEventColumn.SWEEP_ID: pl.String,
        SweepEventColumn.POINT_KEY: pl.String,
        SweepEventColumn.POINT_NAME: pl.String,
        SweepEventColumn.STATE: pl.Enum(categories(SweepState)),
        SweepEventColumn.AT: TIMESTAMP,
        SweepEventColumn.HOST: pl.String,
        SweepEventColumn.PID: pl.Int32,
        SweepEventColumn.ORIGIN: pl.String,
        SweepEventColumn.ERROR: pl.String,
        SweepEventColumn.DURATION_S: pl.Float64,
    }
)
"""Append-only point transitions; the latest state per ``point_key`` is the sweep's live progress."""

SWEEP_EVENT_PARTITIONS = (SweepEventColumn.SWEEP_ID,)
"""The sweep event table's partition column: one directory per sweep."""


class ConvergenceColumn(StrEnum):
    """The keys ``elmer.runtime.solve`` writes into ``convergence.jsonl``, one sample per solver iteration."""

    WALL_TIME = "wall_time"
    """`t_wall` `[s]`: Time since the parser started reading the solver output."""

    SOLVER = "solver"
    """The sif ``Equation`` name Elmer tags the sample with, lowercased."""

    LOOP = "loop"
    """Which loop the sample belongs to."""

    ITER = "iter"
    """One-based iteration count within the loop."""

    NRM = "nrm"
    """`‖u‖₂` `[?]`: The solution's L₂ norm at this iteration."""

    RELC = "relc"
    """`Δ_rel` `[-]`: Relative change of the norm from the previous iteration."""


CONVERGENCE_SCHEMA = pl.Schema(
    {
        ConvergenceColumn.WALL_TIME: pl.Float64,
        ConvergenceColumn.SOLVER: pl.String,
        ConvergenceColumn.LOOP: pl.Enum(categories(ConvergenceLoop)),
        ConvergenceColumn.ITER: pl.Int32,
        ConvergenceColumn.NRM: pl.Float64,
        ConvergenceColumn.RELC: pl.Float64,
    }
)
"""Read straight from a bundle's ``convergence.jsonl``; per-run and never centralised, so it has no partitions."""
