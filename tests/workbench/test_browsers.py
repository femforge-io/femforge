"""The run and artifact browsers: their pure row/filter helpers, the rendered panels, the browsers dock, and a panel's polling feed."""

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import polars as pl
import pytest

from femforge.frames import Solid
from femforge.artifacts.local import LocalArtifactStore
from femforge.common.utils import title
from femforge.common.vector import Vec3
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.physical.units import quantity
from femforge.run.bundle import write_bundle, write_manifest
from femforge.run.inspect import Run
from femforge.run.manifest import RunManifest
from femforge.run.result import Result
from femforge.run.retention import Preset
from femforge.run.stages import Stage, StageEvent
from femforge.telemetry.artifacts import ArtifactQuery
from femforge.telemetry.paths import TELEMETRY_ENV, Telemetry
from femforge.telemetry.runs import RunQuery
from femforge.telemetry.schemas import ARTIFACT_INDEX_SCHEMA, RUN_INDEX_SCHEMA, ArtifactColumn, RunColumn


pytest.importorskip("trame")
pytest.importorskip("pyvista")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Workbench  # noqa: E402
from femforge_workbench.dock import Split, panels  # noqa: E402
from femforge_workbench.feeds import Feed  # noqa: E402
from femforge_workbench.grid import GROUP_COLUMN, Row  # noqa: E402
from femforge_workbench.panels import Cls, Shared  # noqa: E402
from femforge_workbench.panels.artifacts import (  # noqa: E402
    ANY,
    ArtifactBrowserPanel,
    ArtifactDetailPanel,
    ArtifactFilters,
    ArtifactRow,
    PairField,
    artifact_rows,
    copy_handler,
    runs_using,
    spec_summary,
)
from femforge_workbench.panels.artifacts import BrowserVar as ArtifactVar, DetailVar as ArtifactDetailVar  # noqa: E402
from femforge_workbench.panels.browsers import BrowserPanel, workbench  # noqa: E402
from femforge_workbench.panels.runs import (  # noqa: E402
    BrowserVar,
    ComponentColumn,
    DetailVar,
    PurgeVar,
    RunBrowserPanel,
    RunDetailPanel,
    RunFilters,
    RunRow,
    STARTING,
    StageField,
    StageState,
    StatusVariant,
    UNKNOWN_VARIANT,
    component_rows,
    format_wall_time,
    run_rows,
    stage_rows,
)
from femforge_workbench.panels.table import StateVar as TableVar  # noqa: E402
from femforge_workbench.widgets.table import ColumnSpec, LayoutDefaults, TableOptions  # noqa: E402
from femforge_workbench.persist import LocalJsonStore  # noqa: E402
from femforge_workbench.registry import BROWSERS, resolve  # noqa: E402
from tests.workbench.tree import find, prop, texts  # noqa: E402


FIXED_DT = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
MESH_KEY = "96e36c7aebeffdfd91702525bcf080e94424bb496e8ac510e3a95052300a1152"
STEP_KEY = "d525586a9cd6cd14f45b65d2ae0f267ab9f2fb8bd6dceee9b7d722e9870718a1"
RUN_ID = "bright-otter-11111111"
WATCH_TIMEOUT = 10.0
"""Seconds a watch test may wait for the panel to catch up before it fails instead of hanging."""


def _row(schema: pl.Schema, **values: object) -> dict[str, object]:
    filled: dict[str, object] = {name: None for name in schema.names()}
    filled.update(values)

    return filled


