"""Tests for telemetry.runs: the incremental scan of run bundles, the driver's own rows, the fluent query and the live overlay."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast

import polars as pl

from femforge.artifacts.lock import LOCK_SUFFIX, DirLock
from femforge.common.files import BundleFile
from femforge.config import SimulationConfig
from femforge.run.manifest import RunManifest
from femforge.telemetry.context import sweep_point
from femforge.telemetry.paths import Telemetry
from femforge.telemetry.runs import RunIndex, RunQuery, live_runs, run_row
from femforge.telemetry.schemas import LIVE_RUN_SCHEMA, RUN_INDEX_SCHEMA, LiveRunColumn, RunColumn, RunSource
from femforge.run.result import LifecycleStatus, RunStatus


if TYPE_CHECKING:
    from pathlib import Path


CREATED = "2026-09-05T12:00:00+00:00"


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(runs_dir: Path, run_id: str, **overrides: object) -> Path:
    """A synthetic bundle carrying the manifest keys the index reads, plus a result and a solved VTU."""
    sim_dir: Path = runs_dir / run_id
    manifest: dict[str, object] = {
        "run_id": run_id,
        "cache_key": "c" * 64,
        "created_at": CREATED,
        "git_sha": "sha1",
        "elmer_version": "26.2",
        "physics": "thermal",
        "mesh_key": "9c1d" * 16,
        "mesh_stats": {"element_count": 4067, "min_element_size": 0.002, "max_element_size": 0.004},
        "status": "ok",
        "stage_history": [
            {"stage": "mesh", "started_at": CREATED, "finished_at": "2026-09-05T12:00:10+00:00"},
            {"stage": "run_solver", "started_at": "2026-09-05T12:00:10+00:00", "finished_at": None},
        ],
        "artifacts": [{"kind": "elmer_mesh", "key": "k" * 64}],
        "cleanups": [{"trigger": "driver"}],
        "notes": "an overnight point",
        "config": {"meta": {"description": "stall snapshot", "tags": {"experiment": "thermal_limit"}}, "frame": {"kind": "axisymmetric"}},
    }
    manifest.update(overrides)

    _write(sim_dir / BundleFile.MANIFEST, manifest)
    _write(sim_dir / BundleFile.RESULT, {"status": "ok", "temperature_max": "370.9 K", "wall_time": 0.53, "force": {"x": 0.0, "y": -8.7, "z": 0.0, "units": "newton"}})
    _ = (sim_dir / "case_t0001.vtu").write_text("<VTKFile/>", encoding="utf-8")

    return sim_dir


def _lock(runs_dir: Path, run_id: str) -> DirLock:
    """A held claim on a run, as the driver leaves one while it solves."""
    lock = DirLock(runs_dir / f"{run_id}{LOCK_SUFFIX}")
    assert lock.try_acquire()

    return lock


def _index(tmp_path: Path) -> RunIndex:
    return RunIndex(Telemetry(tmp_path / "telemetry"), tmp_path / "runs")


# --- the scan ----------------------------------------------------------------


def test_a_scanned_bundle_lands_with_every_column_the_browser_needs(tmp_path: Path) -> None:
    """One pass over the bundles fills the manifest, mesh, metadata and result columns of the index."""
    index = _index(tmp_path)
    _ = _bundle(tmp_path / "runs", "brave-otter-1234")

    assert index.update() == 1

    row = cast("dict[str, object]", index.query().frame().rows(named=True)[0])

    assert row[RunColumn.RUN_ID] == "brave-otter-1234"
    assert row[RunColumn.STATUS] == "ok"
    assert row[RunColumn.SOURCE] == RunSource.SCAN
    assert row[RunColumn.CREATED_AT] == datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    assert row[RunColumn.FINISHED_AT] == datetime(2026, 9, 5, 12, 0, 10, tzinfo=timezone.utc)
    assert row[RunColumn.CURRENT_STAGE] == "run_solver"
    assert row[RunColumn.STAGES_DONE] == 1
    assert row[RunColumn.ELEMENT_COUNT] == 4067
    assert row[RunColumn.DESCRIPTION] == "stall snapshot"
    assert row[RunColumn.TAGS] == '{"experiment": "thermal_limit"}'
    assert row[RunColumn.MODEL_DIM] == 2
    assert row[RunColumn.ARTIFACT_KEYS] == ["k" * 64]
    assert row[RunColumn.CLEANUPS] == 1
    assert row[RunColumn.HAS_RESULT] is True
    assert row[RunColumn.HAS_VTU] is True
    assert row[RunColumn.PUBLISHED] is False
    assert row[RunColumn.TEMPERATURE_MAX] == 370.9
    assert row[RunColumn.FORCE_Y] == -8.7
    assert row[RunColumn.WALL_TIME] == 0.53
    assert cast("int", row[RunColumn.BUNDLE_BYTES]) > 0


def test_a_second_pass_reads_only_the_bundles_whose_manifest_moved(tmp_path: Path) -> None:
    """The mtime anti-join is the watermark, so an unchanged runs directory costs no manifest parse."""
    index = _index(tmp_path)
    runs = tmp_path / "runs"
    first = _bundle(runs, "one-1111")
    _ = _bundle(runs, "two-2222")

    assert index.update() == 2
    assert index.update() == 0

    _write(first / BundleFile.MANIFEST, {"run_id": "one-1111", "created_at": CREATED, "status": "error"})

    assert index.update() == 1
    assert index.query().frame().height == 2


def test_rebuild_re_reads_every_bundle(tmp_path: Path) -> None:
    """A rebuild ignores the watermark, so a schema change can be replayed over the whole directory."""
    index = _index(tmp_path)
    _ = _bundle(tmp_path / "runs", "one-1111")

    _ = index.update()

    assert index.update(rebuild=True) == 1
    assert index.query().frame().height == 1


def test_the_newest_row_wins_and_the_older_one_is_dropped(tmp_path: Path) -> None:
    """Appends never rewrite, so the reader collapses a run's history to its latest row."""
    index = _index(tmp_path)
    sim_dir = _bundle(tmp_path / "runs", "one-1111")

    _ = index.update()
    _write(sim_dir / BundleFile.MANIFEST, {"run_id": "one-1111", "created_at": CREATED, "status": "diverged"})
    _ = index.update(rebuild=True)

    frame = index.query().frame()

    assert frame.height == 1
    assert frame.get_column(RunColumn.STATUS).to_list() == ["diverged"]


