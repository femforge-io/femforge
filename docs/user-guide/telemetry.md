# Telemetry

Telemetry is the central record of what femforge did: every log record, every run, every stored artifact and every sweep point, kept as hive-partitioned parquet under one directory. A run bundle describes one run; telemetry answers questions across thousands of them, such as every error from last night's sweep or every run that used one mesh, without opening each bundle. It is off until you name a root, and a process with telemetry off never imports polars.

## Turn it on

Name a telemetry root, the directory the tables live under, in one of two ways:

| Where | How |
|---|---|
| The environment | Set `FEMFORGE_TELEMETRY` to the root. Every driver, log sink and command in the process uses it. |
| The code | Pass `telemetry=` to [Driver][femforge.run.Driver] or [setup_logging][femforge.common.setup_logging]. An explicit root wins over the variable. |

```console
export FEMFORGE_TELEMETRY=telemetry
```

With a root, the driver appends a row to the run index when it first writes a run's manifest and another once the run settles, so a run is visible while it solves. Every record logged during `evaluate()` lands in that driver's root. A failed telemetry write only logs a warning; it never fails a solve.

## What the root holds

```text
telemetry/
├── logs/date=2026-10-13/process=gap-sweep/part-<host>-<pid>-<utc>-<seq>.parquet
├── runs/month=2026-10-01/part-...parquet
├── artifacts/kind=elmer_mesh/part-...parquet
├── sweeps/month=2026-10-01/part-...parquet
├── sweep_events/sweep_id=gap-study/part-...parquet
└── live/logs-<host>-<pid>.jsonl
```

| Table | One row per | Partitioned by | Written by |
|---|---|---|---|
| `logs` | log record | date and process name | the parquet log sink |
| `runs` | run index pass over a bundle | the month the run was created | the driver, `femforge index`, `femforge runs reap` |
| `artifacts` | index pass over a stored artifact | artifact kind | `femforge index --store` |
| `sweeps` | sweep launch, plus a closing row when it finishes | the month it started | [SweepJournal][femforge.telemetry.SweepJournal] |
| `sweep_events` | point state transition | sweep id | [SweepJournal][femforge.telemetry.SweepJournal] |

[Telemetry][femforge.telemetry.Telemetry] addresses a root and hands out one [HiveStore][femforge.telemetry.HiveStore] per table. Each table's columns are a `polars.Schema` whose names come from a `StrEnum`, such as [RunColumn][femforge.telemetry.RunColumn] and [LogColumn][femforge.telemetry.LogColumn], so you filter with `pl.col(RunColumn.STATUS)` instead of a bare string.

The store is append-only. Each writer writes its own shard, named by host, process id, a UTC stamp and a sequence number, staged under a temporary name and moved into place, so a reader never sees half a file and many processes write at once without locks. An index re-read adds a new row for the same run; readers keep the latest row per run.

The convergence log is the exception: each bundle keeps its own `convergence.jsonl`, and [scan_convergence][femforge.telemetry.scan_convergence] reads it as a typed frame with two derived columns, `first_relc` and `progress`, the fraction of the way to the tolerance the sif set.

## The journal

Every log record and every sweep event goes first to the writing process's own JSONL journal under `live/`, one flushed line per record. The log sink checkpoints the journal into parquet every 500 records, every 5 s, and at exit, then truncates it, so a record sits in exactly one of the two places. A process that dies leaves its journal behind, and the next sink or sweep writer that starts on the root folds it into parquet.

Readers union both halves. [scan_logs][femforge.telemetry.scan_logs] and `SweepQuery.events()` return the settled shards together with what is still only in a journal, so a live sweep shows its newest records.

## Read it back

The query classes are fluent: each method returns a new query, and nothing reads parquet until `frame()` or `page()`. This lists the last week's finished runs:

```python
from datetime import datetime, timedelta, timezone

import femforge as ff
from femforge.telemetry import RunColumn, RunIndex

telemetry = ff.Telemetry("telemetry")
index = RunIndex(telemetry, "runs")
added = index.update()

week_ago = datetime.now(timezone.utc) - timedelta(days=7)
recent = index.query().status("ok", "diverged").since(week_ago).newest(20).frame()

print(f"{added} bundles indexed")
print(recent.select(RunColumn.RUN_ID, RunColumn.STATUS, RunColumn.CREATED_AT, RunColumn.WALL_TIME))
```

`update()` reads only the bundles whose `manifest.json` changed since the last pass; `update(rebuild=True)` reads them all. It parses a dozen manifest keys without validating the config, so a corrupt bundle costs one null instead of the pass.

| Reader | Over | Filters |
|---|---|---|
| [RunIndex][femforge.telemetry.RunIndex]`.query()` | every runs directory the root knows | `status`, `tags`, `text`, `since`, `sweep`, `newest`, `with_live`, `where` |
| [ArtifactIndex][femforge.telemetry.ArtifactIndex]`.query()` | one artifact store | `kind`, `spec_type`, `since`, `key_prefix`, `newest`, `where` |
| [LogQuery][femforge.telemetry.LogQuery]`.over(telemetry)` | logs, settled and live | `levels`, `processes`, `run`, `sweep`, `since`, `until`, `text`, `where` |
| [SweepQuery][femforge.telemetry.SweepQuery] | one sweep | `launch`, `events`, `latest`, `failures`, `summary` |

`with_live()` joins the claim each run holds right now, read from `<run_id>.lock/owner.json`, with its heartbeat age. `RunQuery.runs()` and `ArtifactQuery.records()` open the matching bundles and artifacts, skipping the ones retention has removed.