def _run_frame(runs_dir: Path) -> pl.DataFrame:
    def row(run_id: str, status: str, physics: str, **values: object) -> dict[str, object]:
        return _row(
            RUN_INDEX_SCHEMA,
            month=FIXED_DT.date().replace(day=1),
            run_id=run_id,
            runs_dir=str(runs_dir),
            status=status,
            physics=physics,
            created_at=FIXED_DT,
            indexed_at=FIXED_DT,
            stages_done=3,
            source="scan",
            **values,
        )

    rows = [
        row(
            RUN_ID,
            "ok",
            "thermal",
            description="stall snapshot",
            tags=json.dumps({"experiment": "thermal_limit"}),
            artifact_keys=[STEP_KEY],
            mesh_key=MESH_KEY,
            wall_time=2.0,
            temperature_max=330.0,
            bundle_bytes=4_000_000,
            model_dim=2,
            element_count=13281,
        ),
        row(
            "calm-otter-22222222",
            "error",
            "magnetostatics_2d",
            description="force sweep",
            tags=json.dumps({"experiment": "force"}),
            artifact_keys=[],
            mesh_key="other",
        ),
        row(
            "dim-badger-33333333",
            "ok",
            "magnetostatics_2d",
            description="force sweep",
            tags=json.dumps({"experiment": "force"}),
            artifact_keys=[MESH_KEY],
            mesh_key="other",
        ),
    ]

    return pl.DataFrame(rows, schema=RUN_INDEX_SCHEMA)


def _artifact_frame(store_root: Path) -> pl.DataFrame:
    def row(kind: str, name: str, key: str, spec_type: str, spec: dict[str, object], **values: object) -> dict[str, object]:
        return _row(
            ARTIFACT_INDEX_SCHEMA,
            kind=kind,
            name=name,
            key=key,
            store_root=str(store_root),
            created_at=FIXED_DT,
            indexed_at=FIXED_DT,
            spec_type=spec_type,
            producer_host="Nova",
            producer_pid=1234,
            file_count=4,
            total_bytes=2_500_000,
            files=["mesh.header"],
            spec=json.dumps(spec),
            **values,
        )

    rows = [
        row(
            "elmer_mesh",
            "steady-printer-96e36c7a",
            MESH_KEY,
            "femforge.run.kinds:MeshSpec",
            {"model_dim": 2, "order": 1, "size": 0.4, "nested": {"a": 1}},
        ),
        row("step", "jumbo-badger-d525586a", STEP_KEY, "femforge.geometry:GeometryArea", {"digest": "abc"}),
    ]

    return pl.DataFrame(rows, schema=ARTIFACT_INDEX_SCHEMA)


def _telemetry(root: Path, runs_dir: Path, store_root: Path) -> Telemetry:
    telemetry = Telemetry(root)
    _ = telemetry.runs.append(_run_frame(runs_dir))
    _ = telemetry.artifacts.append(_artifact_frame(store_root))

    return telemetry


