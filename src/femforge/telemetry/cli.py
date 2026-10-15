"""The telemetry half of the ``femforge`` command: build the indexes, list runs and sweeps, query the logs, compact the store."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, cast

import polars as pl
from rich.table import Table

from femforge.artifacts.local import LocalArtifactStore
from femforge.common.cli import CliError
from femforge.common.log import TELEMETRY_ENV
from femforge.telemetry.artifacts import ArtifactIndex
from femforge.telemetry.hive import SETTLE_S
from femforge.telemetry.journal import journal_glob
from femforge.telemetry.paths import Telemetry, TelemetryDir, TelemetryRootMissing, require_root, resolve_root
from femforge.telemetry.runs import RunIndex, RunQuery
from femforge.telemetry.schemas import ArtifactColumn, LiveRunColumn, LogColumn, RunColumn, SweepColumn, SweepEventColumn
from femforge.telemetry.sink import LogQuery
from femforge.telemetry.sweep import SweepQuery, sweep_runs
from femforge.telemetry.tail import TextTail


if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import Console


__all__ = ["TelemetryCommand", "add_arguments", "indexed_artifacts", "run"]

DEFAULT_LIMIT = 50
"""How many rows a listing prints when ``--limit`` is not given."""

FOLLOW_INTERVAL_S = 0.5
"""`t` `[s]`: How often ``logs tail --follow`` re-reads the live journals."""

ROOT_HELP = f"The telemetry root (default: ${TELEMETRY_ENV})."
"""The ``--telemetry`` help text every subcommand shares."""


class TelemetryCommand(StrEnum):
    """The ``femforge`` subcommands this module owns; the entry point dispatches on membership."""

    INDEX = "index"
    RUNS = "runs"
    LOGS = "logs"
    SWEEPS = "sweeps"
    TELEMETRY = "telemetry"


class IndexAction(StrEnum):
    """Whether an index pass re-reads everything or only what moved since the last one."""

    BUILD = "build"
    UPDATE = "update"


class ListingAction(StrEnum):
    """The second-level verbs of the listing and maintenance commands, named once for the parser and once for the dispatch."""

    LS = "ls"
    REAP = "reap"
    SHOW = "show"
    QUERY = "query"
    TAIL = "tail"
    COMPACT = "compact"


RUN_COLUMNS = (
    RunColumn.RUN_ID,
    RunColumn.STATUS,
    RunColumn.CREATED_AT,
    RunColumn.PHYSICS,
    RunColumn.DESCRIPTION,
    RunColumn.ELEMENT_COUNT,
    RunColumn.WALL_TIME,
    RunColumn.TEMPERATURE_MAX,
    RunColumn.FORCE_Y,
)
"""What ``runs ls`` shows; ``--live`` appends the lock-owner columns."""

LIVE_COLUMNS = (RunColumn.CURRENT_STAGE, LiveRunColumn.PID, LiveRunColumn.HEARTBEAT_AGE_S, LiveRunColumn.STALE)
"""The in-flight columns ``--live`` appends to the listing."""

REAP_COLUMNS = (LiveRunColumn.RUN_ID, LiveRunColumn.HOST, LiveRunColumn.PID, LiveRunColumn.HEARTBEAT_AT, RunColumn.STATUS)
"""What ``runs reap`` shows for each claim it broke."""

LOG_COLUMNS = (LogColumn.TIME, LogColumn.LEVEL, LogColumn.PROCESS, LogColumn.RUN_ID, LogColumn.MESSAGE)
"""What ``logs query`` shows."""

SWEEP_COLUMNS = (SweepColumn.SWEEP_ID, SweepColumn.DESCRIPTION, SweepColumn.STARTED_AT, SweepColumn.FINISHED_AT, SweepColumn.TOTAL, SweepColumn.MAX_WORKERS, SweepColumn.PROCESS)
"""What ``sweeps ls`` shows."""

POINT_COLUMNS = (SweepEventColumn.POINT_KEY, SweepEventColumn.POINT_NAME, SweepEventColumn.STATE, SweepEventColumn.AT, SweepEventColumn.DURATION_S, SweepEventColumn.ORIGIN)
"""What ``sweeps show`` lists for each point."""

ARTIFACT_COLUMNS = (ArtifactColumn.KIND, ArtifactColumn.NAME, ArtifactColumn.KEY, ArtifactColumn.CREATED_AT, ArtifactColumn.FILE_COUNT, ArtifactColumn.TOTAL_BYTES, ArtifactColumn.SPEC_TYPE)
"""What ``artifacts ls`` shows when it lists from the index."""


def telemetry_root(explicit: str | None) -> Path:
    """The telemetry root a command works on: ``--telemetry``, else the environment, else a failure naming both."""
    try:
        return require_root(explicit)
    except TelemetryRootMissing as error:
        raise CliError(str(error)) from error


def cell(value: object) -> str:
    """One frame cell as table text: a blank for null, minutes for a timestamp, three significant digits for a float."""
    if value is None:
        return ""

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")

    if isinstance(value, float):
        return f"{value:.3g}"

    return str(value)


def frame_table(frame: pl.DataFrame, columns: Sequence[str], *, title: str) -> Table:
    """A rich table of the named frame columns, in frame order.

    Args:
        frame: The rows to show.
        columns: The frame columns to show, left to right.
        title: The table title.
    """
    table = Table(title=title)
    for column in columns:
        table.add_column(str(column))

    for row in cast("list[dict[str, object]]", frame.rows(named=True)):
        table.add_row(*(cell(row[column]) for column in columns))

    return table


def add_arguments(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register every telemetry subcommand on the ``femforge`` parser."""

    def with_root(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        _ = parser.add_argument("--telemetry", default=None, help=ROOT_HELP)

        return parser

    index = commands.add_parser(TelemetryCommand.INDEX, help="Build or refresh the run and artifact indexes.")
    index_actions = index.add_subparsers(dest="action", required=True)
    for action in IndexAction:
        scope: str = "Re-read every bundle." if action is IndexAction.BUILD else "Read only the bundles whose manifest moved."
        pass_over = with_root(index_actions.add_parser(action, help=scope))
        _ = pass_over.add_argument("--runs-dir", required=True, help="The runs directory holding the bundles.")
        _ = pass_over.add_argument("--store", default=None, help="Also index the artifact store rooted here.")

    runs = commands.add_parser(TelemetryCommand.RUNS, help="List indexed runs.")
    run_actions = runs.add_subparsers(dest="action", required=True)
    listing = with_root(run_actions.add_parser(ListingAction.LS, help="List runs newest first."))
    _ = listing.add_argument(
        "--status",
        nargs="*",
        default=None,
        help="Only runs in these statuses.",
    )
    _ = listing.add_argument(
        "--tag",
        action="append",
        default=None,
        metavar="K=V",
        help="Only runs carrying this metadata tag (repeatable).",
    )
    _ = listing.add_argument("--text", default=None, help="Only runs whose id, description or notes contain this.")
    _ = listing.add_argument(
        "--since",
        type=float,
        default=None,
        metavar="DAYS",
        help="Only runs created within this many days.",
    )
    _ = listing.add_argument("--sweep", default=None, help="Only runs solved under this sweep.")
    _ = listing.add_argument("--live", action="store_true", help="Also show which runs are claimed right now.")
    _ = listing.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="How many rows to print.")
    reap = with_root(run_actions.add_parser(ListingAction.REAP, help="Break the claims of runs whose process died, mark the bundles they left running as abandoned, and re-index them."))
    _ = reap.add_argument("--runs-dir", required=True, help="The runs directory holding the claims.")

    logs = commands.add_parser(TelemetryCommand.LOGS, help="Query the stored log records or tail the live ones.")
    log_actions = logs.add_subparsers(dest="action", required=True)
    query = with_root(log_actions.add_parser(ListingAction.QUERY, help="Search the parquet log store."))
    _ = query.add_argument("--level", nargs="*", default=None, help="Only these levels.")
    _ = query.add_argument("--process", nargs="*", default=None, help="Only these process names.")
    _ = query.add_argument("--run", default=None, help="Only records bound to this run.")
    _ = query.add_argument("--sweep", default=None, help="Only records bound to this sweep.")
    _ = query.add_argument(
        "--since",
        type=float,
        default=None,
        metavar="DAYS",
        help="Only records from within this many days.",
    )
    _ = query.add_argument("--text", default=None, help="Only records whose message contains this.")
    _ = query.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="How many rows to print.")

    tail = with_root(log_actions.add_parser(ListingAction.TAIL, help="Print the live journals, which hold what parquet has not settled yet."))
    _ = tail.add_argument("--follow", action="store_true", help="Keep reading until interrupted.")

    sweeps = commands.add_parser(TelemetryCommand.SWEEPS, help="List sweeps or show one sweep's progress.")
    sweep_actions = sweeps.add_subparsers(dest="action", required=True)
    _ = with_root(sweep_actions.add_parser(ListingAction.LS, help="List sweeps newest first."))
    show = with_root(sweep_actions.add_parser(ListingAction.SHOW, help="Show one sweep's counts, points and failures."))
    _ = show.add_argument("sweep_id", help="The sweep to show.")

    telemetry = commands.add_parser(TelemetryCommand.TELEMETRY, help="Maintain the telemetry store.")
    telemetry_actions = telemetry.add_subparsers(dest="action", required=True)
    compact = with_root(telemetry_actions.add_parser(ListingAction.COMPACT, help="Fold each table's settled shards into one shard per partition."))
    _ = compact.add_argument(
        "--settle",
        type=float,
        default=SETTLE_S,
        metavar="SECONDS",
        help="How long a shard must be untouched before it is folded.",
    )


