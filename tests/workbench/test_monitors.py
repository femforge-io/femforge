"""The live monitors: the pure helpers on synthetic data, the dock spec, the feeds on a task-mode server, and the pty bridge on a fake pty."""

import asyncio
import json
import queue
import subprocess
import threading
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import polars as pl
import pytest

from femforge.common.files import BundleFile, write_atomic
from femforge.common.log import LogLevel, log
from femforge.config import SimulationConfig
from femforge.elmer.solver_log import LoopTargets
from femforge.execution.docker.cli import CONTAINER_PREFIX
from femforge.meshing.config import MeshConfig
from femforge.run.manifest import RunManifest
from femforge.run.stages import Stage, StageEvent
from femforge.telemetry.convergence import ConvergenceTargetsFile
from femforge.telemetry.journal import JOURNAL_SUFFIX, json_default
from femforge.telemetry.paths import TELEMETRY_ENV, Telemetry, TelemetryDir
from femforge.telemetry.schemas import CONVERGENCE_SCHEMA, LOG_SCHEMA, RUN_INDEX_SCHEMA, SWEEP_EVENT_SCHEMA, LogColumn, SweepEventColumn, SweepState
from femforge.telemetry.sweep import SweepJournal, SweepSummary


pytest.importorskip("trame")
pytest.importorskip("pyvista")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Workbench  # noqa: E402
from femforge_workbench.dock import Split, panels  # noqa: E402
from femforge_workbench.grid import GROUP_COLUMN, Row  # noqa: E402
from femforge_workbench.panels import ItemField, Shared  # noqa: E402
from femforge_workbench.panels.artifacts import ANY  # noqa: E402
from femforge_workbench.panels.log_browser import (  # noqa: E402
    Folded,
    JournalTails,
    LogBrowserPanel,
    LogFilters,
    LogVar,
    detail_json,
    journal_rows,
    last_line_time,
    line_time,
    parse_moment,
)
from femforge_workbench.panels.monitors import MonitorPanel, workbench  # noqa: E402
from femforge_workbench.panels.run_monitor import RunMonitorPanel, RunVar, convergence_figure, heartbeat_text, run_items  # noqa: E402
from femforge_workbench.panels.sweep_monitor import STALLED, FailureField, PointRow, SweepMonitorPanel, SweepVar, default_sweep, failure_items, point_rows, progress, sweep_items, traceback_of  # noqa: E402
from femforge_workbench.panels import terminal  # noqa: E402
from femforge_workbench.panels.terminal import EXITED, LOCAL_LABEL, Mode, PtyBridge, Target, TerminalPanel, TerminalVar, attach_targets  # noqa: E402
from femforge_workbench.persist import LocalJsonStore  # noqa: E402
from femforge_workbench.registry import MONITORS, resolve  # noqa: E402
from tests.workbench.tree import find, prop  # noqa: E402


FIXED_DT = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
RUN_ID = "bright-otter-11111111"
DEAD_PID = 999999
"""A pid Windows never allocates (pids are multiples of four)."""

LIVE_PID = 4242
HOST = "box"
TIMEOUT = 30.0
TRACE = "Traceback (most recent call last):\n  RuntimeError: gmsh died"
SWEEP_ID = "sweep-a"


def _shell(name: str, root: Path) -> Workbench:
    return Workbench(name, title="Monitors", server=get_server(f"monitors-{name}"), persist=LocalJsonStore(root / "persist"), user="tester")


async def _until(predicate: Callable[[], bool], timeout: float = TIMEOUT) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise TimeoutError("the condition did not come true in time")

        await asyncio.sleep(0.01)


def _index_run(telemetry: Telemetry, runs_dir: Path, run_id: str, status: str) -> None:
    row: dict[str, object] = {name: None for name in RUN_INDEX_SCHEMA.names()}
    row.update(
        month=FIXED_DT.date().replace(day=1),
        run_id=run_id,
        runs_dir=str(runs_dir),
        status=status,
        created_at=FIXED_DT,
        indexed_at=FIXED_DT,
        source="scan",
    )
    _ = telemetry.runs.append(pl.DataFrame([row], schema=RUN_INDEX_SCHEMA))


def _running_bundle(runs_dir: Path, run_id: str) -> Path:
    manifest = RunManifest.build(
        SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step")),
        status="running",
        run_id=run_id,
        created_at=FIXED_DT,
    )
    manifest.stage_history = [
        StageEvent(stage=Stage.MESH, started_at=FIXED_DT, finished_at=FIXED_DT + timedelta(seconds=2)),
        StageEvent(stage=Stage.RUN_SOLVER, started_at=FIXED_DT + timedelta(seconds=3)),
    ]
    sim_dir = runs_dir / run_id
    sim_dir.mkdir(parents=True)
    write_atomic(sim_dir / BundleFile.MANIFEST, manifest.model_dump_json(indent=2))
    _ = ConvergenceTargetsFile.of({("heat equation", "NS"): LoopTargets(max_iterations=20, tolerance=1e-5)}).write(sim_dir)

    return sim_dir