def _bundle(tmp_path: Path) -> Path:
    manifest = RunManifest.build(SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step")), status="ok", run_id=RUN_ID, created_at=FIXED_DT)
    result = Result(
        frame=Solid(),
        status="ok",
        component_forces={"MAGNETS": quantity(Vec3(1.0, -2.0, 0.5), "newton")},
        component_torques={"MAGNETS": quantity(Vec3(0.0, 0.0, 0.25), "newton * meter")},
    )

    return write_bundle(tmp_path / RUN_ID, manifest, result)


# --- Artifact helpers ----------------------------------------------------------


def test_spec_summary_names_the_model_and_its_first_scalar_fields() -> None:
    spec = json.dumps({"model_dim": 2, "size": 0.25, "algorithm": "frontal", "extra": "dropped", "nested": {"a": 1}})

    assert spec_summary("femforge.run.kinds:MeshSpec", spec) == "MeshSpec  model_dim=2, size=0.25, algorithm=frontal"
    assert spec_summary("MeshSpec", json.dumps({"nested": {"a": 1}})) == "MeshSpec"
    assert spec_summary("MeshSpec", "not json") == "MeshSpec"
    assert spec_summary("MeshSpec", json.dumps([1, 2])) == "MeshSpec"


def test_artifact_rows_carry_the_listing_columns_and_the_store_path(tmp_path: Path) -> None:
    frame = _artifact_frame(tmp_path)

    rows = artifact_rows(frame)

    assert [row[ArtifactRow.KIND] for row in rows] == ["elmer_mesh", "step"]
    assert rows[0][ArtifactRow.KEY] == MESH_KEY[:12]
    assert rows[0][ArtifactRow.CREATED] == "2026-09-02 10:00"
    assert rows[0][ArtifactRow.MB] == 2.5
    assert rows[0][ArtifactRow.HOST] == "Nova"
    assert str(rows[0][ArtifactRow.SPEC]).startswith("MeshSpec  model_dim=2")
    assert rows[0][ArtifactRow.PATH] == str(tmp_path / "elmer_mesh" / "steady-printer-96e36c7a")


def test_runs_using_finds_a_key_among_the_inputs_or_as_the_mesh(tmp_path: Path) -> None:
    runs = _run_frame(tmp_path).lazy()

    assert runs_using(runs, MESH_KEY) == [RUN_ID, "dim-badger-33333333"]
    assert runs_using(runs, STEP_KEY) == [RUN_ID]
    assert runs_using(runs, "unknown") == []


def test_artifact_filters_narrow_by_kind_and_by_text(tmp_path: Path) -> None:
    telemetry = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")
    query = ArtifactQuery(telemetry.artifacts.scan(), LocalArtifactStore(tmp_path / "store"))

    def names(filters: ArtifactFilters) -> list[str]:
        return sorted(filters.apply(query).frame()[ArtifactColumn.NAME].to_list())

    assert names(ArtifactFilters()) == ["jumbo-badger-d525586a", "steady-printer-96e36c7a"]
    assert names(ArtifactFilters(kinds=("step",))) == ["jumbo-badger-d525586a"]
    assert names(ArtifactFilters(text="FRONTAL")) == []
    assert names(ArtifactFilters(text="badger")) == ["jumbo-badger-d525586a"]
    assert names(ArtifactFilters(text="meshspec")) == ["steady-printer-96e36c7a"]


def test_the_copy_handler_reads_the_state_variable_in_the_client() -> None:
    handler = copy_handler("artifact_detail_key")

    assert handler.value == 'navigator.clipboard.writeText(trame.state.get("artifact_detail_key"))'


# --- Run helpers ---------------------------------------------------------------


def test_format_wall_time_scales_with_the_duration() -> None:
    assert format_wall_time(None) == ""
    assert format_wall_time(2.02) == "2.0 s"
    assert format_wall_time(125.0) == "2m 05s"
    assert format_wall_time(3900.0) == "1h 05m"


def test_stage_rows_mark_the_last_unfinished_stage_running_and_earlier_ones_crashed() -> None:
    now = FIXED_DT + timedelta(seconds=30)
    history = [
        StageEvent(stage=Stage.MESH, started_at=FIXED_DT, finished_at=FIXED_DT + timedelta(seconds=4)),
        StageEvent(stage=Stage.RUN_SOLVER, started_at=FIXED_DT + timedelta(seconds=5)),
    ]

    running = stage_rows(history, now, status="running")
    crashed = stage_rows(history, now, status="error")

    assert [row[StageField.STATE] for row in running] == ["Done", "Running"]
    assert [row[StageField.STATE] for row in crashed] == ["Done", "Crashed"]
    assert running[0][StageField.SECONDS] == 4.0
    assert running[1][StageField.SECONDS] == 25.0
    assert running[1][StageField.VARIANT] == StatusVariant.RUNNING and crashed[1][StageField.VARIANT] == StatusVariant.ERROR
    assert "bg-primary" in str(running[1][StageField.DOT]) and "bg-destructive" in str(crashed[1][StageField.DOT])
    assert StatusVariant.of("") == StatusVariant.of("a status nobody named") == UNKNOWN_VARIANT, "an unrecognised status must not read as success"
    assert stage_rows([], now) == []


def test_stage_rows_treat_a_naive_timestamp_as_utc() -> None:
    history = [StageEvent(stage=Stage.MESH, started_at=datetime(2026, 9, 2, 10, 0, 0))]

    rows = stage_rows(history, FIXED_DT + timedelta(seconds=8))

    assert rows[0][StageField.SECONDS] == 8.0


def test_run_rows_format_the_table_and_flag_the_claimed_runs(tmp_path: Path) -> None:
    frame = _run_frame(tmp_path)

    rows = run_rows(frame, live={"calm-otter-22222222"})

    assert rows[0][RunRow.RUN_ID] == RUN_ID
    assert rows[0][RunRow.CREATED] == "2026-09-02 10:00"
    assert rows[0][RunRow.TAGS] == "experiment=thermal_limit"
    assert rows[0][RunRow.WALL] == "2.0 s"
    assert rows[0][RunRow.T_MAX] == 330.0
    assert rows[0][RunRow.MB] == 4.0
    assert rows[0][RunRow.FORCE_Y] is None
    assert rows[0][RunRow.LIVE] == ""
    assert rows[1][RunRow.LIVE] == "●"
    assert rows[1][RunRow.TAGS] == "experiment=force"


def test_run_rows_survive_a_row_without_tags(tmp_path: Path) -> None:
    frame = _run_frame(tmp_path).with_columns(pl.lit(None, pl.String).alias(RunColumn.TAGS))

    assert run_rows(frame)[0][RunRow.TAGS] == ""
    assert run_rows(frame.with_columns(pl.lit("[]").alias(RunColumn.TAGS)))[0][RunRow.TAGS] == ""
    assert run_rows(frame.with_columns(pl.lit("{oops").alias(RunColumn.TAGS)))[0][RunRow.TAGS] == ""


def test_component_rows_join_the_lumped_force_and_torque(tmp_path: Path) -> None:
    run = Run(_bundle(tmp_path))

    rows = component_rows(run)

    assert rows == [
        {
            ComponentColumn.COMPONENT: "MAGNETS",
            ComponentColumn.FX: 1.0,
            ComponentColumn.FY: -2.0,
            ComponentColumn.FZ: 0.5,
            ComponentColumn.FORCE: 2.2913,
            ComponentColumn.TORQUE: 0.25,
        }
    ]


def test_run_filters_narrow_by_status_text_tag_and_physics(tmp_path: Path) -> None:
    telemetry = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")

    def ids(filters: RunFilters) -> list[str]:
        frame = filters.apply(RunQuery(telemetry.runs.scan())).frame()

        return sorted(frame[RunColumn.RUN_ID].to_list())

    assert ids(RunFilters()) == [RUN_ID, "calm-otter-22222222", "dim-badger-33333333"]
    assert ids(RunFilters(statuses=("ok",))) == [RUN_ID, "dim-badger-33333333"]
    assert ids(RunFilters(text="stall")) == [RUN_ID]
    assert ids(RunFilters(tags=("experiment=thermal_limit",))) == [RUN_ID]
    assert ids(RunFilters(tags=("experiment=thermal_limit", "experiment=force"))) == [RUN_ID, "calm-otter-22222222", "dim-badger-33333333"]
    assert ids(RunFilters(physics=("magnetostatics_2d",))) == ["calm-otter-22222222", "dim-badger-33333333"]
    assert ids(RunFilters(statuses=("ok",), physics=("magnetostatics_2d",))) == ["dim-badger-33333333"]
    assert len(ids(RunFilters(limit=1))) == 1


# --- The dock ------------------------------------------------------------------


def _shell(name: str, root: Path) -> Workbench:
    return Workbench(name, title="Browsers", server=get_server(f"workbench-{name}"), persist=LocalJsonStore(root), user="tester")


def _settled(app: Workbench, act: Callable[[], None] = lambda: None) -> None:
    """Marks the state ready and runs `act` inside a loop, so the flush dispatches the panels' listeners, the asynchronous ones included."""

    async def run() -> None:
        app.state.ready()
        act()
        await asyncio.sleep(0)

    asyncio.run(run())


def test_the_browsers_spec_resolves_to_a_split_of_the_four_panels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))

    factory = resolve(BROWSERS.target)
    assert callable(factory)
    root = factory()

    assert isinstance(root, Split)
    assert [panel.panel_id for panel in panels(root)] == [str(member) for member in BrowserPanel]