def run_index(args: argparse.Namespace, console: Console) -> int:
    """``femforge index build|update``: refresh the run index, and the artifact index too when a store is named."""
    telemetry = Telemetry(telemetry_root(cast("str | None", args.telemetry)))
    rebuild: bool = cast("str", args.action) == IndexAction.BUILD

    runs_dir = Path(cast("str", args.runs_dir))
    started: float = time.monotonic()
    indexed: int = RunIndex(telemetry, runs_dir).update(rebuild=rebuild)
    console.print(f"{indexed} run rows from {runs_dir} in {time.monotonic() - started:.1f}s")

    store_root = cast("str | None", args.store)
    if store_root is not None:
        started = time.monotonic()
        artifacts: int = ArtifactIndex(telemetry, LocalArtifactStore(store_root)).update(rebuild=rebuild)
        console.print(f"{artifacts} artifact rows from {store_root} in {time.monotonic() - started:.1f}s")

    return 0


def list_runs(args: argparse.Namespace, console: Console) -> int:
    """``femforge runs ls``: the indexed runs a filter selects, newest first."""
    telemetry = Telemetry(telemetry_root(cast("str | None", args.telemetry)))
    query = RunQuery(telemetry.runs.scan())

    statuses = cast("list[str] | None", args.status)
    if statuses:
        query = query.status(*statuses)

    for pair in cast("list[str] | None", args.tag) or []:
        key, _, value = pair.partition("=")
        query = query.tags(**{key: value})

    needle = cast("str | None", args.text)
    if needle is not None:
        query = query.text(needle)

    days = cast("float | None", args.since)
    if days is not None:
        query = query.since(datetime.now(timezone.utc) - timedelta(days=days))

    sweep_id = cast("str | None", args.sweep)
    if sweep_id is not None:
        query = query.sweep(sweep_id)

    live: bool = cast("bool", args.live)
    if live:
        query = query.with_live()

    frame: pl.DataFrame = query.newest(cast("int", args.limit)).frame()
    columns = (*RUN_COLUMNS, *LIVE_COLUMNS) if live else RUN_COLUMNS
    console.print(frame_table(frame, columns, title=f"{frame.height} runs in {telemetry.root}"))

    return 0