def _sample(wall: float, solver: str, loop: str, iteration: int, relc: float) -> str:
    return json.dumps({"wall_time": wall, "solver": solver, "loop": loop, "iter": iteration, "nrm": 1.0, "relc": relc})


def _convergence_frame() -> pl.DataFrame:
    rows: list[dict[str, object]] = [
        {"wall_time": 0.1, "solver": "heat equation", "loop": "NS", "iter": 1, "nrm": 1.0, "relc": 1e-1},
        {"wall_time": 0.2, "solver": "heat equation", "loop": "NS", "iter": 2, "nrm": 1.0, "relc": 1e-3},
        {"wall_time": 0.3, "solver": "heat equation", "loop": "NS", "iter": 3, "nrm": 1.0, "relc": None},
        {"wall_time": 0.4, "solver": "mag", "loop": "SS", "iter": 1, "nrm": 1.0, "relc": 1e-2},
        {"wall_time": 0.5, "solver": "mag", "loop": "SS", "iter": 2, "nrm": 1.0, "relc": 1e-4},
    ]

    return pl.DataFrame(rows, schema=CONVERGENCE_SCHEMA)


def _event(point: str, state: SweepState, at: datetime, *, pid: int = LIVE_PID, error: str | None = None, duration: float | None = None) -> dict[str, object]:
    return {
        SweepEventColumn.SWEEP_ID: SWEEP_ID,
        SweepEventColumn.POINT_KEY: point,
        SweepEventColumn.POINT_NAME: f"point {point}",
        SweepEventColumn.STATE: state.value,
        SweepEventColumn.AT: at,
        SweepEventColumn.HOST: HOST,
        SweepEventColumn.PID: pid,
        SweepEventColumn.ORIGIN: "solved",
        SweepEventColumn.ERROR: error,
        SweepEventColumn.DURATION_S: duration,
    }


def _latest() -> pl.DataFrame:
    rows: list[dict[str, object]] = [
        _event("p1", SweepState.DONE, FIXED_DT, duration=9.5),
        _event("p2", SweepState.RUNNING, FIXED_DT + timedelta(seconds=1), pid=DEAD_PID),
        _event("p3", SweepState.RUNNING, FIXED_DT + timedelta(seconds=2)),
        _event("p4", SweepState.QUEUED, FIXED_DT + timedelta(seconds=3)),
        _event("p5", SweepState.FAILED, FIXED_DT + timedelta(seconds=4), error=TRACE, duration=4.0),
        _event("p6", SweepState.SKIPPED, FIXED_DT + timedelta(seconds=5)),
    ]

    return pl.DataFrame(rows, schema=SWEEP_EVENT_SCHEMA)


def _log_row(when: datetime, level: LogLevel, process: str, message: str, **values: object) -> dict[str, object]:
    row: dict[str, object] = {name: None for name in LOG_SCHEMA.names()}
    row.update(
        date=when.date(),
        process=process,
        time=when,
        level=level.value,
        level_no=20,
        name="tests",
        function="f",
        line=1,
        message=message,
        host=HOST,
        pid=LIVE_PID,
        thread="MainThread",
    )
    row.update(values)

    return row


def _log_rows() -> list[dict[str, object]]:
    return [
        _log_row(FIXED_DT, LogLevel.INFO, "cli", "mesh built", run_id="run-1", extra=json.dumps({"elements": 12})),
        _log_row(FIXED_DT + timedelta(minutes=1), LogLevel.WARNING, "sensitivity_sweep", "mesh coarse", sweep_id="sweep-1", point_key="p1"),
        _log_row(FIXED_DT + timedelta(minutes=2), LogLevel.ERROR, "cli", "solver diverged", exception="Traceback\nValueError: boom"),
    ]


def _log_frame() -> pl.DataFrame:
    return pl.DataFrame(_log_rows(), schema=LOG_SCHEMA)


# --- Run monitor helpers --------------------------------------------------------