def test_rows_indexed_in_the_same_instant_collapse_to_the_one_written_last(tmp_path: Path) -> None:
    """Write order breaks a tie on ``indexed_at``, so the last row appended is the run's state."""
    index = _index(tmp_path)
    sim_dir = _bundle(tmp_path / "runs", "one-1111")
    _ = index.update()
    stamp = datetime.now(timezone.utc) + timedelta(minutes=1)

    rows = [run_row({"run_id": "one-1111", "created_at": CREATED, "status": status}, sim_dir, source=RunSource.SCAN, manifest_mtime=stamp) for status in [*("running", "abandoned", "error") * 5, "ok"]]
    _ = index.store.append(pl.DataFrame(rows, schema=RUN_INDEX_SCHEMA).with_columns(pl.lit(stamp).alias(RunColumn.INDEXED_AT)))

    assert index.query().frame().get_column(RunColumn.STATUS).to_list() == ["ok"]


def test_lock_directories_and_bundles_without_a_manifest_are_not_candidates(tmp_path: Path) -> None:
    """Only a directory holding a manifest is a bundle; a claim directory and a stray one are skipped."""
    index = _index(tmp_path)
    runs = tmp_path / "runs"
    _ = _bundle(runs, "one-1111")
    _ = _lock(runs, "one-1111")
    (runs / "not-a-bundle").mkdir()
    _ = (runs / "stray.txt").write_text("x", encoding="utf-8")

    assert index.update() == 1