def reap_claims(args: argparse.Namespace, console: Console) -> int:
    """``femforge runs reap``: break the silent claims under a runs directory, mark their bundles abandoned, and re-index what changed."""
    # Imported here: femforge.run pulls in the whole solve stack, which a listing never needs.
    from femforge.run.reap import reap_runs

    telemetry = Telemetry(telemetry_root(cast("str | None", args.telemetry)))
    runs_dir = Path(cast("str", args.runs_dir))

    reaped = reap_runs(runs_dir)
    indexed: int = RunIndex(telemetry, runs_dir).update()

    table = Table(title=f"{len(reaped)} claims reaped, {indexed} rows re-indexed")
    for column in REAP_COLUMNS:
        table.add_column(str(column))

    for run in reaped:
        owner = run.owner
        table.add_row(
            run.run_id,
            cell(owner.host if owner is not None else None),
            cell(owner.pid if owner is not None else None),
            cell(owner.heartbeat_at if owner is not None else None),
            cell(run.status),
        )

    console.print(table)

    return 0


def query_logs(args: argparse.Namespace, console: Console) -> int:
    """``femforge logs query``: the stored records a filter selects, oldest first."""
    telemetry = Telemetry(telemetry_root(cast("str | None", args.telemetry)))
    query = LogQuery.over(telemetry)

    levels = cast("list[str] | None", args.level)
    if levels:
        query = query.levels(*levels)

    processes = cast("list[str] | None", args.process)
    if processes:
        query = query.processes(*processes)

    run_id = cast("str | None", args.run)
    if run_id is not None:
        query = query.run(run_id)

    sweep_id = cast("str | None", args.sweep)
    if sweep_id is not None:
        query = query.sweep(sweep_id)

    days = cast("float | None", args.since)
    if days is not None:
        query = query.since(datetime.now(timezone.utc) - timedelta(days=days))

    needle = cast("str | None", args.text)
    if needle is not None:
        query = query.text(needle)

    frame: pl.DataFrame = query.page(limit=cast("int", args.limit))
    console.print(frame_table(frame, LOG_COLUMNS, title=f"{frame.height} records in {telemetry.root}"))

    return 0