For anything the filters do not cover, scan a table as a polars `LazyFrame`:

```python
import femforge as ff
import polars as pl
from femforge.telemetry import LogColumn

telemetry = ff.Telemetry("telemetry")

errors = telemetry.logs.scan().filter(pl.col(LogColumn.LEVEL) == "ERROR").group_by(LogColumn.PROCESS).len().collect()
print(errors)
```

`scan()` reads the settled shards only; use `scan_logs(telemetry)` to include the live journals.

## Record a sweep

A sweep is the caller's: you pick its id, and a [SweepJournal][femforge.telemetry.SweepJournal] records its launch row and each point transition. Inside [sweep_point][femforge.telemetry.sweep_point], every log record and every run index row the driver writes carries the sweep id and the point key:

```python
import traceback

import femforge as ff
from femforge.telemetry import SweepJournal, SweepState, sweep_point


def gap_config(gap_mm: int) -> ff.SimulationConfig:
    """The actuator config at one air gap."""
    return ff.SimulationConfig(
        mesh=ff.MeshConfig(step=f"models/actuator-gap-{gap_mm}mm.step", global_mesh_size=2 * ff.U.mm),
        domain=ff.DomainConfig(materials=[ff.materials.Air(), ff.materials.N52()]),
        meta=ff.RunMeta(description=f"Gap {gap_mm} mm", tags={"study": "gap"}),
    )


telemetry = ff.Telemetry("telemetry")
driver = ff.Driver(runs_dir="runs", telemetry=telemetry.root)
gaps = [1, 2, 3]

journal = SweepJournal(telemetry, sweep_id="gap-study")
journal.start("Air gap sweep", total=len(gaps))

for gap in gaps:
    key = f"gap={gap}"
    journal.event(key, SweepState.RUNNING, point_name=f"Gap {gap} mm")
    try:
        with sweep_point(journal.sweep_id, key):
            _ = driver.evaluate(gap_config(gap))
    except Exception:
        journal.event(key, SweepState.FAILED, error=traceback.format_exc())
        continue

    journal.event(key, SweepState.DONE)

journal.finish()
```

Each worker process of a parallel sweep opens a `SweepJournal` of its own with the same sweep id. `SweepQuery(telemetry, "gap-study").summary()` returns a [SweepSummary][femforge.telemetry.SweepSummary]: the count per state, the points settled per hour over the last 30 minutes, or over the sweep's life when it is younger, and the ETA that rate gives.

## Follow a run live

| Tool | Follows |
|---|---|
| `femforge logs tail --follow` | every process's log journal under `live/`, re-read every 0.5 s |
| [TextTail][femforge.telemetry.TextTail] | one growing text file, such as a bundle's `run.log` or `elmersolver.log`; each `read_new()` returns only the complete lines written since the last call |
| [LiveLogTap][femforge.telemetry.LiveLogTap] | this process's own records: a loguru sink that keeps the last 5000 in memory and pushes each new one onto asyncio subscriber queues |

The workbench's [live monitors](workbench.md#the-built-in-cards) use all three.

## Logging

femforge logs through loguru. [setup_logging][femforge.common.setup_logging] installs the terminal sink and routes the standard library's `logging` and `warnings` through it; `ff.log` runs it with the defaults before its first record.

```python
from pathlib import Path

import femforge as ff
from femforge.common import setup_logging

setup_logging(level="DEBUG", telemetry=Path("telemetry"), process="gap-sweep")

ff.log.bind(gap=0.002, mesh_size=0.0005).info("Gap set.")
```

| Setting | Argument | Default |
|---|---|---|
| Terminal level | `level` | `FEMFORGE_LOG_LEVEL`, else `INFO` |
| Telemetry root | `telemetry` | `FEMFORGE_TELEMETRY`, else off |
| Process name, the `logs` partition | `process` | the script's file name, folded to `[a-z0-9_.-]` |
| Library gates | `library_levels` | `build123d` at `WARNING` |

The parquet sink takes every record at `DEBUG` whatever the terminal level, so the store keeps what the terminal hides. Each run also writes its own records, at `DEBUG` and above, to `run.log` in its bundle.

Log a short, fixed message and put the data in `log.bind()`, keyed by field name in snake case without a unit suffix. The terminal prints the bound fields beside the message, and the sink stores them as JSON in the `extra` column, so you can search the message as text and filter on the fields. `run_id`, `sweep_id` and `point_key` get columns of their own.

## Commands

The `femforge` command reads and maintains the store; each subcommand takes `--telemetry`, else reads `FEMFORGE_TELEMETRY`.

| Command | Does |
|---|---|
| `femforge index update --runs-dir R [--store S]` | Index the bundles of a runs directory that changed, and an artifact store with `--store`. |
| `femforge index build --runs-dir R [--store S]` | The same, re-reading every bundle and artifact. |
| `femforge runs ls` | List indexed runs, newest first. |
| `femforge runs reap --runs-dir R` | Break dead claims, mark their bundles `abandoned` and re-index them. |
| `femforge logs query` | Search the stored log records. |
| `femforge logs tail [--follow]` | Print the live journals. |
| `femforge sweeps ls` | List sweeps, newest first. |
| `femforge sweeps show <id>` | Show one sweep's counts, points and failures. |
| `femforge telemetry compact [--settle SECONDS]` | Fold each partition's shards untouched for `--settle` seconds, 300 by default, into one shard. |

`femforge artifacts ls` reads the artifact index when a root resolves and the index has rows, and scans the store otherwise. [CLI](cli.md) lists every option.