def test_an_empty_runs_directory_indexes_nothing(tmp_path: Path) -> None:
    """A runs directory that does not exist yet is not an error, it is simply no rows."""
    index = _index(tmp_path)

    assert index.update() == 0

    (tmp_path / "runs").mkdir()

    assert index.update() == 0


def test_an_unreadable_manifest_still_yields_a_row_keyed_by_its_directory(tmp_path: Path) -> None:
    """A corrupt bundle must not break the pass; it is indexed as what its directory can tell."""
    index = _index(tmp_path)
    sim_dir = tmp_path / "runs" / "broken-0000"
    sim_dir.mkdir(parents=True)
    _ = (sim_dir / BundleFile.MANIFEST).write_text("{not json", encoding="utf-8")

    assert index.update() == 1

    row = cast("dict[str, object]", index.query().frame().rows(named=True)[0])

    assert row[RunColumn.RUN_ID] == "broken-0000"
    assert row[RunColumn.STATUS] is None
    assert row[RunColumn.MESH_NAME] is None
    assert row[RunColumn.HAS_RESULT] is False


def test_odd_manifest_values_read_as_null_rather_than_raising(tmp_path: Path) -> None:
    """Every field is read defensively: wrong JSON types, an unparseable date and a unitless quantity all read as null."""
    index = _index(tmp_path)
    sim_dir = _bundle(
        tmp_path / "runs",
        "odd-0000",
        created_at="not-a-date",
        stage_history="unexpected",
        config=[],
        mesh_key=7,
        mesh_stats={"element_count": True},
        artifacts=[{"kind": "elmer_mesh"}, {"kind": "step", "key": "k" * 64}],
    )
    _write(sim_dir / BundleFile.RESULT, {"temperature_max": "warm", "wall_time": None, "force": "sideways"})

    assert index.update() == 1

    row = cast("dict[str, object]", index.query().frame().rows(named=True)[0])

    assert row[RunColumn.CREATED_AT] is None
    assert row[RunColumn.CURRENT_STAGE] is None
    assert row[RunColumn.DESCRIPTION] is None
    assert row[RunColumn.TAGS] is None
    assert row[RunColumn.MESH_KEY] is None
    assert row[RunColumn.ELEMENT_COUNT] is None
    assert row[RunColumn.TEMPERATURE_MAX] is None
    assert row[RunColumn.FORCE_Y] is None
    assert row[RunColumn.MODEL_DIM] is None
    assert row[RunColumn.ARTIFACT_KEYS] == ["k" * 64]


def test_an_unknown_frame_reads_as_no_model_dim(tmp_path: Path) -> None:
    index = _index(tmp_path)
    _ = _bundle(tmp_path / "runs", "wedge-0000", config={"frame": {"kind": "wedge"}})

    assert index.update() == 1

    row = cast("dict[str, object]", index.query().frame().rows(named=True)[0])

    assert row[RunColumn.MODEL_DIM] is None


# --- the driver's own rows ---------------------------------------------------


def test_record_appends_a_driver_row_carrying_the_sweep_point(tmp_path: Path) -> None:
    """A run solved under a sweep point is attributable to it without the sweep wiring the index."""
    index = _index(tmp_path)
    sim_dir = tmp_path / "runs" / "driven-0001"
    sim_dir.mkdir(parents=True)
    manifest = RunManifest.build(
        SimulationConfig(),
        status=LifecycleStatus.RUNNING,
        run_id="driven-0001",
        code_sha="sha1",
    )
    _ = (sim_dir / BundleFile.MANIFEST).write_text(manifest.model_dump_json(), encoding="utf-8")

    with sweep_point("sweep-a", "point-7"):
        index.record(manifest, sim_dir)

    row = cast("dict[str, object]", index.query().frame().rows(named=True)[0])

    assert row[RunColumn.SOURCE] == RunSource.DRIVER
    assert row[RunColumn.SWEEP_ID] == "sweep-a"
    assert row[RunColumn.POINT_KEY] == "point-7"
    assert row[RunColumn.STATUS] == "running"


