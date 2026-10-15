# Telemetry

`femforge.telemetry` is the process-wide record of what the pipeline did: every log record, every run, every stored artifact and every sweep point, kept as hive-partitioned parquet under one root. A run bundle describes one run; telemetry answers questions across thousands of them without opening each bundle. This page covers the store, its tables, the journal that makes it live, the log sink, the indexes, sweep progress and convergence files, and the rules a consumer may rely on. [Telemetry](../../user-guide/telemetry.md) in the user guide covers turning it on and reading it back.

## The root

`resolve_root` is the on and off switch: an explicit path, else `FEMFORGE_TELEMETRY`, else None, and None means telemetry is off and nothing is created (`src/femforge/telemetry/paths.py:55-64`). A reader that needs a root calls `require_root`, which raises `TelemetryRootMissing` naming both ways to give one (`src/femforge/telemetry/paths.py:67-73`).

With telemetry off, a process never imports polars. The logging setup and the driver check the explicit root and the variable before they import `femforge.telemetry` (`src/femforge/common/log.py:205-210`, `src/femforge/run/driver.py:365-374`).

[Telemetry][femforge.telemetry.Telemetry] addresses a root and hands out one cached [HiveStore][femforge.telemetry.HiveStore] per table (`src/femforge/telemetry/paths.py:76-115`). `TelemetryDir` names the directories (`src/femforge/telemetry/paths.py:33-52`):

| Directory | Holds | Partitioned by |
|---|---|---|
| `logs/` | the log table | `date`, then `process` |
| `runs/` | the run index | `month` |
| `artifacts/` | the artifact index | `kind` |
| `sweeps/` | a launch row per sweep, plus a closing row once it finishes | `month` |
| `sweep_events/` | one row per sweep point transition | `sweep_id` |
| `live/` | each process's write-ahead journals | not a table |

## The hive store

A [HiveStore][femforge.telemetry.HiveStore] is one append-only table: a typed `scan`, an atomic `append`, and a settling `compact` (`src/femforge/telemetry/hive.py:100-182`). Its schema lists every column, the partition columns included. The partition values live in the directory names, so the shard bodies leave those columns out and a scan types them back through `hive_schema` (`src/femforge/telemetry/hive.py:115-123`, `src/femforge/telemetry/hive.py:145`).

| Operation | Behavior | Where |
|---|---|---|
| `append(frame)` | casts the frame onto the schema and writes one new shard per partition; existing shards stay untouched | `src/femforge/telemetry/hive.py:149-162` |
| a shard write | stages the parquet in a `.tmp` sibling and moves it into place, so a reader never sees half a file | `src/femforge/telemetry/hive.py:53-62` |
| a shard name | `part-<host>-<pid>-<utcstamp>-<seq>.parquet`, unique across hosts, processes and writes, so writers need no lock | `src/femforge/telemetry/hive.py:43-50` |
| `scan()` | a lazy frame over every shard, typed onto the current schema | `src/femforge/telemetry/hive.py:138-147` |
| `compact(settle_s)` | in each partition, folds the shards untouched for `settle_s`, 300 s by default, into one merged shard, then deletes them | `src/femforge/telemetry/hive.py:34-35`, `src/femforge/telemetry/hive.py:164-182` |

Compaction writes the merged shard before it deletes the originals, so the rows exist on disk at every moment, and a reader that lists a partition between the two steps sees them twice.

Columns are typed once, in `src/femforge/telemetry/schemas.py`:

- **Column names are StrEnums.** A query selects `pl.col(LogColumn.LEVEL)`, so the writer and the reader name a column through one symbol.
- **Categories come from existing vocabularies.** `pl.Enum` columns take their categories from `LogLevel`, `ManifestStatus` (a union of `Literal` types, flattened by `literal_categories`), `RunSource`, `SweepState` and `ConvergenceLoop` (`src/femforge/telemetry/schemas.py:45-61`). Identical categories let shards from different processes concatenate without a cast.
- **A vocabulary may grow.** polars refuses to unify Enum columns whose categories differ, so `scan` and `compact` split the shards into consecutive runs that share one parquet schema and type each run before they meet (`src/femforge/telemetry/hive.py:65-67`, `src/femforge/telemetry/hive.py:144-147`). `coerce` fills a column a shard predates with typed nulls, nulls an unknown Enum category instead of raising, and parses ISO date and time strings (`src/femforge/telemetry/hive.py:70-97`).
- **Every timestamp is `Datetime("us", "UTC")`** (`src/femforge/telemetry/schemas.py:41-42`). Open vocabularies, such as bound log fields, run tags and artifact specs, are JSON in a string column.