def test_convergence_figure_draws_one_trace_per_loop_on_a_log_axis_with_tolerance_lines() -> None:
    targets = ConvergenceTargetsFile.of({("heat equation", "NS"): LoopTargets(max_iterations=20, tolerance=1e-5), ("mag", "SS"): LoopTargets(max_iterations=3, tolerance=None)})

    figure = convergence_figure(_convergence_frame(), targets, revision="run-1")

    payload = cast("dict[str, object]", figure.to_plotly_json())
    traces = cast("list[dict[str, object]]", payload["data"])
    layout = cast("dict[str, object]", payload["layout"])
    assert [trace["name"] for trace in traces] == ["heat equation NS", "mag SS", "heat equation NS tolerance"]
    assert traces[0]["x"] == [0.1, 0.2] and traces[0]["y"] == [1e-1, 1e-3]
    assert traces[2]["x"] == [0.0, 0.5] and traces[2]["y"] == [1e-5, 1e-5]
    assert cast("dict[str, object]", traces[2]["line"])["dash"] == "dash"
    assert cast("dict[str, object]", layout["yaxis"])["type"] == "log"
    assert layout["uirevision"] == "run-1"


def test_convergence_figure_of_an_empty_frame_still_draws_the_tolerances() -> None:
    targets = ConvergenceTargetsFile.of({("heat equation", "NS"): LoopTargets(max_iterations=20, tolerance=1e-5)})

    figure = convergence_figure(pl.DataFrame(schema=CONVERGENCE_SCHEMA), targets)

    traces = cast("list[dict[str, object]]", cast("dict[str, object]", figure.to_plotly_json())["data"])
    assert len(traces) == 1 and traces[0]["x"] == [0.0, 1.0]


def test_run_items_and_heartbeat_text_read_the_live_columns() -> None:
    frame = pl.DataFrame(
        {
            "run_id": ["a", "b"],
            "status": ["running", "ok"],
            "stale": [False, None],
            "heartbeat_age_s": [3.2, None],
            "pid": [LIVE_PID, None],
        }
    )

    items = run_items(frame)

    assert items[0] == {ItemField.TITLE: "● a running", ItemField.VALUE: "a"}
    assert items[1] == {ItemField.TITLE: "b ok", ItemField.VALUE: "b"}
    assert heartbeat_text(frame.filter(pl.col("run_id") == "a"), "a") == f"heartbeat 3 s ago · pid {LIVE_PID}"
    assert heartbeat_text(frame.with_columns(pl.lit(value=True).alias("stale")), "a").endswith("· stale")
    assert heartbeat_text(frame, "zzz") == ""


# --- Sweep monitor helpers -------------------------------------------------------


def test_point_rows_group_by_state_and_mark_a_running_point_whose_worker_is_gone() -> None:
    frame = point_rows(_latest(), alive=lambda pid: pid == LIVE_PID, host=HOST)

    states = frame.get_column(PointRow.STATE).to_list()
    labels = frame.get_column(GROUP_COLUMN).to_list()
    assert states == [STALLED, "running", "queued", "failed", "done", "skipped"]
    assert labels[0] == f"{STALLED}  ·  1 rows"
    assert frame.get_column(PointRow.POINT).to_list()[0] == "point p2"
    assert frame.get_column(PointRow.PID).to_list()[0] == DEAD_PID
    assert frame.get_column(PointRow.AT).to_list()[0] == "12:00:01"
    assert frame.get_column(PointRow.DURATION).to_list()[4] == 9.5
    assert point_rows(_latest(), alive=lambda pid: pid == LIVE_PID, host="elsewhere").get_column(PointRow.STATE).to_list()[0] == "running"


def test_progress_reports_the_settled_share_the_counts_and_the_pace() -> None:
    summary = SweepSummary(queued=2, running=1, done=4, failed=1, skipped=2, rate_per_h=12.0, eta_s=900.0, elapsed_s=125.0)

    shown = progress(summary, 10)

    assert shown.percent == 70.0
    assert shown.counts == "7/10 settled · 4 done · 1 failed · 2 skipped · 1 running · 2 queued"
    assert shown.pace == "12.0/h · ETA 15m 00s · elapsed 2m 05s"
    assert progress(summary, 0).percent == 0.0
    assert progress(summary, 2).percent == 100.0, "a journal with more settled points than the launch declared must not slide the bar out of its box"
    assert "ETA —" in progress(SweepSummary(0, 0, 0, 0, 0, 0.0, None, 0.0), 1).pace


def test_failure_items_and_the_traceback_of_a_point() -> None:
    latest = _latest()

    items = failure_items(latest)

    assert items == [{FailureField.KEY: "p5", FailureField.NAME: "point p5", FailureField.AT: "12:00:04"}]
    assert traceback_of(latest, "p5") == TRACE
    assert traceback_of(latest, "p1") == ""
    assert traceback_of(latest, "absent") == ""