def test_the_dock_builds_every_panel_over_a_telemetry_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    app = _shell("browsers-build", tmp_path)

    app.add(workbench())

    browser = app.panel(BrowserPanel.RUNS, RunBrowserPanel)
    rows = cast("list[Row]", app.state[browser.key(TableVar.ROWS)])
    options = cast("TableOptions", app.state[browser.key(TableVar.OPTIONS)])
    assert app.panel_ids == [str(member) for member in BrowserPanel]
    assert sorted(str(row[RunRow.RUN_ID]) for row in rows) == [RUN_ID, "calm-otter-22222222", "dim-badger-33333333"]
    assert str(app.state[browser.key(BrowserVar.COUNT)]) == "3 runs · 0 live"
    assert options.get("virtual") is True, "the index runs to thousands of rows, so the client virtualizes them"


def test_the_run_browser_toolbar_binds_its_filters_and_narrows_the_listing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    app = _shell("browsers-filters", tmp_path)
    app.add(workbench())
    _settled(app)
    browser = app.panel(BrowserPanel.RUNS, RunBrowserPanel)
    template = cast("dict[str, object]", app.state[f"trame__template_{browser.template_name}"])

    search = find(template, "ff-input")[0]
    assert prop(search, "defaultValue") == {"js": browser.key(BrowserVar.TEXT)}, "free text is uncontrolled and commits on blur and Enter"
    assert [prop(node, "value") for node in find(template, "ff-select")] == [{"js": browser.key(name)} for name in (BrowserVar.STATUS, BrowserVar.TAG, BrowserVar.PHYSICS, BrowserVar.LIMIT)]
    assert app.state[browser.key(BrowserVar.STATUS)] == ANY

    with app.state:
        app.state[browser.key(BrowserVar.STATUS)] = "error"

    assert browser.narrowing.statuses == ("error",)
    assert [str(row[RunRow.RUN_ID]) for row in cast("list[Row]", app.state[browser.key(TableVar.ROWS)])] == ["calm-otter-22222222"]
    assert str(app.state[browser.key(BrowserVar.COUNT)]) == "1 runs · 0 live"

    with app.state:
        app.state[browser.key(BrowserVar.STATUS)] = ANY
        app.state[browser.key(BrowserVar.TEXT)] = "stall"

    assert [str(row[RunRow.RUN_ID]) for row in cast("list[Row]", app.state[browser.key(TableVar.ROWS)])] == [RUN_ID]