def journal_line(text: str) -> str:
    """One journalled log record rendered as a terminal line; an unparseable line is printed as it stands."""
    try:
        record = cast("dict[str, object]", json.loads(text))
    except ValueError:
        return text

    when = record.get(LogColumn.TIME)
    stamp: str = when[11:19] if isinstance(when, str) else ""
    run_id = record.get(LogColumn.RUN_ID)
    tag: str = f" [{run_id}]" if isinstance(run_id, str) else ""

    return f"{stamp} {cell(record.get(LogColumn.LEVEL)):<8}{tag} {cell(record.get(LogColumn.MESSAGE))}"


def tail_logs(args: argparse.Namespace, console: Console) -> int:
    """``femforge logs tail``: the live journals, which hold the records parquet has not settled yet."""
    telemetry = Telemetry(telemetry_root(cast("str | None", args.telemetry)))
    pattern: str = journal_glob(TelemetryDir.LOGS)
    tails: list[TextTail] = []

    def drain() -> None:
        known = {tail.path for tail in tails}
        tails.extend(TextTail(path) for path in sorted(telemetry.live.glob(pattern)) if path not in known)

        for tail in tails:
            for line in tail.read_new():
                # markup off: a log message is text, and a bracketed run id would otherwise read as a Rich tag.
                console.print(journal_line(line), highlight=False, markup=False)

    drain()

    while cast("bool", args.follow):  # pragma: no cover - runs until the reader interrupts it
        time.sleep(FOLLOW_INTERVAL_S)
        drain()

    return 0


def summary_table(query: SweepQuery) -> Table:
    """One sweep's counts, throughput and ETA as a two-column table."""
    summary = query.summary()

    table = Table(title=f"Sweep {query.sweep_id}")
    table.add_column("Field")
    table.add_column("Value", justify="right")

    for name, value in cast("dict[str, object]", vars(summary)).items():
        table.add_row(name, cell(value))

    return table


def show_sweeps(args: argparse.Namespace, console: Console) -> int:
    """``femforge sweeps ls|show``: every sweep the root knows, or one sweep's points and failures."""
    telemetry = Telemetry(telemetry_root(cast("str | None", args.telemetry)))

    if cast("str", args.action) == ListingAction.LS:
        frame: pl.DataFrame = sweep_runs(telemetry)
        console.print(frame_table(frame, SWEEP_COLUMNS, title=f"{frame.height} sweeps in {telemetry.root}"))

        return 0

    query = SweepQuery(telemetry, cast("str", args.sweep_id))
    console.print(summary_table(query))
    console.print(frame_table(query.latest(), POINT_COLUMNS, title="Points"))

    failures: pl.DataFrame = query.failures()
    if failures.height:
        console.print(frame_table(failures, (SweepEventColumn.POINT_KEY, SweepEventColumn.ERROR), title="Failures"))

    return 0


def compact_store(args: argparse.Namespace, console: Console) -> int:
    """``femforge telemetry compact``: fold every table's settled shards into one shard per partition."""
    telemetry = Telemetry(telemetry_root(cast("str | None", args.telemetry)))
    settle: float = cast("float", args.settle)

    table = Table(title=f"Compacted {telemetry.root}")
    table.add_column("Table")
    table.add_column("Shards", justify="right")
    table.add_column("Merged", justify="right")

    for store in telemetry.stores():
        merged = store.compact(settle_s=settle)
        table.add_row(store.root.name, str(len(store.shards())), str(len(merged)))

    console.print(table)

    return 0


def indexed_artifacts(store: LocalArtifactStore, kind: str | None, explicit: str | None) -> Table | None:
    """``femforge artifacts ls`` off the index, or None when no telemetry root resolves or the index holds nothing yet.

    Args:
        store: The artifact store whose indexed artifacts to list.
        kind: Only this kind; None lists every kind.
        explicit: The ``--telemetry`` root; None falls back to the environment.
    """
    root: Path | None = resolve_root(explicit)
    if root is None:
        return None

    query = ArtifactIndex(Telemetry(root), store).query()
    frame: pl.DataFrame = (query.kind(kind) if kind is not None else query).frame()
    if frame.height == 0:
        return None

    return frame_table(frame, ARTIFACT_COLUMNS, title=f"{frame.height} artifacts indexed under {store.root}")


def run(args: argparse.Namespace, console: Console) -> int:
    """Dispatch one telemetry subcommand."""
    command: str = cast("str", args.command)
    action: str = cast("str", args.action)

    if command == TelemetryCommand.INDEX:
        return run_index(args, console)

    if command == TelemetryCommand.RUNS:
        return reap_claims(args, console) if action == ListingAction.REAP else list_runs(args, console)

    if command == TelemetryCommand.LOGS:
        return query_logs(args, console) if action == ListingAction.QUERY else tail_logs(args, console)

    if command == TelemetryCommand.SWEEPS:
        return show_sweeps(args, console)

    return compact_store(args, console)