def test_sweep_items_and_the_default_pick_an_unfinished_sweep_first() -> None:
    frame = pl.DataFrame(
        {
            "sweep_id": ["newer", "older"],
            "description": ["Smoke sweep", None],
            "total": [5, None],
            "finished_at": [FIXED_DT, None],
        }
    )

    items = sweep_items(frame)

    assert items[0] == {ItemField.TITLE: "newer · Smoke sweep · 5 points", ItemField.VALUE: "newer"}
    assert items[1] == {ItemField.TITLE: "older · running", ItemField.VALUE: "older"}
    assert default_sweep(frame) == "older"
    assert default_sweep(frame.filter(pl.col("sweep_id") == "newer")) == "newer"
    assert default_sweep(frame.clear()) == ""


# --- Log browser helpers ---------------------------------------------------------


def test_log_filters_narrow_an_in_memory_frame() -> None:
    frame = _log_frame()

    def messages(filters: LogFilters) -> list[str]:
        return cast("list[str]", filters.rows(frame, 10).get_column(LogColumn.MESSAGE).to_list())

    assert messages(LogFilters()) == ["mesh built", "mesh coarse", "solver diverged"]
    assert messages(LogFilters(levels=("WARNING",))) == ["mesh coarse"]
    assert messages(LogFilters(processes=("cli",))) == ["mesh built", "solver diverged"]
    assert messages(LogFilters(run_id="run-1")) == ["mesh built"]
    assert messages(LogFilters(sweep_id="sweep-1")) == ["mesh coarse"]
    assert messages(LogFilters(text="mesh")) == ["mesh built", "mesh coarse"]
    assert messages(LogFilters(since=FIXED_DT + timedelta(seconds=30), until=FIXED_DT + timedelta(seconds=90))) == ["mesh coarse"]
    assert LogFilters().rows(frame, 2).get_column(LogColumn.MESSAGE).to_list() == ["mesh coarse", "solver diverged"]


def test_parse_moment_reads_iso_timestamps_as_utc() -> None:
    assert parse_moment("2026-09-05T12:00:00") == FIXED_DT
    assert parse_moment("2026-09-05 12:00:00+00:00") == FIXED_DT
    assert parse_moment("") is None
    assert parse_moment("yesterday") is None


def test_journal_rows_parse_the_records_and_skip_a_malformed_line() -> None:
    lines = [json.dumps(row, default=json_default) for row in _log_rows()] + ["not json at all", json.dumps([1, 2])]

    frame = journal_rows(lines)

    assert frame.schema == LOG_SCHEMA
    assert frame.height == 3
    assert frame.get_column(LogColumn.TIME).to_list()[0] == FIXED_DT
    assert frame.get_column(LogColumn.DATE).to_list()[0] == date(2026, 9, 5)
    assert frame.get_column(LogColumn.LEVEL).to_list() == ["INFO", "WARNING", "ERROR"]
    assert journal_rows([]).schema == LOG_SCHEMA