## Tables and schemas

The tables below are generated from `src/femforge/telemetry/schemas.py` at build time. The meaning column is each member's docstring in the column enum.

### logs

One row per log record; `extra` is the JSON of every bound key without a column of its own.

```python exec="true" session="telemetry"
from enum import StrEnum

import polars as pl

from femforge.common.introspect import attribute_docstrings
from femforge.telemetry import schemas


def kind(dtype: pl.DataType) -> str:
    if isinstance(dtype, pl.Enum):
        return "Enum: " + ", ".join(f"`{category}`" for category in dtype.categories.to_list())

    if isinstance(dtype, pl.Datetime):
        return f"Datetime({dtype.time_unit}, {dtype.time_zone})"

    return str(dtype)


def schema_table(schema: pl.Schema, columns: type[StrEnum], partitions: tuple[str, ...] = ()) -> None:
    meanings = attribute_docstrings(columns)
    print("| Column | Type | Meaning |")
    print("| --- | --- | --- |")
    for column in columns:
        meaning = " ".join(meanings.get(column.name, "").split()).replace("|", "\\|")
        name = f"`{column.value}` (partition)" if column in partitions else f"`{column.value}`"
        print(f"| {name} | {kind(schema[column.value])} | {meaning} |")


schema_table(schemas.LOG_SCHEMA, schemas.LogColumn, schemas.LOG_PARTITIONS)
```

### runs

One row per index pass over a run bundle; the latest row per `run_id` wins.

```python exec="true" session="telemetry"
schema_table(schemas.RUN_INDEX_SCHEMA, schemas.RunColumn, schemas.RUN_INDEX_PARTITIONS)
```

### artifacts

One row per index pass over an `artifact.json` sidecar; the tool-version columns mirror `ToolVersions`.

```python exec="true" session="telemetry"
schema_table(schemas.ARTIFACT_INDEX_SCHEMA, schemas.ArtifactColumn, schemas.ARTIFACT_INDEX_PARTITIONS)
```

### sweeps

A launch row per sweep and a closing row once it finishes, deduplicated on `sweep_id`.

```python exec="true" session="telemetry"
schema_table(schemas.SWEEP_RUN_SCHEMA, schemas.SweepColumn, schemas.SWEEP_RUN_PARTITIONS)
```

### sweep_events

Append-only point transitions; the latest state per `point_key` is the sweep's progress.

```python exec="true" session="telemetry"
schema_table(schemas.SWEEP_EVENT_SCHEMA, schemas.SweepEventColumn, schemas.SWEEP_EVENT_PARTITIONS)
```

### Live runs

Never stored: [live_runs][femforge.telemetry.live_runs] builds these rows on every call from the `owner.json` of each `runs/<run_id>.lock` claim, so liveness is always current (`src/femforge/telemetry/runs.py:187-215`).

```python exec="true" session="telemetry"
schema_table(schemas.LIVE_RUN_SCHEMA, schemas.LiveRunColumn)
```

### Convergence

Read from one bundle's `convergence.jsonl`; per run and never centralized, so it has no partitions.

```python exec="true" session="telemetry"
schema_table(schemas.CONVERGENCE_SCHEMA, schemas.ConvergenceColumn)
```

## The journal

Every log record and every sweep event goes first to a JSONL journal the writing process owns, `live/<table>-<host>-<pid>.jsonl`, and parquet receives it in batches (`src/femforge/telemetry/journal.py:135-178`). The journal is the write-ahead log: a viewer tailing a running sweep sees a record at once, and a killed process loses nothing it wrote.

| Step | Behavior | Where |
|---|---|---|
| write | append one JSON line and flush it, under a lock | `src/femforge/telemetry/journal.py:146-155` |
| checkpoint | read the journal's records, append them to the store, truncate the journal | `src/femforge/telemetry/journal.py:157-168` |
| close | close the handle and delete the empty file | `src/femforge/telemetry/journal.py:170-178` |
| recovery | append the records of every journal whose process id, read from its file name, no longer runs, then delete it | `src/femforge/telemetry/journal.py:97-132` |