def test_record_without_a_written_manifest_stamps_the_current_time(tmp_path: Path) -> None:
    """The row is appended even before the manifest reaches disk, so the first write is never blocked on it."""
    index = _index(tmp_path)
    manifest = RunManifest.build(
        SimulationConfig(),
        status=LifecycleStatus.RUNNING,
        run_id="unwritten-0001",
        code_sha="sha1",
    )

    index.record(manifest, tmp_path / "runs" / "unwritten-0001")

    stamped = cast("datetime | None", index.query().frame().get_column(RunColumn.MANIFEST_MTIME).to_list()[0])

    assert stamped is not None
    assert (datetime.now(timezone.utc) - stamped).total_seconds() < 60


def test_a_recorded_run_is_not_read_again_by_the_next_scan(tmp_path: Path) -> None:
    """The driver's row carries the manifest mtime, so the scan's watermark already covers it."""
    index = _index(tmp_path)
    sim_dir = _bundle(tmp_path / "runs", "driven-0001")
    manifest = RunManifest.build(
        SimulationConfig(),
        status=RunStatus.OK,
        run_id="driven-0001",
        code_sha="sha1",
    )
    index.record(manifest, sim_dir)

    assert index.update() == 0


# --- the query ---------------------------------------------------------------


def test_a_rescanned_run_keeps_the_sweep_the_driver_filed_it_under(tmp_path: Path) -> None:
    """Only the worker knew the sweep point; a later scan of the same bundle must not blank it."""
    index = _index(tmp_path)
    sim_dir = tmp_path / "runs" / "driven-0002"
    sim_dir.mkdir(parents=True)
    manifest = RunManifest.build(
        SimulationConfig(),
        status=LifecycleStatus.RUNNING,
        run_id="driven-0002",
        code_sha="sha1",
    )
    _ = (sim_dir / BundleFile.MANIFEST).write_text(manifest.model_dump_json(), encoding="utf-8")

    with sweep_point("sweep-a", "point-7"):
        index.record(manifest, sim_dir)

    manifest.status = LifecycleStatus.ABANDONED
    _ = (sim_dir / BundleFile.MANIFEST).write_text(manifest.model_dump_json(), encoding="utf-8")
    later = (datetime.now(timezone.utc) + timedelta(minutes=1)).timestamp()
    os.utime(sim_dir / BundleFile.MANIFEST, (later, later))

    assert index.update() == 1

    row = cast("dict[str, object]", index.query().frame().rows(named=True)[0])

    assert row[RunColumn.STATUS] == "abandoned"
    assert row[RunColumn.SOURCE] == RunSource.SCAN
    assert row[RunColumn.SWEEP_ID] == "sweep-a"
    assert row[RunColumn.POINT_KEY] == "point-7"


def test_filters_narrow_by_status_tags_text_sweep_and_age(tmp_path: Path) -> None:
    """Each filter is one predicate over the frame, and they compose."""
    index = _index(tmp_path)
    runs = tmp_path / "runs"
    _ = _bundle(runs, "good-1111")
    _ = _bundle(
        runs,
        "bad-2222",
        status="error",
        config={"meta": {"description": "other", "tags": {"experiment": "sweep_b"}}},
    )
    _ = index.update()

    query = index.query()

    assert query.status("ok").run_ids() == ["good-1111"]
    assert query.tags(experiment="thermal_limit").run_ids() == ["good-1111"]
    assert query.text("STALL").run_ids() == ["good-1111"]
    assert query.text("bad-2222").run_ids() == ["bad-2222"]
    assert query.since(datetime(2026, 9, 6, tzinfo=timezone.utc)).run_ids() == []
    assert query.since(datetime(2026, 9, 4, tzinfo=timezone.utc)).status("error").run_ids() == ["bad-2222"]
    assert query.sweep("nothing").run_ids() == []
    assert len(query.newest(1).run_ids()) == 1