def test_journal_tails_report_a_vanished_journal_once_with_the_last_time_read(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    journal = live / f"{TelemetryDir.LOGS}-{HOST}-{DEAD_PID}{JOURNAL_SUFFIX}"
    lines = [
        json.dumps(
            _log_row(
                FIXED_DT + timedelta(seconds=index),
                LogLevel.INFO,
                "worker",
                f"m{index}",
            ),
            default=json_default,
        )
        for index in range(3)
    ]
    _ = journal.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    odd = live / f"{TelemetryDir.LOGS}-odd{JOURNAL_SUFFIX}"
    _ = odd.write_text(lines[0] + "\n", encoding="utf-8")
    tails = JournalTails(live, skip="")

    assert len(tails.read_new()) == 3
    assert tails.seen[journal] == FIXED_DT + timedelta(seconds=1)
    assert tails.folded() == []

    with journal.open("a", encoding="utf-8") as stream:
        _ = stream.write(lines[2] + "\n")

    assert len(tails.read_new()) == 1
    assert tails.seen[journal] == FIXED_DT + timedelta(seconds=2)

    journal.unlink()
    odd.unlink()

    assert tails.folded() == [Folded(HOST, DEAD_PID, FIXED_DT + timedelta(seconds=2))]
    assert tails.folded() == []


def test_journal_tails_seeking_the_end_remember_where_each_journal_stood(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    journal = live / f"{TelemetryDir.LOGS}-{HOST}-{DEAD_PID}{JOURNAL_SUFFIX}"
    _ = journal.write_text(
        json.dumps(
            _log_row(
                FIXED_DT,
                LogLevel.INFO,
                "worker",
                "m0",
            ),
            default=json_default,
        )
        + "\n"
        + "not json\n",
        encoding="utf-8",
    )
    empty = live / f"{TelemetryDir.LOGS}-{HOST}-{DEAD_PID + 1}{JOURNAL_SUFFIX}"
    _ = empty.write_text("", encoding="utf-8")
    tails = JournalTails(live, skip="")

    tails.seek_end()

    assert tails.read_new() == []
    assert tails.seen == {journal: None, empty: None}
    assert last_line_time(live / "missing.jsonl") is None
    assert line_time(json.dumps({"time": 5})) is None

    journal.unlink()
    empty.unlink()

    assert sorted(tails.folded(), key=lambda folded: folded.pid) == [Folded(HOST, DEAD_PID, None), Folded(HOST, DEAD_PID + 1, None)]


def test_detail_json_shows_the_extras_and_the_traceback_lines() -> None:
    row: Row = {LogColumn.EXTRA: json.dumps({"elements": 12}), LogColumn.EXCEPTION: "Traceback\nValueError: boom"}

    detail = cast("dict[str, object]", json.loads(detail_json(row)))

    assert detail == {LogColumn.EXTRA: {"elements": 12}, LogColumn.EXCEPTION: ["Traceback", "ValueError: boom"]}
    assert cast("dict[str, object]", json.loads(detail_json({LogColumn.EXTRA: "{oops"})))[LogColumn.EXTRA] == "{oops"
    assert cast("dict[str, object]", json.loads(detail_json({}))) == {LogColumn.EXTRA: "", LogColumn.EXCEPTION: []}


# --- Terminal helpers ------------------------------------------------------------


class FakePty:
    """A pty whose output the test feeds through a queue."""

    def __init__(self) -> None:
        self.out: queue.Queue[str | None] = queue.Queue()
        self.written: list[str] = []
        self.sizes: list[tuple[int, int]] = []
        self.alive = True

    def read(self, size: int) -> str:
        item = self.out.get()
        if item is None:
            raise EOFError

        return item[:size]

    def write(self, data: str) -> int:
        self.written.append(data)

        return len(data)

    def setwinsize(self, rows: int, cols: int) -> None:
        self.sizes.append((rows, cols))

    def isalive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.alive = False
        self.out.put(None)


def test_the_pty_bridge_pumps_output_forwards_input_and_resizes_and_ends_the_child() -> None:
    pty = FakePty()
    delivered: list[str] = []
    exited = threading.Event()

    def deliver(text: str) -> None:
        delivered.append(text)
        if text == EXITED:
            exited.set()

    bridge = PtyBridge(pty, deliver)
    pty.out.put("PS C:\\> ")
    bridge.input("dir\r")
    bridge.resize(120, 40)
    bridge.resize(0, 40)
    pty.out.put(None)

    assert exited.wait(TIMEOUT)
    assert delivered == ["PS C:\\> ", EXITED]
    assert pty.written == ["dir\r"]
    assert pty.sizes == [(40, 120)]

    bridge.stop()
    bridge.input("ignored")

    assert not pty.alive
    assert pty.written == ["dir\r"]


def test_a_stopped_bridge_does_not_announce_the_exit_it_asked_for() -> None:
    pty = FakePty()
    delivered: list[str] = []

    bridge = PtyBridge(pty, delivered.append)
    bridge.stop()
    bridge.thread.join(TIMEOUT)

    assert not bridge.thread.is_alive()
    assert delivered == []


def test_attach_targets_list_the_shell_and_the_elmer_containers() -> None:
    def which(name: str) -> str | None:
        return "C:/docker.exe" if name == "docker" else None

    def listing(argv: Sequence[str]) -> str:
        assert list(argv)[:3] == ["C:/docker.exe", "ps", "--filter"]

        return f"{CONTAINER_PREFIX}abc\n{CONTAINER_PREFIX}def\nhomeassistant\n"

    targets = attach_targets(listing, which=which)

    assert [target.label for target in targets] == [LOCAL_LABEL, f"docker {CONTAINER_PREFIX}abc", f"docker {CONTAINER_PREFIX}def"]
    assert targets[1].argv == ("C:/docker.exe", "exec", "-it", f"{CONTAINER_PREFIX}abc", "bash")


def test_attach_targets_fall_back_to_the_shell_without_docker() -> None:
    def failing(argv: Sequence[str]) -> str:
        raise subprocess.CalledProcessError(1, list(argv))

    def missing(argv: Sequence[str]) -> str:
        raise FileNotFoundError(argv[0])

    def docker_only(name: str) -> str | None:
        return "docker" if name == "docker" else None

    assert [target.label for target in attach_targets(failing, which=lambda _: None)] == [LOCAL_LABEL]
    assert [target.label for target in attach_targets(failing, which=docker_only)] == [LOCAL_LABEL]
    assert [target.label for target in attach_targets(missing, which=docker_only)] == [LOCAL_LABEL]


def test_the_terminal_panel_spawns_on_open_forwards_input_and_ends_the_child(tmp_path: Path) -> None:
    app = _shell("terminal", tmp_path)
    fake = FakePty()
    spawned: list[tuple[tuple[str, ...], int, int]] = []

    def spawn(argv: Sequence[str], rows: int, cols: int) -> FakePty:
        spawned.append((tuple(argv), rows, cols))

        return fake

    panel = TerminalPanel(app, "terminal", "Terminal", spawn=spawn, targets=lambda: [Target(LOCAL_LABEL, ("shell.exe",))])
    asyncio.run(panel.refresh_targets())
    panel.on_resize({"cols": 100, "rows": 30})
    panel.on_opened()

    assert panel.mode is Mode.PTY
    assert spawned == [(("shell.exe",), 30, 100)]
    assert app.state[panel.key(TerminalVar.STATUS)] == LOCAL_LABEL

    panel.on_input("dir\r")
    panel.on_resize({"cols": 90, "rows": 20})
    panel.on_resize("not an event")
    panel.restart()

    assert fake.written == ["dir\r"]
    assert fake.sizes == [(20, 90)]
    assert len(spawned) == 2 and spawned[1] == (("shell.exe",), 20, 90)

    panel.stop()

    assert panel.bridge is None and not fake.alive


def test_a_host_without_a_pty_mirrors_the_log_and_locks_the_target_picker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("terminal-mirror", tmp_path)
    monkeypatch.setattr(terminal, "pty_spawner", lambda: None)

    panel = TerminalPanel(app, "terminal", "Terminal", targets=lambda: [Target(LOCAL_LABEL, ("shell.exe",))])
    template = cast("dict[str, object]", app.state[f"trame__template_{panel.template_name}"])

    assert panel.mode is Mode.MIRROR
    assert prop(find(template, "ff-select")[0], "disabled") is True, "a target can only be attached through a pty"
    assert str(app.state[panel.key(TerminalVar.STATUS)]).startswith("no pty available")


# --- The dock ------------------------------------------------------------------


def test_the_monitors_spec_resolves_to_a_split_of_the_four_panels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))

    factory = resolve(MONITORS.target)
    assert callable(factory)
    root = factory()

    assert isinstance(root, Split)
    assert [panel.panel_id for panel in panels(root)] == [str(member) for member in MonitorPanel]