A reader tolerates a half-written line. A journal is newline-delimited, so a torn tail costs at most one record (`src/femforge/telemetry/journal.py:28-29`). `journal_lines` keeps only the lines that parse as a JSON object and counts the rest, such as stray console output or a torn tail (`src/femforge/telemetry/journal.py:45-69`), and recovery logs that count as a warning. The kept lines go through `read_ndjson` with `ignore_errors` and `coerce` onto the table's schema (`src/femforge/telemetry/journal.py:87-94`).

Every new [ParquetLogSink][femforge.telemetry.ParquetLogSink] recovers the log journals dead processes left on its root (`src/femforge/telemetry/sink.py:143`), and every [SweepJournal][femforge.telemetry.SweepJournal] does the same for sweep events (`src/femforge/telemetry/sweep.py:88-93`). Live readers union both halves: [scan_logs][femforge.telemetry.scan_logs] concatenates the parquet shards with the journals (`src/femforge/telemetry/sink.py:200-204`), and `SweepQuery.events` does the same for one sweep (`src/femforge/telemetry/sweep.py:187-192`).

## The log sink and loguru

`src/femforge/common/log.py` is the only logging configuration site (`src/femforge/common/log.py:1`). [setup_logging][femforge.common.setup_logging] installs the terminal sink, routes the standard library's `logging` and `warnings` into loguru, gates chatty libraries such as build123d at the source, and installs the parquet sink when a root resolves (`src/femforge/common/log.py:245-298`):

| Setting | Resolution | Where |
|---|---|---|
| terminal level | `level`, else `FEMFORGE_LOG_LEVEL`, else `INFO` | `src/femforge/common/log.py:263` |
| parquet sink level | always `DEBUG`: the store is the searchable record, the terminal only the live view | `src/femforge/common/log.py:231-239` |
| sink order | the parquet sink goes in after `logger.remove()`, which would otherwise drop it | `src/femforge/common/log.py:297-298` |
| a repeat call | adjusts the terminal level, the library gates and the telemetry root, swapping the sink when the root moves | `src/femforge/common/log.py:265-274`, `src/femforge/common/log.py:212-216` |

`ff.log` runs `setup_logging` with the defaults before its first record, so a script that never calls it still gets the terminal sink (`src/femforge/common/log.py:358-375`).

[ParquetLogSink][femforge.telemetry.ParquetLogSink] is a plain loguru sink callable (`src/femforge/telemetry/sink.py:117-197`):

- It writes each record to the journal at once and checkpoints into parquet every 500 records, every 5 s from a timer thread, and at exit through `atexit` (`src/femforge/telemetry/sink.py:44-48`, `src/femforge/telemetry/sink.py:150-197`). The timer and the sink share one re-entrant lock, and `close` is idempotent.
- It never logs into itself. polars and the standard library bridge can emit records from inside a flush, so the flushing thread carries a `FlushGuard` flag and the sink drops what arrives on it (`src/femforge/telemetry/sink.py:111-114`, `src/femforge/telemetry/sink.py:156-157`).
- It promotes `run_id`, `sweep_id` and `point_key` out of the bound fields into columns, so a query can push a filter on them down. Every other bound key becomes JSON in `extra`, with Rich renderables flattened to plain text at 120 columns (`src/femforge/telemetry/sink.py:38-42`, `src/femforge/telemetry/sink.py:68-108`).
- It names the `process` partition after the script, folded to `[a-z0-9_.-]` because the name becomes a directory (`src/femforge/telemetry/sink.py:50-65`).

Two context managers route records:

| Context | Effect | Where |
|---|---|---|
| `telemetry_scope(root)` | binds a route key, so the records inside land in that root's own sink; one sink per folder lives for the process | `src/femforge/common/log.py:378-413` |
| `run_log_context(run_id, dir)` | binds `run_id` and writes every record that carries it, at `DEBUG` and above, to the bundle's `run.log` | `src/femforge/common/log.py:416-446` |

[Driver.evaluate][femforge.run.Driver.evaluate] wraps a whole evaluation in `telemetry_scope(self.telemetry)`, so its records land in the driver's root and not the process default (`src/femforge/run/driver.py:204-205`).

A child process relays its records instead of writing its own. `log_to_stdout_json` replaces the terminal sink with one JSON line per record on stdout (`src/femforge/common/log.py:320-328`). The parent passes each output line to `relay_record`, which logs a JSON record at its own level with the child's module, function and line patched in, and any other line at `DEBUG` (`src/femforge/common/log.py:338-355`). The mesh worker works this way (`src/femforge/meshing/backend.py:240`).