def test_status_and_physics_read_in_title_case_while_the_filters_keep_the_raw_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    app = _shell("browsers-case", tmp_path)
    app.add(workbench())
    browser = app.panel(BrowserPanel.RUNS, RunBrowserPanel)
    template = cast("dict[str, object]", app.state[f"trame__template_{browser.template_name}"])
    rows = {str(row[RunRow.RUN_ID]): row for row in cast("list[Row]", app.state[browser.key(TableVar.ROWS)])}

    choices = {str(prop(node, "value")): texts(node)[0] for node in find(template, "ff-select-item")}

    assert (rows["calm-otter-22222222"][RunRow.STATUS], rows["calm-otter-22222222"][RunRow.PHYSICS]) == ("Error", "Magnetostatics 2D")
    assert choices["any"] == "Any" and choices["error"] == "Error" and choices["magnetostatics_2d"] == "Magnetostatics 2D"
    assert choices["experiment=force"] == "experiment=force", "a tag pair is data and reads as written"


def test_a_clicked_run_row_fills_the_detail_panel_and_gates_the_purge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")
    _ = _bundle(tmp_path)
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    app = _shell("browsers-detail", tmp_path)
    app.add(workbench())
    browser = app.panel(BrowserPanel.RUNS, RunBrowserPanel)
    detail = app.panel(BrowserPanel.RUN_DETAIL, RunDetailPanel)

    browser.on_pick({RunRow.RUN_ID: RUN_ID})
    detail.publish(detail.load(RUN_ID))

    assert app.state[Shared.SELECTED_RUN_ID] == RUN_ID
    assert app.state[detail.key(DetailVar.TITLE)] == RUN_ID
    assert app.state[detail.key(DetailVar.STATUS_VARIANT)] == StatusVariant.OK
    assert [row[PairField.LABEL] for row in cast("list[Row]", app.state[detail.key(DetailVar.META)])][:2] == ["Created", "Physics"]
    assert [row[ComponentColumn.COMPONENT] for row in cast("list[Row]", app.state[detail.key(DetailVar.COMPONENTS)])] == ["MAGNETS"]
    assert app.state[detail.key(PurgeVar.PLANNED)] is False, "Delete stays gated until a preview names what would go"

    detail.open_purge()
    detail.preview_purge()

    assert app.state[detail.key(PurgeVar.DIALOG)] is True
    assert app.state[detail.key(PurgeVar.PLANNED)] is True
    assert "would go" in str(app.state[detail.key(PurgeVar.TOTAL)])

    detail.load_preset(Preset.KEEP_ALL)

    assert app.state[detail.key(PurgeVar.PLANNED)] is False and detail.plan is None