def test_the_dock_builds_every_monitor_over_an_empty_telemetry_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    app = _shell("monitors-build", tmp_path)
    header_before = len(app.header.root.children)

    app.add(workbench())

    assert app.panel_ids == [str(member) for member in MonitorPanel]
    monitor = app.panel(MonitorPanel.RUN_MONITOR, RunMonitorPanel)
    sweeps = app.panel(MonitorPanel.SWEEP_MONITOR, SweepMonitorPanel)
    assert app.state[monitor.key(RunVar.ITEMS)] == []
    assert app.state[sweeps.key(SweepVar.SWEEP)] == ""

    app.state[Shared.SELECTED_RUN_ID] = "older-otter-00000000"

    assert monitor.items() == [{ItemField.TITLE: "older-otter-00000000", ItemField.VALUE: "older-otter-00000000"}], "a run the newest-N query misses still names the picker's trigger"
    assert app.panel(MonitorPanel.LOG_BROWSER, LogBrowserPanel).table.frame.height == 0
    assert isinstance(app.panel(MonitorPanel.TERMINAL, TerminalPanel), TerminalPanel)
    assert len(app.header.root.children) == header_before + 2, "the run and the sweep picker are built into the app bar"


def test_the_monitor_panels_bind_their_pickers_strips_and_viewers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    app = _shell("monitors-template", tmp_path)
    app.add(workbench())
    monitor = app.panel(MonitorPanel.RUN_MONITOR, RunMonitorPanel)
    sweeps = app.panel(MonitorPanel.SWEEP_MONITOR, SweepMonitorPanel)
    browser = app.panel(MonitorPanel.LOG_BROWSER, LogBrowserPanel)

    header = cast("dict[str, object]", app.state["trame__template_app-header"])
    run_panel = cast("dict[str, object]", app.state[f"trame__template_{monitor.template_name}"])
    sweep_panel = cast("dict[str, object]", app.state[f"trame__template_{sweeps.template_name}"])
    log_panel = cast("dict[str, object]", app.state[f"trame__template_{browser.template_name}"])

    assert [prop(node, "value") for node in find(header, "ff-select")] == [{"js": str(Shared.SELECTED_RUN_ID)}, {"js": sweeps.key(SweepVar.SWEEP)}]
    assert [prop(node, "value") for node in find(run_panel, "ff-toggle-group-item")] == [str(BundleFile.SOLVER_LOG), str(BundleFile.RUN_LOG)]
    assert prop(find(run_panel, "ff-badge")[0], "variant") == {"js": monitor.key(RunVar.STATUS_VARIANT)}
    assert prop(find(sweep_panel, "ff-progress")[0], "value") == {"js": sweeps.key(SweepVar.PERCENT)}
    assert {"js": sweeps.key(SweepVar.FAILURES)} in [prop(node, "items") for node in find(sweep_panel, "ReactFor")]
    assert prop(find(sweep_panel, "ff-code")[0], "value") == {"js": sweeps.key(SweepVar.TRACEBACK)}
    assert app.state[browser.key(LogVar.LEVEL)] == ANY and app.state[browser.key(LogVar.LIVE)] is False
    assert prop(find(log_panel, "ff-switch")[0], "checked") == {"js": browser.key(LogVar.LIVE)}