## The run and artifact indexes

An index turns directories of self-describing files into rows, and both indexes follow three rules.

**The watermark lives in the rows.** `update` on [RunIndex][femforge.telemetry.RunIndex] takes one `os.scandir` over the runs directory and one `stat` per bundle, then anti-joins `(run_id, manifest_mtime)` against the rows it holds, so a pass over an unchanged directory parses nothing (`src/femforge/telemetry/runs.py:329-388`). [ArtifactIndex][femforge.telemetry.ArtifactIndex] does the same on `(kind, name, dir_mtime)` and reads a sidecar only for a directory that moved (`src/femforge/telemetry/artifacts.py:134-188`). `update(rebuild=True)` ignores the watermark, which replays a schema change. No catalog file sits beside the rows to keep consistent.

**A manifest is read, never validated.** A changed bundle's manifest is parsed with `json.loads` and a dozen keys are picked out of it, because validating the embedded config would cost more than the rest of the pass (`src/femforge/telemetry/runs.py:91-98`). Every key reads defensively, down to the number in a serialized quantity such as `"370.9 K"` without a unit registry (`src/femforge/telemetry/runs.py:42-88`), so one corrupt bundle costs one null and not the pass. The `month` partition is never null: a manifest without a readable date files under the modification time that got it read (`src/femforge/telemetry/runs.py:148-149`).

**Rows are append-only, and the reader dedupes.** `RunQuery.frame` sorts by `indexed_at` and keeps the last row per `run_id` (`src/femforge/telemetry/runs.py:278-280`); `ArtifactQuery.frame` keeps the last per `(kind, key)` (`src/femforge/telemetry/artifacts.py:100-102`). A run's second row supersedes its first, and nothing rewrites a shard.

The driver records its own runs. With a root, it appends a row when it first writes the manifest and another once the run has settled, so a running sweep is visible and a later scan finds nothing new (`src/femforge/run/driver.py:315-316`, `src/femforge/run/driver.py:448`). A failed write only warns, because telemetry never fails a solve (`src/femforge/run/driver.py:376-385`). `current_sweep_point()` supplies the sweep and point ids of a driver row (`src/femforge/telemetry/runs.py:145`), and a later scan row inherits them, because only the worker that solved the run knew them (`src/femforge/telemetry/runs.py:390-410`).

`RunQuery` and `ArtifactQuery` are fluent: each method returns a new query, and nothing is collected until `frame()`. `runs()` and `records()` return the real objects and skip whatever retention removed since (`src/femforge/telemetry/runs.py:301-317`). `with_live()` joins [live_runs][femforge.telemetry.live_runs], because the claim files are the truth about what is in flight. `femforge artifacts ls` lists from the artifact index when a root resolves and the index holds rows, and scans the store otherwise (`src/femforge/cli.py:273-276`, `src/femforge/telemetry/cli.py:445-462`).

## Sweep progress

[SweepJournal][femforge.telemetry.SweepJournal] writes one sweep (`src/femforge/telemetry/sweep.py:67-170`):

| Call | Writes |
|---|---|
| `start()` | the launch row, so a reader sees the sweep from its first point |
| `event()` | one point transition, through this process's own sweep-event journal |
| `checkpoint()` | this process's journalled events into parquet |
| `finish()` | the closing row with `finished_at`; then it checkpoints, closes its journal, recovers the journals of exited workers and compacts the sweep's shards into one |

The parent and every worker own a journal of their own, one flushed line per transition, so a killed worker's events survive it. [sweep_point][femforge.telemetry.sweep_point] sets a context variable and binds both ids into the log context, so every record emitted under a point is attributable to it without the caller binding anything (`src/femforge/telemetry/context.py:38-49`).

[SweepQuery][femforge.telemetry.SweepQuery] reads one sweep back (`src/femforge/telemetry/sweep.py:173-232`). `latest()` is the last transition per `point_key`, and `failures()` keeps the failed points with the traceback each worker recorded. `summary()` counts the states, divides the points settled in the trailing 30 minutes, or over the sweep's whole life when it is younger, by that window to get a rate, and divides the outstanding points by the rate for an ETA (`src/femforge/telemetry/sweep.py:27`, `src/femforge/telemetry/sweep.py:202-232`). Sweep ids are the caller's.

## Convergence