def test_picking_the_selected_run_again_tells_every_listener_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    app = _shell("browsers-repick", tmp_path)
    app.add(workbench())
    browser = app.panel(BrowserPanel.RUNS, RunBrowserPanel)
    heard: list[object] = []

    @app.state.change(Shared.SELECTED_RUN_ID)
    def on_selected(**_: object) -> None:
        heard.append(app.state[Shared.SELECTED_RUN_ID])

    def pick_twice() -> None:
        heard.clear()
        browser.on_pick({RunRow.RUN_ID: RUN_ID})
        browser.on_pick({RunRow.RUN_ID: RUN_ID})

    _settled(app, pick_twice)

    assert heard == [RUN_ID, RUN_ID], "a run that finished since its first pick is read again"


def test_the_detail_panel_follows_a_running_run_until_it_ends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    bundle = tmp_path / RUN_ID

    async def run() -> tuple[str, str, bool]:
        app = _shell("browsers-watch", tmp_path)
        app.add(workbench())
        detail = app.panel(BrowserPanel.RUN_DETAIL, RunDetailPanel)
        detail.watch.period = 0.01

        bundle.mkdir()
        await detail.show(RUN_ID)
        starting = str(app.state[detail.key(DetailVar.STATUS)])

        _ = write_manifest(bundle, RunManifest.build(config, status="running", run_id=RUN_ID, created_at=FIXED_DT))
        while app.state[detail.key(DetailVar.STATUS)] != "Running":
            await asyncio.sleep(0.01)

        running = str(app.state[detail.key(DetailVar.STATUS)])
        _ = write_manifest(bundle, RunManifest.build(config, status="ok", run_id=RUN_ID, created_at=FIXED_DT))
        while detail.watch.running:
            await asyncio.sleep(0.01)

        assert app.state[detail.key(DetailVar.STATUS)] == "OK"

        return starting, running, detail.watch.running

    assert asyncio.run(asyncio.wait_for(run(), WATCH_TIMEOUT)) == (title(STARTING), "Running", False)


def test_a_rebuilt_detail_panel_shows_the_selected_run_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")
    _ = _bundle(tmp_path)
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))

    async def run() -> object:
        app = _shell("browsers-rebuilt", tmp_path)
        app.add(workbench())
        app.state[Shared.SELECTED_RUN_ID] = RUN_ID

        app.replace_root(workbench())
        detail = app.panel(BrowserPanel.RUN_DETAIL, RunDetailPanel)
        assert detail.showing is not None
        await detail.showing

        return app.state[detail.key(DetailVar.TITLE)]

    assert asyncio.run(run()) == RUN_ID


def test_a_missing_bundle_is_reported_rather_than_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    app = _shell("browsers-missing", tmp_path)
    app.add(workbench())
    detail = app.panel(BrowserPanel.RUN_DETAIL, RunDetailPanel)

    view = detail.load(RUN_ID)

    assert view.status == "gone" and detail.run is None
    assert detail.load("never-indexed-00000000").path == ""