def test_the_run_monitor_follows_a_growing_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "telemetry"
    runs_dir = tmp_path / "runs"
    sim_dir = _running_bundle(runs_dir, RUN_ID)
    _index_run(Telemetry(root), runs_dir, RUN_ID, "running")
    monkeypatch.setenv(TELEMETRY_ENV, str(root))

    async def run() -> tuple[list[Row], int, list[str], list[str], str]:
        app = _shell("run-monitor", tmp_path)
        app.add(workbench())
        monitor = app.panel(MonitorPanel.RUN_MONITOR, RunMonitorPanel)
        monitor.feed.period = 0.02
        app.state[Shared.SELECTED_RUN_ID] = RUN_ID

        started = app.server.start(exec_mode="task", port=0, open_browser=False, show_connection_info=False, disable_logging=True, timeout=0)
        assert started is not None
        _ = await app.server.ready

        monitor.on_visible(True)
        await _until(lambda: monitor.feed.polls >= 1 and monitor.watch is not None)
        stages = cast("list[Row]", app.state[monitor.key(RunVar.STAGES)])
        before = monitor.convergence.height

        with (sim_dir / BundleFile.CONVERGENCE).open("a", encoding="utf-8") as stream:
            _ = stream.write(_sample(0.1, "heat equation", "NS", 1, 1e-1) + "\n" + _sample(0.2, "heat equation", "NS", 2, 1e-3) + "\n")

        with (sim_dir / BundleFile.SOLVER_LOG).open("a", encoding="utf-8") as stream:
            _ = stream.write("ELMER SOLVER STARTING\nComputeChange: NS (ITER=1)\n")

        _ = (sim_dir / BundleFile.RUN_LOG).write_text("stage run_solver started\n", encoding="utf-8")
        await _until(lambda: monitor.convergence.height == 2 and len(monitor.solver_lines) == 2 and len(monitor.run_lines) == 1)
        samples = str(app.state[monitor.key(RunVar.SAMPLES)])

        monitor.on_visible(False)
        await app.server.stop()
        await started

        return stages, before, list(monitor.solver_lines), list(monitor.run_lines), samples

    stages, before, solver_lines, run_lines, samples = asyncio.run(run())

    assert [row["stage"] for row in stages] == ["Mesh", "Run Solver"]
    assert [row["state"] for row in stages] == ["Done", "Running"]
    assert before == 0
    assert solver_lines == ["ELMER SOLVER STARTING", "ComputeChange: NS (ITER=1)"]
    assert run_lines == ["stage run_solver started"]
    assert samples == "2 samples"