[solve_case][femforge.elmer.solve_case] writes `convergence.jsonl` in the bundle, one flushed line per convergence sample, with exactly the keys of the convergence schema (`src/femforge/elmer/runtime/solve.py:72-84`). Immediately before the solve, the driver's `run_solver` stage writes `convergence_targets.json` from the solver stack's `convergence_targets`, the same mapping it hands the terminal renderer (`src/femforge/run/driver.py:682-692`). [ConvergenceTargetsFile][femforge.telemetry.ConvergenceTargetsFile] is that file's model, and a bundle without one reads as having no targets (`src/femforge/telemetry/convergence.py:50-100`). Both files belong to the `logs` retention class (`src/femforge/common/files.py:89`).

[scan_convergence][femforge.telemetry.scan_convergence] reads the log onto the convergence schema, skipping a malformed line, and derives two columns (`src/femforge/telemetry/convergence.py:111-142`):

| Column | Meaning |
|---|---|
| `first_relc` | the relative change at iteration 1 of each `(solver, loop)`; iteration 1 resets it, so a nonlinear loop that restarts inside a steady-state sweep is measured from its own start |
| `progress` | the log-scale fraction of the decades between `first_relc` and the loop's tolerance already covered |

`progress` calls `convergence_progress`, the same pure function the terminal renderer uses, so a stored run and a live one report the same number (`src/femforge/elmer/solver_log.py:105-119`, `src/femforge/elmer/solver_log.py:151-153`).

## Tail and tap

Two readers follow data that is still moving:

| Reader | Follows | Where |
|---|---|---|
| [TextTail][femforge.telemetry.TextTail] | one growing text file, such as a journal, a `run.log` or an `elmersolver.log`: a byte-offset cursor that holds back an unterminated last line and rewinds when the file shrinks | `src/femforge/telemetry/tail.py:15-50` |
| [LiveLogTap][femforge.telemetry.LiveLogTap] | this process's own records: a loguru sink with a ring buffer of the last 5000 and one asyncio queue per subscriber | `src/femforge/telemetry/tap.py:34-84` |

`femforge logs tail` runs one `TextTail` per log journal and re-reads them every 0.5 s with `--follow` (`src/femforge/telemetry/cli.py:367-388`). `LiveLogTap` builds the same row shape as the parquet sink, imports no trame, delivers to each subscriber's loop with `call_soon_threadsafe`, drops a record rather than block when a queue is full, and unsubscribes a subscriber whose loop has closed (`src/femforge/telemetry/tap.py:46-65`).

## The CLI

`src/femforge/telemetry/cli.py` adds the `index`, `runs`, `logs`, `sweeps` and `telemetry` subcommands to the `femforge` command (`src/femforge/telemetry/cli.py:148-230`). Each resolves its root through `--telemetry`, else `FEMFORGE_TELEMETRY`, and fails with a message naming both (`src/femforge/telemetry/cli.py:108-113`). Every listing renders through `frame_table`, whose headers are the schema's column names, so a table cannot drift from the frame behind it (`src/femforge/telemetry/cli.py:130-145`). `runs reap` imports `femforge.run` only when it runs, because a listing never needs the solve stack (`src/femforge/telemetry/cli.py:289-290`). [CLI](../../user-guide/cli.md#telemetry) lists every option.

## What a consumer may rely on

| A consumer may rely on | Because |
|---|---|
| A shard, once in place, never changes | writers only add shards; compaction writes the merged shard before it deletes the originals |
| A reader never sees a half-written shard | every shard is staged under `.tmp` and moved into place |
| The latest row is the truth for an indexed entity | readers sort by `indexed_at` and keep the last row per run, artifact or sweep |
| A column missing from an older shard reads as null | `HiveStore.scan` coerces every shard onto the current schema |
| A record logged a moment ago is readable | it is in the writer's journal, and `scan_logs` and `SweepQuery.events` read the journals too |
| A claim's liveness is current | `live_runs` reads the claim files on every call and stores nothing |

| A consumer may not rely on | Because |
|---|---|
| One row per run, artifact or sweep in the raw scan | every pass appends; dedupe by the key and `indexed_at` |
| One shard per partition | each append adds shards until a compaction folds the settled ones |
| A shard path that stays on disk | a compaction deletes the shards it merged |
| A record in exactly one place during a checkpoint or a compaction | a checkpoint appends before it truncates the journal, and a compaction writes before it deletes, so a concurrent reader can see a record twice |
| Telemetry rows for every run | a failed telemetry write only warns, and a process without a root writes none |