def test_the_artifact_browser_groups_by_kind_and_a_row_click_selects_the_store_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_root = tmp_path / "store"
    _ = _telemetry(tmp_path / "telemetry", tmp_path, store_root)
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    app = _shell("browsers-artifacts", tmp_path)
    app.add(workbench())
    _settled(app)
    browser = app.panel(BrowserPanel.ARTIFACTS, ArtifactBrowserPanel)
    detail = app.panel(BrowserPanel.ARTIFACT_DETAIL, ArtifactDetailPanel)

    assert str(app.state[browser.key(ArtifactVar.COUNT)]) == "2 artifacts"
    assert cast("TableOptions", app.state[browser.key(TableVar.OPTIONS)]).get("defaults") == LayoutDefaults(groupBy=[GROUP_COLUMN]), "grouped by kind out of the box; the client's own panel regroups"
    assert GROUP_COLUMN in [column.get("field") for column in cast("list[ColumnSpec]", app.state[browser.key(TableVar.COLUMNS)])]

    with app.state:
        app.state[browser.key(ArtifactVar.KIND)] = "step"

    rows = cast("list[Row]", app.state[browser.key(TableVar.ROWS)])
    assert [row[ArtifactRow.NAME] for row in rows] == ["jumbo-badger-d525586a"]

    _settled(app, lambda: browser.on_pick(rows[0]))
    detail.publish(detail.load(Path(str(rows[0][ArtifactRow.PATH]))))

    assert app.state[Shared.SELECTED_ARTIFACT_PATH] == rows[0][ArtifactRow.PATH]
    assert str(app.state[detail.key(ArtifactDetailVar.TITLE)]).endswith("no artifact.json"), "the index row names a store directory this test never wrote"


def test_a_missing_telemetry_root_is_named_rather_than_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)

    with pytest.raises(ValueError, match=TELEMETRY_ENV):
        _ = workbench()


def test_the_detail_panels_render_their_sections_through_the_react_loops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    app = _shell("browsers-template", tmp_path)
    app.add(workbench())
    detail = app.panel(BrowserPanel.RUN_DETAIL, RunDetailPanel)
    template = cast("dict[str, object]", app.state[f"trame__template_{detail.template_name}"])

    loops = [prop(node, "items") for node in find(template, "ReactFor")]
    timeline = find(template, "ol")[0]
    badges = find(template, "ff-badge")
    assert {"js": detail.key(DetailVar.STAGES)} in loops and {"js": detail.key(DetailVar.REFS)} in loops
    assert str(Cls.TIMELINE) == prop(timeline, "className")
    assert prop(badges[0], "variant") == {"js": detail.key(DetailVar.STATUS_VARIANT)}
    assert find(template, "ff-dialog") and [node for node in find(template, "ff-button") if prop(node, "variant") == "destructive"]


def test_a_feed_polls_while_the_panel_is_shown_and_stops_when_it_is_hidden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _telemetry(tmp_path / "telemetry", tmp_path, tmp_path / "store")
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))

    async def run() -> tuple[int, bool]:
        app = _shell("browsers-feed", tmp_path)
        app.add(workbench())
        browser = app.panel(BrowserPanel.RUNS, RunBrowserPanel)
        feed: Feed[pl.DataFrame | None] = browser.feed
        feed.period = 0.01

        started = app.server.start(exec_mode="task", port=0, open_browser=False, show_connection_info=False, disable_logging=True, timeout=0)
        assert started is not None
        _ = await app.server.ready

        browser.on_visible(True)
        while feed.polls < 3:
            await asyncio.sleep(0.01)

        browser.on_visible(False)
        polls = feed.polls
        await asyncio.sleep(0.05)
        idle = feed.polls == polls and not feed.running

        await app.server.stop()
        await started

        return polls, idle

    polls, idle = asyncio.run(run())

    assert polls >= 3
    assert idle