def test_the_sweep_monitor_publishes_a_synthetic_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "telemetry"
    telemetry = Telemetry(root)
    journal = SweepJournal(telemetry, sweep_id=SWEEP_ID, process="tests")
    journal.start("Smoke sweep", total=4, max_workers=2)
    journal.event("p1", SweepState.QUEUED, point_name="one")
    journal.event("p2", SweepState.RUNNING, point_name="two")
    journal.event("p3", SweepState.FAILED, point_name="three", error=TRACE, duration_s=4.0)
    journal.event("p4", SweepState.DONE, point_name="four", duration_s=3.0)
    monkeypatch.setenv(TELEMETRY_ENV, str(root))

    async def run() -> tuple[str, float, str, list[Row], list[str], str]:
        app = _shell("sweep-monitor", tmp_path)
        app.add(workbench())
        monitor = app.panel(MonitorPanel.SWEEP_MONITOR, SweepMonitorPanel)
        monitor.feed.period = 0.02
        selected = str(app.state[monitor.key(SweepVar.SWEEP)])

        started = app.server.start(exec_mode="task", port=0, open_browser=False, show_connection_info=False, disable_logging=True, timeout=0)
        assert started is not None
        _ = await app.server.ready

        monitor.on_visible(True)
        await _until(lambda: monitor.feed.polls >= 1 and monitor.table.frame.height == 4)
        monitor.show_failure("p3")
        percent = cast("float", app.state[monitor.key(SweepVar.PERCENT)])
        counts = str(app.state[monitor.key(SweepVar.COUNTS)])
        failures = cast("list[Row]", app.state[monitor.key(SweepVar.FAILURES)])
        states = cast("list[str]", monitor.table.frame.get_column(PointRow.STATE).to_list())
        traceback = str(app.state[monitor.key(SweepVar.TRACEBACK)])

        monitor.on_visible(False)
        await app.server.stop()
        await started

        return selected, percent, counts, failures, states, traceback

    try:
        selected, percent, counts, failures, states, traceback = asyncio.run(run())
    finally:
        journal.finish()

    assert selected == SWEEP_ID
    assert percent == 50.0
    assert counts.startswith("2/4 settled · 1 done · 1 failed · 0 skipped · 1 running · 1 queued")
    assert [item[FailureField.KEY] for item in failures] == ["p3"]
    assert states == ["running", "queued", "failed", "done"]
    assert traceback == TRACE


def test_the_log_browser_filters_the_store_and_follows_the_journals_and_the_tap_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "telemetry"
    telemetry = Telemetry(root)
    _ = telemetry.logs.append(_log_frame())
    monkeypatch.setenv(TELEMETRY_ENV, str(root))

    async def run() -> tuple[int, int, list[str], int]:
        app = _shell("log-browser", tmp_path)
        app.add(workbench())
        browser = app.panel(MonitorPanel.LOG_BROWSER, LogBrowserPanel)
        browser.feed.period = 0.02

        started = app.server.start(exec_mode="task", port=0, open_browser=False, show_connection_info=False, disable_logging=True, timeout=0)
        assert started is not None
        _ = await app.server.ready

        await _until(lambda: browser.table.frame.height == 3)
        with app.state:
            app.state[browser.key(LogVar.LEVEL)] = LogLevel.WARNING.value

        await _until(lambda: browser.table.frame.height == 1)
        narrowed = browser.table.frame.height
        with app.state:
            app.state[browser.key(LogVar.LEVEL)] = ANY
            app.state[browser.key(LogVar.LIVE)] = True

        browser.on_visible(True)
        await _until(lambda: browser.live_mode and browser.feed.running and browser.stream.running and browser.live.height >= 3)
        seeded = browser.live.height

        def messages() -> list[str]:
            return cast("list[str]", browser.live.get_column(LogColumn.MESSAGE).to_list())

        journal = root / TelemetryDir.LIVE / f"{TelemetryDir.LOGS}-{HOST}-{DEAD_PID}{JOURNAL_SUFFIX}"
        journal.parent.mkdir(parents=True, exist_ok=True)

        def other(index: int) -> dict[str, object]:
            return _log_row(FIXED_DT + timedelta(hours=1, seconds=index), LogLevel.INFO, "worker", f"from another process {index}", pid=DEAD_PID)

        lines = [json.dumps(other(index), default=json_default) for index in range(2)]
        _ = journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
        await _until(lambda: "from another process 1" in messages())

        # The other process exits: its journal is folded into the store, its last line included, and deleted before the next poll.
        folded = [other(index) for index in range(1, 4)]
        _ = telemetry.logs.append(pl.DataFrame(folded, schema=LOG_SCHEMA))
        journal.unlink()
        await _until(lambda: "from another process 3" in messages())

        log.info("from the workbench itself")
        await _until(lambda: "from the workbench itself" in messages())
        shown = browser.table.frame.height

        browser.on_visible(False)
        await app.server.stop()
        await started

        return narrowed, seeded, messages(), shown

    narrowed, seeded, messages, shown = asyncio.run(run())

    assert narrowed == 1
    assert seeded >= 3
    assert messages[:3] == ["mesh built", "mesh coarse", "solver diverged"]
    assert [message for message in messages if message.startswith("from another process")] == [f"from another process {index}" for index in range(4)]
    assert "from the workbench itself" in messages
    assert shown == len(messages)