def test_a_filter_judges_each_run_by_its_latest_row(tmp_path: Path) -> None:
    """A run recorded running and later settled ok is not a running run, however many rows it left."""
    index = _index(tmp_path)
    runs = tmp_path / "runs"
    _ = _bundle(runs, "settled-1111", status="running")
    _ = index.update()
    _ = _bundle(runs, "settled-1111", status="ok")
    _ = index.update(rebuild=True)

    assert index.query().status("running").run_ids() == []
    assert index.query().status("ok").run_ids() == ["settled-1111"]


def test_where_takes_any_predicate(tmp_path: Path) -> None:
    """The named filters are shorthands; an arbitrary expression is always available."""
    index = _index(tmp_path)
    _ = _bundle(tmp_path / "runs", "good-1111")
    _ = index.update()

    assert index.query().where(pl.col(RunColumn.ELEMENT_COUNT) > 1000).run_ids() == ["good-1111"]


def test_runs_opens_the_bundles_that_are_still_on_disk(tmp_path: Path) -> None:
    """A run whose directory retention removed is dropped rather than raising when the facade is asked for."""
    index = _index(tmp_path)
    runs = tmp_path / "runs"
    kept = _bundle(runs, "kept-1111")
    removed = _bundle(runs, "gone-2222")
    _ = index.update()

    for child in removed.iterdir():
        child.unlink()
    removed.rmdir()

    opened = index.query().runs()

    assert [run.path for run in opened] == [kept]


# --- the live overlay --------------------------------------------------------


def test_live_runs_reads_the_owner_of_each_claim(tmp_path: Path) -> None:
    """The in-flight table is built per call from the lock owners, so it is always current."""
    runs = tmp_path / "runs"
    runs.mkdir()
    lock = _lock(runs, "held-1111")
    (runs / f"ownerless{LOCK_SUFFIX}").mkdir()

    frame = live_runs(runs)

    assert frame.get_column(LiveRunColumn.RUN_ID).to_list() == ["held-1111"]
    assert frame.get_column(LiveRunColumn.STALE).to_list() == [False]
    assert cast("float", frame.get_column(LiveRunColumn.HEARTBEAT_AGE_S).to_list()[0]) < 60

    lock.release()

    assert live_runs(runs).height == 0
    assert live_runs(tmp_path / "nowhere").schema == LIVE_RUN_SCHEMA


def test_with_live_joins_the_claims_onto_the_listing(tmp_path: Path) -> None:
    """A listing can mark which runs are in flight without a second lookup per row."""
    index = _index(tmp_path)
    runs = tmp_path / "runs"
    _ = _bundle(runs, "held-1111")
    _ = _bundle(runs, "idle-2222")
    _ = index.update()
    lock = _lock(runs, "held-1111")

    frame = index.query().with_live().frame()
    held = frame.filter(pl.col(RunColumn.RUN_ID) == "held-1111")

    assert cast("int | None", held.get_column(LiveRunColumn.PID).to_list()[0]) is not None
    assert frame.filter(pl.col(RunColumn.RUN_ID) == "idle-2222").get_column(LiveRunColumn.PID).to_list() == [None]

    lock.release()


def test_the_live_overlay_of_an_empty_index_is_still_typed(tmp_path: Path) -> None:
    """Nothing indexed means nothing to join, and the frame still carries both schemas."""
    frame = RunQuery(Telemetry(tmp_path).runs.scan()).with_live().frame()

    assert frame.height == 0
    assert LiveRunColumn.STALE in frame.columns


# --- the row builder ---------------------------------------------------------


def test_run_row_uses_the_directory_name_when_the_manifest_has_no_id(tmp_path: Path) -> None:
    """A manifest missing its own id still indexes: the bundle directory is the run's identity."""
    sim_dir = tmp_path / "runs" / "named-by-dir"
    sim_dir.mkdir(parents=True)

    row = run_row(
        {},
        sim_dir,
        source=RunSource.SCAN,
        manifest_mtime=datetime.now(timezone.utc) - timedelta(days=1),
    )

    assert row[RunColumn.RUN_ID] == "named-by-dir"
    assert row[RunColumn.RUNS_DIR] == str(sim_dir.parent)
    assert row[RunColumn.STAGES_DONE] == 0
