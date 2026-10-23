"""The field viewer: the built-in spec, the controller's drawing path by model dimension, the controls the shell builds and the state they bind."""

import asyncio
import json
import math
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import numpy as np
import polars as pl
import pytest

from femforge.artifacts.lock import LOCK_SUFFIX, OWNER_FILE, DirLock
from femforge.common.files import BundleFile
from femforge.common.log import TELEMETRY_ENV, log
from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.config import RunMeta, SimulationConfig
from femforge.elmer.config import PhysicsSelection
from femforge.elmer.physics import Physics
from femforge.meshing.config import MeshConfig
from femforge.physical.conditions import ConditionGlyph, SurfaceForce
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import quantity
from femforge.run.bundle import write_manifest
from femforge.elmer.sections import SifSection
from femforge.run.conditions import ConditionRecord, ConditionsFile, GroupConditions, SolveComponent
from femforge.run.entities import Bodies, Body, BodyRecord, BoundaryRecord, EntitiesFile
from femforge.run.inspect import Run
from femforge.run.manifest import RunManifest
from femforge.run.mechanical import VOIGT_COMPONENTS
from femforge.telemetry.paths import Telemetry
from femforge.telemetry.schemas import RUN_INDEX_SCHEMA, RunColumn
from femforge.viz.field_display import BodyPaint, BodyStyle, DisplayMode, DisplaySpec, SceneActor
from femforge.viz.field_lines import FieldLineOptions, SeedPlacement, StreamlineActor
from femforge.viz.glyphs import GlyphLayer, GlyphView, GroupScene
from femforge.viz.symmetry import Expansion
from femforge_workbench.dock import Split, panels, template_name
from femforge_workbench.feeds import Feed
from femforge_workbench.persist import LocalJsonStore
from femforge_workbench.registry import FIELD_VIEWER, resolve
from tests.workbench.tree import Node, find, prop, texts


pytest.importorskip("trame")
pytest.importorskip("pyvista")

import pyvista as pv  # noqa: E402
from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Hook, StateVar, Template, Workbench  # noqa: E402
from femforge_workbench.tasks import Task, TaskBoard, TaskPhase  # noqa: E402
from femforge_workbench.colormaps import COLORMAPS, FAVORITES  # noqa: E402
from femforge_workbench.panels import ItemField, Shared  # noqa: E402
from femforge_workbench.panels import field_viewer as field_viewer_module  # noqa: E402
from femforge_workbench.panels.setup import SetupPanel  # noqa: E402
from femforge_workbench.panels.scene import SIDES, StateVar as SceneVar, View, ViewMode  # noqa: E402
from femforge_workbench.panels.field_viewer import (  # noqa: E402
    FieldControlsPanel,
    FieldPanel,
    FieldVar,
    FieldViewerController,
    FieldViewerPanel,
    GLYPH_KEYS,
    OPEN_SECTIONS,
    RowKey,
    body_rows,
    SAME_AS_FIELD,
    colormap_gradient,
    colormap_items,
    names_of,
    number_of,
    pair_of,
    INDEX_UNREADABLE,
    NO_INDEX_NOTICE,
    NO_RUN_INDEX,
    NO_RUN_SELECTED,
    NO_RUNS_NOTICE,
    NO_RUNS_TASKS_NOTICE,
    NO_RUNS_YET,
    PICK_HOLD,
    SOLVING_AS,
    RunEntry,
    RunFlag,
    bundle_entry,
    claimed,
    merged,
    run_entries,
    seed_count,
    similar_bounds,
    workbench,
)
from femforge.run.result import RunStatus


MAX_LABEL = 23
"""The longest colormap label the pickers' label column fits."""


def _hex_grid() -> pv.UnstructuredGrid:
    cube1 = [(0.0, 0, 0), (1.0, 0, 0), (1.0, 1, 0), (0.0, 1, 0), (0.0, 0, 1), (1.0, 0, 1), (1.0, 1, 1), (0.0, 1, 1)]
    cube2 = [(1.0, 0, 0), (2.0, 0, 0), (2.0, 1, 0), (1.0, 1, 0), (1.0, 0, 1), (2.0, 0, 1), (2.0, 1, 1), (1.0, 1, 1)]
    points = np.array(cube1 + cube2, dtype=np.float64)
    cells = np.array([8, *range(0, 8), 8, *range(8, 16)])
    grid = pv.UnstructuredGrid(cells, np.array([pv.CellType.HEXAHEDRON, pv.CellType.HEXAHEDRON]), points)
    grid.cell_data["GeometryIds"] = np.array([1, 2], dtype=np.int32)
    grid.cell_data["joule heating"] = np.array([2.0, 3.0])
    grid.point_data["magnetic flux density"] = np.column_stack([points[:, 0], points[:, 1], np.zeros(len(points))])

    return grid


def _elastic_grid() -> pv.UnstructuredGrid:
    grid = _hex_grid()
    del grid.cell_data["joule heating"]
    del grid.point_data["magnetic flux density"]
    points = np.asarray(grid.points)
    count = len(points)
    grid.point_data["displacement"] = np.column_stack([1e-3 * points[:, 0], 1e-3 * points[:, 1], -1e-4 * points[:, 0] ** 2])

    for variable, values in (("stress", (1e6, 2e6, 3e6, 0.0, 0.0, 0.0)), ("strain", (1e-5, 2e-5, 3e-5, 0.0, 0.0, 0.0))):
        for component, value in zip(VOIGT_COMPONENTS, values, strict=True):
            grid.point_data[f"{variable}_{component}"] = np.full(count, value)

    grid.point_data["vonmises"] = np.full(count, 1.7e6)
    grid.point_data["tresca"] = np.full(count, 1e6)
    grid.point_data["principal stress"] = np.tile([2e6, 3e6, 1e6], (count, 1))
    grid.point_data["principal strain"] = np.tile([2e-5, 3e-5, 1e-5], (count, 1))

    return grid


def _quad_grid() -> pv.UnstructuredGrid:
    points = np.array([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0), (2.0, 0.0, 0.0), (2.0, 1.0, 0.0)], dtype=np.float64)
    cells = np.array([4, 0, 1, 2, 3, 4, 1, 4, 5, 2])
    grid = pv.UnstructuredGrid(cells, np.array([pv.CellType.QUAD, pv.CellType.QUAD]), points)
    grid.cell_data["GeometryIds"] = np.array([1, 2], dtype=np.int32)
    grid.point_data["temperature"] = points[:, 0] + 300.0

    return grid


def _bundle(root: Path, grid: pv.UnstructuredGrid) -> Path:
    root.mkdir()
    grid.save(str(root / "case_t0001.vtu"))  # pyright: ignore[reportUnknownMemberType]  # pyvista's save signature is partially untyped
    entities = EntitiesFile(bodies={1: BodyRecord(name="LEFT", material="copper"), 2: BodyRecord(name="RIGHT", material="steel")})
    _ = (root / BundleFile.ENTITIES).write_text(entities.model_dump_json(), encoding="utf-8")

    return root


def _manifested(root: Path, grid: pv.UnstructuredGrid, physics: Physics = Physics.MAGNETOSTATICS, force: Vec3 | None = None) -> Path:
    bundle = _bundle(root, grid)
    loads = [EntityTag(selector={TagKey.NAME: "TIP"}, conditions=[SurfaceForce(force=quantity(force, "N"))])] if force is not None else []
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"), domain=DomainConfig(tags=loads), physics=PhysicsSelection(selected=physics))
    _ = write_manifest(bundle, RunManifest.build(config, status=RunStatus.OK, run_id=root.name))

    return bundle


def _thermal_grid() -> pv.UnstructuredGrid:
    grid = _hex_grid()
    grid.point_data["temperature"] = np.asarray(grid.points)[:, 0] + 300.0

    return grid


def _shell(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workbench:
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)
    app = Workbench(name, title="Fields", server=get_server(f"field-viewer-{name}"), persist=LocalJsonStore(tmp_path / "persist"), user="tester")
    app.add(workbench())

    return app


def _controller(app: Workbench) -> FieldViewerController:
    return app.panel(FieldPanel.VIEW_3D, FieldViewerPanel).controller


def _template(app: Workbench, panel_id: FieldPanel) -> Node:
    return cast("Node", app.state[f"trame__template_{template_name(panel_id)}"])






def _bound(node: Node, name: str) -> object:
    """The state key or expression a prop is bound to, the raw value when it is not a binding."""
    value = prop(node, name)
    if isinstance(value, dict):
        return cast("dict[str, object]", value).get("js")

    return value


def _start(app: Workbench) -> object:
    return app.server.start(exec_mode="task", port=0, open_browser=False, show_connection_info=False, disable_logging=True, timeout=0)


# --- spec and shell -----------------------------------------------------------


def test_the_builtin_spec_resolves_to_a_split_with_the_three_panel_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)
    factory = resolve(FIELD_VIEWER.target)
    assert callable(factory)

    root = factory()

    assert isinstance(root, Split)
    assert [panel.panel_id for panel in panels(root)] == [str(panel_id) for panel_id in FieldPanel]


def test_a_shell_builds_the_templates_the_controls_defaults_and_the_open_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("build", tmp_path, monkeypatch)
    controller = _controller(app)

    assert all(app.state.has(f"trame__template_{template_name(panel_id)}") for panel_id in FieldPanel)
    assert app.state.has(f"trame__template_{Template.HEADER}")
    assert isinstance(app.panel(FieldPanel.CONTROLS, FieldControlsPanel), FieldControlsPanel)
    assert isinstance(app.panel(FieldPanel.SETUP, SetupPanel), SetupPanel)
    assert controller.viewer is app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
    assert app.ctrl[Hook.FIELD_OPEN].exists()

    assert app.state[FieldVar.RUN_ITEMS] == []
    assert app.state[FieldVar.COLORMAP] == "turbo" and app.state[FieldVar.MODE] == "value"
    assert app.state[FieldVar.AUTO_CLIM] is True and app.state[FieldVar.OUTLINE] is False
    assert app.state[FieldVar.LOG_SCALE] is False and app.state[FieldVar.STREAMLINES] is True
    assert app.state[FieldVar.LEGEND] == "hidden" and app.state[FieldVar.OPACITY_MAPPING] is True
    assert app.state[FieldVar.LINES_BODY] == "" and app.state[FieldVar.LINE_COUNT] == [FieldLineOptions().line_count]
    assert app.state[FieldVar.SEED_PLACEMENT] == str(SeedPlacement.CENTER_PLANE)
    assert app.state[Shared.SELECTED_RUN_ID] is None


def test_every_control_binds_its_global_state_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("controls", tmp_path, monkeypatch)
    template = _template(app, FieldPanel.CONTROLS)

    selects = [_bound(node, "value") for node in find(template, "ff-select")]
    comboboxes = [_bound(node, "value") for node in find(template, "ff-combobox")]
    switches = [_bound(node, "checked") for node in find(template, "ff-switch")]
    sliders = find(template, "ff-slider")
    rows = [node for node in find(template, "ReactFor") if _bound(node, "items") == str(FieldVar.BODY_ROWS)]

    assert selects == [str(var) for var in (FieldVar.FIELD, FieldVar.MODE, FieldVar.SEED_PLACEMENT, FieldVar.LINES_BODY)]
    assert comboboxes == [str(FieldVar.COLORMAP), str(FieldVar.BODY_COLORMAP_SHOWN)]
    assert switches == [str(var) for var in (FieldVar.WARP, FieldVar.INVERT, FieldVar.LOG_SCALE, FieldVar.OUTLINE, FieldVar.AUTO_CLIM, FieldVar.OPACITY_MAPPING, FieldVar.STREAMLINES)]
    assert [_bound(node, "value") for node in sliders] == [
        str(FieldVar.SWEEP),
        str(FieldVar.WARP_SCALE),
        str(FieldVar.CLIM),
        str(FieldVar.OPACITY_RANGE),
        str(FieldVar.LINE_COUNT),
        str(FieldVar.MAX_LENGTH),
        str(FieldVar.OPACITY),
        f"item.{RowKey.OPACITY}",
    ]
    assert all(prop(node, "onValueCommit") is not None for node in sliders)
    assert _bound(sliders[1], "max") == str(FieldVar.WARP_SCALE_MAX) and _bound(sliders[1], "step") == f"{FieldVar.WARP_SCALE_MAX} / 100"
    assert _bound(sliders[2], "min") == str(FieldVar.CLIM_MIN) and prop(sliders[2], "disabled") is None, "a moved thumb pins the range instead"
    assert str(FieldVar.FIELD_GRADIENT) in str(prop(sliders[2], "style")), "the range track wears the colormap"
    assert [_bound(node, "value") for node in find(template, "ff-toggle-group")] == [str(FieldVar.EXPANSION), str(FieldVar.EXPANSION), str(FieldVar.LEGEND)]
    assert [texts(node) for node in find(template, "ff-toggle-group-item")] == [["Section"], ["Mirrored"], ["Revolved"], ["Section"], ["Extruded"], ["Hidden"], ["Vertical"], ["Horizontal"]]
    assert [prop(node, "aria-label") for node in find(template, "ff-toggle-group")] == ["Show as", "Show as", "Legend"], "a revolving and a Cartesian section each offer their own forms"
    assert [str(prop(node, "className")).endswith("grid-cols-2") for node in find(template, "ff-toggle-group")] == [False, True, False], "a two-way choice fills its track"
    assert len(rows) == 1 and find(template, "ff-tree") == []
    assert [texts(node)[0] for node in find(template, "ff-accordion-trigger")] == ["Symmetry", "Deformation", "Field", "Field Lines", "Bodies"]
    assert [prop(node, "id") for node in find(template, "ff-accordion-item")] == ["tour-symmetry", "tour-deformation", "tour-field", "tour-lines", "tour-bodies"]
    assert [prop(node, "htmlFor") for node in find(template, "ff-field-label") if prop(node, "htmlFor")] == [prop(node, "id") for node in find(template, "ff-switch")]
    assert [_bound(node, "active") for node in find(template, "ff-busy")] == [str(FieldVar.BUSY)]


def test_the_run_picker_and_the_bundle_path_field_sit_in_the_app_bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("header", tmp_path, monkeypatch)
    header = cast("Node", app.state[f"trame__template_{Template.HEADER}"])

    picker = find(header, "ff-select")[0]
    path = find(header, "ff-input")[0]

    assert _bound(picker, "value") == str(FieldVar.RUN)
    assert _bound(find(header, "ReactFor")[0], "items") == str(FieldVar.RUN_ITEMS)
    assert _bound(path, "value") == str(FieldVar.RUN_TEXT) and prop(path, "readOnly") is True, "the path follows the open run and is not typed into"
    assert find(header, "ff-tree") == []

    assert _bound(picker, "disabled") == f"!{FieldVar.RUN_ITEMS}.length", "an empty picker is disabled rather than opening an empty list"
    assert prop(picker, "onOpenChange") == {"callback": {"js": f'trame.state.set("{FieldVar.RUN_PICKING}", $event)'}}
    assert prop(find(header, "ff-select-trigger")[0], "aria-label") == "Run"
    assert _bound(find(header, "ff-select-value")[0], "placeholder") == str(FieldVar.RUN_PLACEHOLDER)
    assert _bound(find(header, "ff-select-item")[0], "detail") == f"item.{ItemField.DETAIL}"
    assert _bound(find(header, "ff-select-item")[0], "flag") == f"item.{ItemField.FLAG}"
    assert app.state[FieldVar.RUN_PLACEHOLDER] == NO_RUN_INDEX

    opened: list[Path] = []
    monkeypatch.setattr(field_viewer_module, "open_folder", opened.append)
    controls = app.panel(FieldPanel.CONTROLS, FieldControlsPanel)
    folder = next(node for node in find(header, "ff-button") if prop(node, "aria-label") == "Open Folder")
    assert _bound(folder, "disabled") == f"!{FieldVar.RUN_TEXT}", "Open Folder waits for an open run"
    controls.reveal_run()
    app.state[FieldVar.RUN_TEXT] = str(tmp_path)
    controls.reveal_run()
    assert opened == [tmp_path], "only an open run's folder opens"


# --- the controller's paths -----------------------------------------------------


def test_a_3d_run_draws_in_the_plotter_and_integrates_field_lines_off_the_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("three-d", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        bundle = _bundle(tmp_path / "hex", _hex_grid())

        await controller.load(bundle)
        await controller.settle()

        assert controller.model_dim == 3
        assert str(SceneActor.BODIES) in viewer.plotter.renderer.actors
        assert viewer.renderable is controller.scene
        assert app.state[FieldVar.RUN_ID] == "hex" and app.state[FieldVar.RUN_TEXT] == str(bundle)
        assert app.state[FieldVar.FIELDS] == [{"title": "Joule Heating", "value": "joule heating"}, {"title": "Magnetic Flux Density", "value": "magnetic flux density"}]
        assert app.state[FieldVar.FIELD] == "joule heating" and app.state[FieldVar.MODE] == "value"
        assert app.state[FieldVar.VISIBLE_BODIES] == ["LEFT", "RIGHT"] and app.state[FieldVar.HAS_VECTORS] is True
        assert app.state[FieldVar.CLIM_MIN] == 2.0 and app.state[FieldVar.CLIM_MAX] == 3.0
        assert app.state[FieldVar.LINES_BODIES] == [{"title": "Whole Domain", "value": ""}, {"title": "LEFT", "value": "LEFT"}, {"title": "RIGHT", "value": "RIGHT"}]
        assert [row[RowKey.ID] for row in cast("list[dict[str, object]]", app.state[FieldVar.BODY_ROWS])] == ["LEFT", "RIGHT"]

        app.state[FieldVar.FIELD] = "magnetic flux density"
        app.state[FieldVar.MODE] = str(DisplayMode.POLAR_XY)
        app.state[FieldVar.VISIBLE_BODIES] = ["LEFT"]
        app.state[FieldVar.STREAMLINES] = True
        app.state[FieldVar.LINE_COUNT] = [2]
        controller.request_redraw()
        await controller.settle()

        assert controller.scene is not None
        assert controller.scene.spec.mode is DisplayMode.POLAR_XY and controller.scene.spec.hidden_bodies == ("RIGHT",)
        assert controller.scene.spec.streamlines is not None and controller.scene.spec.streamlines.line_count == 2
        assert str(StreamlineActor.LINES) in viewer.plotter.renderer.actors

        app.state[FieldVar.STREAMLINES] = False
        controller.request_redraw()
        await controller.settle()

        assert str(StreamlineActor.LINES) not in viewer.plotter.renderer.actors

    asyncio.run(run())


def test_the_field_lines_follow_the_shown_vector_field_and_fall_back_to_the_flux_density(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> list[str | None]:
        app = _shell("follow", tmp_path, monkeypatch)
        controller = _controller(app)
        grid = _hex_grid()
        grid.point_data["magnetic field strength"] = np.column_stack([np.ones(grid.n_points), np.zeros(grid.n_points), np.zeros(grid.n_points)])

        await controller.load(_bundle(tmp_path / "hex", grid))
        await controller.settle()
        followed: list[str | None] = []
        for field in ("magnetic field strength", "joule heating", "magnetic flux density"):
            app.state[FieldVar.FIELD] = field
            followed.append(controller.streamline_options(3).vectors)

        return followed

    assert asyncio.run(run()) == ["magnetic field strength", "magnetic flux density", "magnetic flux density"]


def test_an_elasticity_run_opens_on_the_displacement_and_offers_von_mises_and_strain_energy_density(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("elastic", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)

        await controller.load(_bundle(tmp_path / "beam", _elastic_grid()))
        await controller.settle()

        titles = [item["title"] for item in cast("list[dict[str, object]]", app.state[FieldVar.FIELDS])]

        assert titles == ["Displacement", "Principal Strain", "Principal Stress", "Strain Energy Density", "Tresca", "Von Mises"]
        assert app.state[FieldVar.FIELD] == "displacement" and app.state[FieldVar.MODE] == "magnitude"
        assert app.state[FieldVar.HAS_VECTORS] is True

        app.state[FieldVar.FIELD] = "principal stress"
        controller.on_field()
        modes = [item["value"] for item in cast("list[dict[str, object]]", app.state[FieldVar.MODES])]

        assert modes == ["max_principal", "mid_principal", "min_principal"] and app.state[FieldVar.MODE] == "max_principal"

        app.state[FieldVar.FIELD] = "vonmises"
        controller.on_field()
        app.state[FieldVar.STREAMLINES] = True
        app.state[FieldVar.LINE_COUNT] = [2]
        controller.request_redraw()
        await controller.settle()

        assert controller.streamline_options(3).vectors == "displacement"
        assert controller.scene is not None and controller.scene.spec.field == "vonmises"
        assert str(StreamlineActor.LINES) in viewer.plotter.renderer.actors

    asyncio.run(run())


def test_an_elasticity_run_opens_deformed_and_rescales_on_the_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("deformed", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)

        await controller.load(_bundle(tmp_path / "beam", _elastic_grid()))
        await controller.settle()
        scene = controller.scene
        assert scene is not None
        auto = scene.auto_warp_scale("displacement")
        deformed = viewer.plotter.bounds
        camera = list(viewer.plotter.camera.position)

        assert app.state[FieldVar.HAS_DISPLACEMENT] is True and app.state[FieldVar.WARP] is True
        assert app.state[FieldVar.WARP_SCALE] == [auto] and app.state[FieldVar.WARP_SCALE_MAX] == pytest.approx(5.0 * auto)
        assert scene.warped == ("displacement", auto) and auto > 0.0
        assert deformed[4] < 0.0 and deformed[1] > 2.0, "the bodies open bent by the solved displacement"

        app.state[FieldVar.WARP_SCALE] = [0.0]
        controller.on_commit()
        await controller.settle()

        assert scene.warped is None and scene.spec.warp_scale == 0.0
        assert viewer.plotter.bounds[0:2] == pytest.approx((0.0, 2.0)) and viewer.plotter.bounds[4] == pytest.approx(0.0), "a zero scale draws the bodies as solved"
        assert list(viewer.plotter.camera.position) == pytest.approx(camera), "a new scale keeps the view"

        app.state[FieldVar.WARP_SCALE] = [2.0 * auto]
        controller.on_commit()
        await controller.settle()

        assert scene.warped == ("displacement", 2.0 * auto)

        app.state[FieldVar.WARP] = False
        controller.on_display()
        await controller.settle()

        assert scene.warped is None and scene.spec.warp is None

    asyncio.run(run())


def test_a_run_without_displacement_has_nothing_to_deform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("undeformed", tmp_path, monkeypatch)
        controller = _controller(app)
        template = _template(app, FieldPanel.CONTROLS)

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()

        assert app.state[FieldVar.HAS_DISPLACEMENT] is False and app.state[FieldVar.WARP] is True, "the switch keeps its place for the next run"
        assert app.state[FieldVar.WARP_SCALE_MAX] == 1.0
        assert controller.scene is not None and controller.scene.warped is None and controller.scene.spec.warp is None
        assert str(FieldVar.HAS_DISPLACEMENT) in [_bound(node, "open") for node in find(template, "ff-reveal")], "the Deformation section collapses away"

        controller.clear("gone")

        assert app.state[FieldVar.HAS_DISPLACEMENT] is False

    asyncio.run(run())


def test_a_run_without_a_vector_field_draws_with_the_field_line_switch_left_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("no-flux", tmp_path, monkeypatch)
        controller = _controller(app)
        grid = _hex_grid()
        del grid.point_data["magnetic flux density"]
        app.state[FieldVar.STREAMLINES] = True

        await controller.load(_bundle(tmp_path / "plain", grid))
        await controller.settle()

        assert app.state[FieldVar.HAS_VECTORS] is False and app.state[FieldVar.STREAMLINES] is True
        assert controller.scene is not None and controller.scene.spec.streamlines is None
        assert not str(app.state[StateVar.STATUS]).startswith("opening"), "the busy line, not the status, says a run is opening"

    asyncio.run(run())




def test_state_changes_redraw_and_a_field_change_snaps_the_mode_on_a_running_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("running", tmp_path, monkeypatch)
        controller = _controller(app)
        bundle = _bundle(tmp_path / "hex", _hex_grid())
        started = _start(app)
        _ = await app.server.ready

        await controller.load(bundle)
        await controller.settle()
        with app.state:
            app.state[FieldVar.FIELD] = "magnetic flux density"

        await controller.settle()
        modes = [item["value"] for item in cast("list[dict[str, object]]", app.state[FieldVar.MODES])]
        snapped_mode = app.state[FieldVar.MODE]
        assert controller.scene is not None
        drawn_field = controller.scene.spec.field

        with app.state:
            app.state[FieldVar.AUTO_CLIM] = False

        await controller.settle()
        with app.state:
            app.state[FieldVar.CLIM] = [0.5, 0.75]

        await controller.settle()
        dragging = controller.scene.clim
        controller.on_commit()
        await controller.settle()
        committed = controller.scene.clim

        await app.server.stop()
        await cast("asyncio.Future[object]", started)

        assert modes == ["magnitude", "x", "y", "z", "polar_xy"] and snapped_mode == "magnitude"
        assert drawn_field == "magnetic flux density"
        assert dragging != (0.5, 0.75), "the thumbs move without redrawing"
        assert committed == (0.5, 0.75)

    asyncio.run(run())


def test_the_viewer_follows_the_shared_selection_only_while_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("selection", tmp_path, monkeypatch)
        controller = _controller(app)
        flat = _bundle(tmp_path / "quad", _quad_grid())
        solid = _bundle(tmp_path / "hex", _hex_grid())

        app.state[Shared.SELECTED_RUN_ID] = str(flat)
        controller.on_selected_run()
        assert controller.scene is None

        controller.on_visible(str(FieldPanel.VIEW_3D), True)
        await controller.settle()
        assert controller.scene is not None and controller.scene.run_id == "quad"

        app.state[Shared.SELECTED_RUN_ID] = str(solid)
        controller.on_selected_run()
        await controller.settle()
        assert controller.scene.run_id == "hex"

        controller.on_visible(str(FieldPanel.VIEW_3D), False)
        app.state[Shared.SELECTED_RUN_ID] = str(flat)
        controller.on_selected_run()
        await controller.settle()
        assert controller.scene.run_id == "hex"

    asyncio.run(run())


def test_picking_the_shown_run_again_opens_it_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("repick", tmp_path, monkeypatch)
        controller = _controller(app)
        bundle = _bundle(tmp_path / "hex", _hex_grid())
        app.state[Shared.SELECTED_RUN_ID] = str(bundle)
        controller.on_visible(str(FieldPanel.VIEW_3D), True)
        await controller.settle()
        first = controller.scene

        controller.on_selected_run()
        await controller.settle()

        assert first is not None and controller.scene is not None
        assert controller.scene is not first and controller.scene.run_id == "hex", "a run picked again is read again, so one that finished since shows its fields"

    asyncio.run(run())


def test_a_run_still_solving_empties_the_views_and_opens_once_it_ends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    solved = False

    def solving(_: object) -> bool:
        return not solved

    monkeypatch.setattr("femforge_workbench.panels.field_viewer.solving", solving)
    monkeypatch.setattr("femforge_workbench.panels.field_viewer.FIELDS_PERIOD", 0.01)

    async def run() -> None:
        nonlocal solved
        app = _shell("solving", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        shown = _bundle(tmp_path / "hex", _hex_grid())
        pending = _bundle(tmp_path / "pending", _hex_grid())
        held = (pending / "case_t0001.vtu").rename(tmp_path / "held.vtu")

        await controller.load(shown)
        await controller.settle()
        await controller.load(pending)

        notice = "pending is still solving. Its fields appear here when it finishes; the solver's terminal shows its progress."
        assert controller.scene is None and app.state[FieldVar.FIELDS] == [] and app.state[FieldVar.BODY_ROWS] == []
        assert str(SceneActor.BODIES) not in viewer.plotter.renderer.actors, "nothing of the last run is left to read as this one"
        assert app.state[viewer.key(SceneVar.NOTICE)] == notice and app.state[StateVar.STATUS] == "pending is solving", "the status line stays short"
        assert app.state[FieldVar.SOLVED_AS] == SOLVING_AS, "the controls agree with the picker"
        assert app.state[FieldVar.BUSY] is True, "the busy bar pulses until the fields open"
        assert app.state[FieldVar.RUN] == str(pending) and app.state[FieldVar.RUN_TEXT] == str(pending)
        assert cast("list[dict[str, object]]", app.state[FieldVar.RUN_ITEMS])[0] == {"title": "pending", "value": str(pending), "detail": [], "flag": "Solving"}

        _ = held.rename(pending / "case_t0001.vtu")
        solved = True
        waiting = controller.wait_task
        assert waiting is not None
        await waiting
        await controller.settle()

        assert controller.scene is not None and controller.scene.run_id == "pending"
        assert app.state[viewer.key(SceneVar.NOTICE)] == "" and app.state[FieldVar.BUSY] is False
        assert cast("list[dict[str, object]]", app.state[FieldVar.RUN_ITEMS])[0] == {"title": "pending", "value": str(pending), "detail": []}

    asyncio.run(run())


def test_with_tasks_a_run_still_solving_points_at_the_task_strip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def solving(_: object) -> bool:
        return True

    monkeypatch.setattr("femforge_workbench.panels.field_viewer.solving", solving)

    async def run() -> str:
        app = _shell("solving-tasks", tmp_path, monkeypatch)
        app.board = TaskBoard(app, "main:tasks", [Task("Solve", time.time)], path=None, roots=[])
        controller = _controller(app)
        pending = _bundle(tmp_path / "pending", _hex_grid())
        (pending / "case_t0001.vtu").unlink()

        await controller.load(pending)
        waiting = controller.wait_task
        assert waiting is not None
        _ = waiting.cancel()

        return cast("str", app.state[app.panel(FieldPanel.VIEW_3D, FieldViewerPanel).key(SceneVar.NOTICE)])

    assert asyncio.run(run()) == "pending is still solving. Its fields appear here when it finishes; the task strip below shows its progress."


def test_a_solid_body_the_next_run_lacks_leaves_the_plotter_with_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> tuple[list[str], list[str]]:
        monkeypatch.delenv(TELEMETRY_ENV, raising=False)
        solid = DisplaySpec(body_styles=(BodyStyle(body="RIGHT", paint=BodyPaint.SOLID, color="#ff0000"),))
        app = Workbench("solid", title="Fields", server=get_server("field-viewer-solid"), persist=LocalJsonStore(tmp_path / "persist"), user="tester")
        app.add(workbench(display=solid))
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        lone = _hex_grid()
        lone.cell_data["GeometryIds"] = np.array([1, 1], dtype=np.int32)

        await controller.load(_bundle(tmp_path / "both", _hex_grid()))
        await controller.settle()
        before = [str(name) for name in viewer.plotter.renderer.actors]

        await controller.load(_bundle(tmp_path / "lone", lone))
        await controller.settle()
        after = [str(name) for name in viewer.plotter.renderer.actors]

        return before, after

    before, after = asyncio.run(run())

    styled = SceneActor.STYLED.of("RIGHT")
    assert styled in before
    assert styled not in after, "a body the shown run lacks must not stay drawn from the run before"


def test_a_finished_run_without_fields_says_so_and_waits_for_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("fieldless", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        assert app.state[viewer.key(SceneVar.NOTICE)] == NO_INDEX_NOTICE

        await controller.load(tmp_path / "missing")

        assert controller.wait_task is None and app.state[FieldVar.BUSY] is False
        assert str(app.state[viewer.key(SceneVar.NOTICE)]).startswith("FileNotFoundError")

        fieldless = tmp_path / "fieldless"
        manifest = RunManifest.build(SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step")), status=RunStatus.OK, run_id="fieldless")
        _ = write_manifest(fieldless, manifest)
        await controller.load(fieldless)

        assert controller.wait_task is None and app.state[FieldVar.BUSY] is False
        assert app.state[viewer.key(SceneVar.NOTICE)] == "fieldless: The solve wrote no fields"
        item = cast("list[dict[str, object]]", app.state[FieldVar.RUN_ITEMS])[0]
        assert item[ItemField.DETAIL] == ["Magnetostatics", "3D"] and item[ItemField.FLAG] == "No Fields"

    asyncio.run(run())


def test_a_2d_run_seeds_its_center_plane_and_offers_no_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> tuple[list[object], list[object]]:
        app = _shell("seeds", tmp_path, monkeypatch)
        controller = _controller(app)
        app.state[FieldVar.SEED_PLACEMENT] = str(SeedPlacement.VOLUME)
        app.state[FieldVar.EXPANSION] = str(Expansion.SECTION)

        await controller.load(_bundle(tmp_path / "quad", _quad_grid()))
        await controller.settle()
        flat = [app.state[FieldVar.SEED_PLACEMENT], app.state[FieldVar.SEED_PLACEMENTS]]

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        solid = [app.state[FieldVar.SEED_PLACEMENT], app.state[FieldVar.SEED_PLACEMENTS]]

        return flat, solid

    flat, solid = asyncio.run(run())

    flat_items = cast("list[dict[str, object]]", flat[1])
    assert flat[0] == SeedPlacement.CENTER_PLANE
    assert [item["title"] for item in flat_items] == ["Center Plane", "Volume (3D Only)", "Gap Line"]
    assert {"title": "Volume (3D Only)", "value": "volume", "disabled": True} in flat_items, "a 2D grid has no depth: volume seeds would repeat the plane's"
    assert solid[0] == SeedPlacement.VOLUME, "the next 3D run takes the volume seeds back"
    assert all("disabled" not in item for item in cast("list[dict[str, object]]", solid[1]))


def test_each_seed_placement_keeps_its_own_count_within_its_own_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("seed-counts", tmp_path, monkeypatch)
    controller = _controller(app)

    def place(placement: SeedPlacement) -> tuple[object, object]:
        app.state[FieldVar.SEED_PLACEMENT] = str(placement)
        controller.on_placement()

        return app.state[FieldVar.LINE_COUNT], app.state[FieldVar.LINE_COUNT_MAX]

    assert (app.state[FieldVar.LINE_COUNT], app.state[FieldVar.LINE_COUNT_MAX]) == ([18], 60)
    assert place(SeedPlacement.VOLUME) == ([16], 16), "the volume's range caps the plane's count"

    app.state[FieldVar.LINE_COUNT] = [12]

    assert place(SeedPlacement.CENTER_PLANE) == ([18], 60), "a count lowered for the volume never lands on the plane"
    assert place(SeedPlacement.VOLUME) == ([12], 16)
    assert place(SeedPlacement.GAP_LINE) == ([18], 200)

    controller.on_placement()

    assert app.state[FieldVar.LINE_COUNT] == [18], "picking the shown placement again changes nothing"


def test_every_section_starts_open_and_the_seed_readout_counts_the_seeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("sections", tmp_path, monkeypatch)
    template = _template(app, FieldPanel.CONTROLS)

    accordion = find(template, "ff-accordion")[0]
    readouts = [node for node in find(template, "span") if any(_bound_child(child) == seed_count() for child in cast("list[object]", node.get("children", [])))]

    assert prop(accordion, "defaultValue") == OPEN_SECTIONS == ["symmetry", "deformation", "field", "lines", "bodies"]
    assert len(readouts) == 1
    assert seed_count().startswith("Math.pow(") and seed_count().endswith(".toLocaleString()"), "the readout is the seed total alone"
    assert str(SeedPlacement.CENTER_PLANE) in seed_count()


def _bound_child(child: object) -> object:
    return cast("dict[str, object]", child).get("js") if isinstance(child, dict) else None


def test_the_field_open_hook_takes_a_bundle_path_the_way_the_run_browser_calls_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> str:
        app = _shell("hook", tmp_path, monkeypatch)
        controller = _controller(app)
        bundle = _bundle(tmp_path / "quad", _quad_grid())

        _ = app.ctrl[Hook.FIELD_OPEN](bundle)
        await controller.settle()

        assert controller.scene is not None
        assert app.state[FieldVar.RUN] == str(bundle)

        return controller.scene.run_id

    assert asyncio.run(run()) == "quad"


def test_a_run_given_to_the_dock_opens_once_the_server_runs_with_the_display_asked_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.delenv(TELEMETRY_ENV, raising=False)
        bundle = _bundle(tmp_path / "hex", _hex_grid())
        app = Workbench("given", title="Fields", server=get_server("field-viewer-given"), persist=LocalJsonStore(tmp_path / "persist"), user="tester")
        display = DisplaySpec(
            field="magnetic flux density",
            colormap="lipari",
            opacity_map=(0.2, 0.9),
            hidden_bodies=("RIGHT",),
            body_styles=(BodyStyle(body="LEFT", paint=BodyPaint.SOLID, color="#336699", opacity=0.5),),
            streamlines=FieldLineOptions(line_count=3, colormap="magma", opacity=0.6),
        )
        app.add(workbench(bundle, display=display))
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        idle = controller.load_task is None

        started = _start(app)
        _ = await app.server.ready
        await controller.settle()
        scene = controller.scene
        lines = str(StreamlineActor.LINES) in viewer.plotter.renderer.actors
        await app.server.stop()
        await cast("asyncio.Future[object]", started)

        assert idle, "nothing opens before the server runs"
        assert scene is not None and scene.run_id == "hex"
        assert scene.spec.field == "magnetic flux density" and scene.spec.streamlines is not None
        assert scene.spec.hidden_bodies == ("RIGHT",) and app.state[FieldVar.VISIBLE_BODIES] == ["LEFT"]
        assert scene.spec.mode is DisplayMode.MAGNITUDE and scene.spec.colormap == "lipari" and scene.spec.opacity_map == (0.2, 0.9)
        assert scene.spec.body_styles == display.body_styles and app.state[FieldVar.LINE_COUNT] == [3]
        styled = scene.drawn_lines
        assert styled is not None
        assert styled.colormap == "lipari" and styled.opacity_map == (0.2, 0.9) and styled.opacity == 0.6, "the lines take the field mapping"
        left = cast("list[dict[str, object]]", app.state[FieldVar.BODY_ROWS])[0]
        assert (left[RowKey.ID], left[RowKey.COLOR], left[RowKey.OPACITY]) == ("LEFT", "#336699", [0.5])
        assert lines

    asyncio.run(run())




def test_each_new_run_opens_on_its_own_defaults_until_the_user_picks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> list[tuple[object, ...]]:
        app = _shell("defaults", tmp_path, monkeypatch)
        controller = _controller(app)
        beam = _manifested(tmp_path / "beam", _elastic_grid(), Physics.LINEAR_ELASTICITY, Vec3(0, 500, 0))
        magnet = _manifested(tmp_path / "magnet", _hex_grid())
        opened: list[tuple[object, ...]] = []
        for bundle in (beam, magnet, beam):
            await controller.load(bundle)
            await controller.settle()
            opened.append(tuple(app.state[key] for key in (FieldVar.FIELD, FieldVar.MODE, FieldVar.STREAMLINES, FieldVar.OPACITY_MAPPING, FieldVar.OPACITY_RANGE)))

        return opened

    assert asyncio.run(run()) == [
        ("displacement", "y", False, False, [0.15, 1.0]),
        ("magnetic flux density", "magnitude", True, True, [0.15, 1.0]),
        ("displacement", "y", False, False, [0.15, 1.0]),
    ]


def test_a_picked_field_carries_to_the_next_run_that_has_it_and_a_run_without_it_opens_on_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> list[tuple[object, ...]]:
        app = _shell("picked", tmp_path, monkeypatch)
        controller = _controller(app)
        pulled = _manifested(tmp_path / "pulled", _elastic_grid(), Physics.LINEAR_ELASTICITY, Vec3(0, 500, 0))
        pushed = _manifested(tmp_path / "pushed", _elastic_grid(), Physics.LINEAR_ELASTICITY, Vec3(-500, 0, 0))
        heated = _manifested(tmp_path / "heated", _thermal_grid(), Physics.THERMAL)
        started = _start(app)
        _ = await app.server.ready

        def shown() -> tuple[object, ...]:
            return tuple(app.state[key] for key in (FieldVar.FIELD, FieldVar.MODE, FieldVar.STREAMLINES, FieldVar.OPACITY_MAPPING))

        await controller.load(pulled)
        await controller.settle()
        opened = [shown()]
        with app.state:
            app.state[FieldVar.FIELD] = "vonmises"
            app.state[FieldVar.STREAMLINES] = True

        await controller.settle()
        for bundle in (pushed, heated, pushed):
            await controller.load(bundle)
            await controller.settle()
            opened.append(shown())

        await app.server.stop()
        await cast("asyncio.Future[object]", started)

        return opened

    assert asyncio.run(run()) == [
        ("displacement", "y", False, False),
        ("vonmises", "value", True, False),
        ("temperature", "value", False, False),
        ("displacement", "x", False, False),
    ]


def test_a_display_that_names_a_field_opens_each_run_on_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> list[tuple[object, ...]]:
        monkeypatch.delenv(TELEMETRY_ENV, raising=False)
        app = Workbench("named", title="Fields", server=get_server("field-viewer-named"), persist=LocalJsonStore(tmp_path / "persist"), user="tester")
        app.add(workbench(display=DisplaySpec(field="joule heating", streamlines=None)))
        controller = _controller(app)
        opened: list[tuple[object, ...]] = []
        for bundle in (_manifested(tmp_path / "first", _hex_grid()), _manifested(tmp_path / "second", _hex_grid())):
            await controller.load(bundle)
            await controller.settle()
            opened.append((app.state[FieldVar.FIELD], app.state[FieldVar.STREAMLINES], app.state[FieldVar.OPACITY_MAPPING]))

        return opened

    assert asyncio.run(run()) == [("joule heating", False, True), ("joule heating", False, True)]


def test_the_ui_store_never_holds_the_field_display_or_the_glyph_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("stored", tmp_path, monkeypatch)
        controller = _controller(app)
        started = _start(app)
        _ = await app.server.ready

        await controller.load(_manifested(tmp_path / "magnet", _hex_grid()))
        await controller.settle()
        with app.state:
            app.state[FieldVar.FIELD] = "joule heating"
            app.state[FieldVar.MODE] = str(DisplayMode.ABSVALUE)
            app.state[FieldVar.OPACITY_MAPPING] = False
            app.state[FieldVar.SETUP_SHOWN] = True
            app.state[FieldVar.GLYPH_KINDS_OFF] = ["thermal"]
            app.state[FieldVar.SELECTED_ENTITY] = "body:MAGNET"
            app.state[FieldVar.AUTHORED_GHOST] = True

        await controller.settle()
        await app.server.stop()
        await cast("asyncio.Future[object]", started)
        app.persist.flush()

    asyncio.run(run())

    def keys(data: object) -> set[str]:
        if isinstance(data, dict):
            pairs = cast("dict[str, object]", data)
            nested = [keys(value) for value in pairs.values()]

            return set(pairs).union(*nested)

        if isinstance(data, list):
            return set[str]().union(*(keys(item) for item in cast("list[object]", data)))

        return set()

    stored = [keys(cast("object", json.loads(file.read_text(encoding="utf-8")))) for file in (tmp_path / "persist").rglob("*.json")]
    found = set[str]().union(*stored)

    assert stored, "the shell saved its state"
    assert not found & {str(key) for key in FieldVar}


def test_the_last_panel_to_leave_the_dock_releases_the_controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def listening() -> bool:
        return any(fn == controller.on_field for fn, _ in app.state._change_callbacks.get(str(FieldVar.FIELD), []))

    app = _shell("dispose", tmp_path, monkeypatch)
    controller = _controller(app)
    controls = app.panel(FieldPanel.CONTROLS, FieldControlsPanel)

    assert app.ctrl[Hook.FIELD_OPEN].exists() and listening()
    assert controller.attached == {str(panel_id) for panel_id in FieldPanel}

    setup = app.panel(FieldPanel.SETUP, SetupPanel)
    controls.dispose()

    assert controller.live and listening(), "the viewer is still on screen"
    assert app.ctrl[Hook.FIELD_OPEN].exists()

    app.panel(FieldPanel.VIEW_3D, FieldViewerPanel).dispose()

    assert controller.live and listening(), "Setup alone keeps the controller"
    assert controller.viewer is None and controller.setup_panel is setup

    setup.dispose()

    assert not app.ctrl[Hook.FIELD_OPEN].exists() and not listening()
    assert controller.setup_panel is None and controls.bound == [] and setup.bound == [] and not controller.live




def test_a_rebuilt_panel_takes_the_controller_back_and_redraws_the_open_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("rebuilt", tmp_path, monkeypatch)
        controller = _controller(app)
        bundle = _bundle(tmp_path / "hex", _hex_grid())

        await controller.load(bundle)
        await controller.settle()
        for panel_id in FieldPanel:
            app.on_panel_removed({"id": str(panel_id)})

        assert not controller.live and controller.attached == set()

        app.rebuild_closed()
        await controller.settle()

        rebuilt = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        assert controller.live and controller.viewer is rebuilt
        assert any(fn == controller.on_field for fn, _ in app.state._change_callbacks.get(str(FieldVar.FIELD), []))
        assert str(SceneActor.BODIES) in rebuilt.plotter.renderer.actors, "the open run is drawn into the fresh plotter"

    asyncio.run(run())


def test_the_run_picker_names_a_bundle_the_index_does_not_know(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("offered", tmp_path, monkeypatch)
        controller = _controller(app)
        bundle = _bundle(tmp_path / "hex", _hex_grid())
        listed = [RunEntry("indexed", "indexed")]

        def entries(_telemetry: Telemetry | None) -> list[RunEntry]:
            return listed

        monkeypatch.setattr(field_viewer_module, "run_entries", entries)

        controller.open(bundle)
        await controller.settle()

        items = cast("list[dict[str, object]]", app.state[FieldVar.RUN_ITEMS])
        assert items == [{"title": "hex", "value": str(bundle), "detail": []}, {"title": "indexed", "value": "indexed", "detail": []}]
        assert app.state[FieldVar.RUN_PLACEHOLDER] == NO_RUN_SELECTED

        controller.open(bundle)
        await controller.settle()

        assert app.state[FieldVar.RUN_ITEMS] is items, "the same runs are not published again"

        listed = [RunEntry("hex", "hex", "4 mm gap")]
        controller.open(bundle)
        await controller.settle()

        assert app.state[FieldVar.RUN] == "hex", "a bundle the index lists is picked as its indexed run"
        assert app.state[FieldVar.RUN_ITEMS] == [{"title": "hex", "value": "hex", "detail": ["4 mm gap"]}]

    asyncio.run(run())


def test_the_picker_keeps_bundles_opened_by_path_the_open_run_and_the_run_being_opened() -> None:
    by_path = RunEntry("/runs/hex", "hex")
    indexed = [RunEntry("new", "new"), RunEntry("old", "old")]

    assert merged(indexed, [by_path, *indexed], None, "") == [by_path, *indexed], "a bundle opened by path stays on top"
    assert merged([RunEntry("hex", "hex"), *indexed], [by_path], None, "") == [RunEntry("hex", "hex"), *indexed], "the index takes over a bundle it lists"

    failed = RunEntry("gone", "gone", flag=RunFlag.NO_FIELDS)
    assert merged(indexed, indexed, failed, "gone") == [*indexed, failed], "the open run the index dropped stays, flagged"
    assert merged(indexed, indexed, by_path, str(by_path.value)) == [by_path, *indexed]

    picked = RunEntry("solving", "solving", flag=RunFlag.SOLVING)
    assert merged(indexed, [picked, *indexed], None, "solving") == [*indexed, picked], "the run just picked stays while it opens"
    assert merged([RunEntry("hex", "hex"), *indexed], [by_path], None, str(by_path.value)) == [RunEntry("hex", "hex"), *indexed], "never twice"


def test_a_run_reads_as_its_id_with_its_description_or_physics_muted_beside_it(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "hex", _hex_grid())
    plain = bundle_entry(Run(bundle), str(bundle), None)
    _ = write_manifest(bundle, RunManifest.build(SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"), meta=RunMeta(description="4 mm gap")), status=RunStatus.OK, run_id="hex"))
    described = bundle_entry(Run(bundle), "hex", RunFlag.SOLVING)

    assert plain == RunEntry(str(bundle), "hex") and plain.by_path and not described.by_path
    assert described.item() == {"title": "hex", "value": "hex", "detail": ["4 mm gap"], "flag": "Solving"}
    assert RunEntry("a", "a", None, "magnetostatics", 2).item() == {"title": "a", "value": "a", "detail": ["Magnetostatics", "2D"]}
    assert RunEntry("a", "a", flag=RunFlag.NO_FIELDS).item() == {"title": "a", "value": "a", "detail": [], "flag": "No Fields"}
    assert described.runs_dir == tmp_path, "an opened bundle knows where its claim lives"


def test_a_run_cleared_while_it_expands_is_not_drawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> tuple[object, bool]:
        app = _shell("cleared", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)

        await controller.load(_bundle(tmp_path / "quad", _quad_grid()))
        await controller.settle()
        scene = controller.scene
        assert scene is not None
        expand = scene.expand

        def slowly(spec: DisplaySpec) -> bool:
            time.sleep(0.3)
            return expand(spec)

        monkeypatch.setattr(scene, "expand", slowly)
        app.state[FieldVar.EXPANSION] = str(Expansion.SECTION)
        controller.request_redraw()
        await asyncio.sleep(0.05)
        controller.clear("gone")
        await controller.settle()

        return controller.drawn, str(SceneActor.BODIES) in viewer.plotter.renderer.actors

    assert asyncio.run(run()) == (None, False), "the expansion that finished after the clear draws nothing"


def test_a_failed_expansion_puts_the_show_as_choice_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> tuple[object, str]:
        app = _shell("failed-expansion", tmp_path, monkeypatch)
        controller = _controller(app)

        await controller.load(_bundle(tmp_path / "quad", _quad_grid()))
        await controller.settle()
        scene = controller.scene
        assert scene is not None

        def failing(_spec: DisplaySpec) -> bool:
            raise RuntimeError("no mirror today")

        monkeypatch.setattr(scene, "expand", failing)
        app.state[FieldVar.EXPANSION] = str(Expansion.MIRROR)
        controller.request_redraw()
        await controller.settle()

        return app.state[FieldVar.EXPANSION], str(app.state[StateVar.STATUS])

    assert asyncio.run(run()) == (str(Expansion.REVOLVE), "RuntimeError: no mirror today")


def test_opening_something_that_is_not_a_bundle_ends_in_the_status_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> str:
        app = _shell("missing", tmp_path, monkeypatch)
        controller = _controller(app)

        await controller.load(tmp_path / "missing")

        assert app.state[FieldVar.BUSY] is False
        with pytest.raises(FileNotFoundError, match="telemetry root"):
            _ = controller.resolve("some-run-id")

        return str(app.state["wb_status"])

    assert asyncio.run(run()).startswith("FileNotFoundError")


def test_opening_a_bundle_without_entities_ends_in_the_status_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> str:
        app = _shell("unnamed", tmp_path, monkeypatch)
        controller = _controller(app)

        bare = tmp_path / "bare"
        bare.mkdir()
        _hex_grid().save(str(bare / "case_t0001.vtu"))  # pyright: ignore[reportUnknownMemberType]  # pyvista's save signature is partially untyped

        await controller.load(bare)

        assert controller.scene is None
        assert app.state[FieldVar.BUSY] is False

        return str(app.state["wb_status"])

    assert "no named bodies" in asyncio.run(run())


# --- pure helpers -----------------------------------------------------------------


def test_body_rows_put_a_material_that_owns_several_bodies_over_them_with_the_looks_they_share() -> None:
    bodies = Bodies([Body("A1", 1, material="copper"), Body("A2", 2, material="copper"), Body("B", 3, material="steel"), Body("C", 4)])
    solid = BodyPaint.SOLID
    looks = {"A1": BodyStyle(body="A1", paint=solid, color="#ff0000", opacity=0.5), "A2": BodyStyle(body="A2", paint=solid, color="#ff0000"), "C": BodyStyle(body="C", paint=BodyPaint.COLORMAP)}

    rows = body_rows(bodies, ["A1", "A2", "B", "C"], looks, "gray")

    assert [(row[RowKey.ID], row[RowKey.GROUP], row[RowKey.MEMBER]) for row in rows] == [
        ("material:copper", True, False),
        ("A1", False, True),
        ("A2", False, True),
        ("B", False, False),
        ("C", False, False),
    ]
    assert rows[0][RowKey.MEMBERS] == ["A1", "A2"] and rows[0][RowKey.COLOR] == "#ff0000" and rows[0][RowKey.OPACITY] == [0.5]
    assert rows[0][RowKey.PAINT] == "solid" and rows[3][RowKey.PAINT] == "field" and rows[3][RowKey.COLOR] is None and rows[3][RowKey.OPACITY] == [1.0]
    assert rows[4][RowKey.PAINT] == "colormap" and rows[4][RowKey.COLOR] == "#808080", "the one colormap body takes the middle of the map"


def test_colormap_items_list_the_favorites_first_and_every_catalogue_name_once() -> None:
    items = colormap_items()
    names = [str(item.get("value")) for item in items]

    assert names[: len(FAVORITES)] == list(FAVORITES)
    assert sorted(names) == sorted(entry.name for entry in COLORMAPS)
    assert [item.get("label") for item in items[:2]] == ["Viridis", "Haline"]
    assert items[0].get("groups") == ["Favorites", "Sequential"] and items[len(FAVORITES)].get("groups") == ["Sequential"]
    assert all("swatch" in item for item in items), "every catalogue name resolves, the ParaView presets included"


def test_colormap_labels_are_unique_short_title_case_names() -> None:
    labels = [entry.label for entry in COLORMAPS]
    shown = [item.get("label", "") for item in colormap_items()]

    assert len(set(labels)) == len(labels)
    assert all(label[0].isupper() and len(label) <= MAX_LABEL for label in labels)
    assert not [label for label in labels if any(mark in label for mark in "_()")]
    assert "Linear Green" in shown and "Cool to Warm Extended" in shown, "the pickers read the labels in Title Case"


def test_state_coercions_tolerate_anything() -> None:
    assert names_of(["a", 1]) == ["a", "1"] and names_of("a") == []
    assert number_of(2, 5.0) == 2.0 and number_of(True, 5.0) == 5.0 and number_of("2", 5.0) == 5.0
    assert pair_of([1, 2.5]) == (1.0, 2.5)
    assert pair_of([1]) is None and pair_of([True, 2]) is None and pair_of("12") is None


def test_run_entries_are_empty_without_a_telemetry_root() -> None:
    assert run_entries(None) == []


def test_similar_bounds_needs_a_close_diagonal_and_centre() -> None:
    box = (0.0, 1.0, 0.0, 1.0, 0.0, 1.0)

    assert similar_bounds(box, (-0.1, 1.1, -0.1, 1.1, -0.1, 1.1))
    assert similar_bounds(box, (0.0, 1.9, 0.0, 1.9, 0.0, 1.9))
    assert not similar_bounds(box, (0.0, 3.0, 0.0, 3.0, 0.0, 3.0))
    assert not similar_bounds(box, (5.0, 6.0, 0.0, 1.0, 0.0, 1.0))
    assert not similar_bounds(box, (0.0, 0.0, 0.0, 0.0, 0.0, 0.0))


def test_a_colormap_swatch_is_a_css_gradient_and_an_unknown_one_has_none() -> None:
    gradient = colormap_gradient("viridis")

    assert gradient is not None and gradient.startswith("linear-gradient(to right, rgb(") and gradient.endswith("100%)")
    assert str(colormap_gradient("viridis", reverse=True)).startswith("linear-gradient(to left")
    assert colormap_gradient("no-such-colormap") is None


def test_the_body_table_sets_visibility_colors_and_opacities_that_reach_the_drawn_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("bodies", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        started = _start(app)
        _ = await app.server.ready

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        scene = controller.scene
        assert scene is not None and scene.spec.body_styles == ()

        app.state[FieldVar.OPACITY] = [0.4]
        controller.on_commit()
        await controller.settle()
        overall = scene.spec.opacity

        with app.state:
            app.state[FieldVar.OPACITY_RANGE] = [0.1, 0.8]
            app.state[FieldVar.OPACITY_MAPPING] = True

        controller.on_commit()
        await controller.settle()
        mapped = scene.spec.opacity_map

        controller.on_row_colormap("LEFT")
        await controller.settle()
        solid = scene.spec.solid()
        with app.state:
            app.state[FieldVar.BODY_COLORMAP] = "gray"

        await controller.settle()
        recolored = (scene.spec.solid(), cast("list[dict[str, object]]", app.state[FieldVar.BODY_ROWS])[0][RowKey.COLOR])
        controller.on_row_color("LEFT", "#ff0000")
        with app.state:
            app.state[FieldVar.BODY_COLORMAP] = "viridis"

        await controller.settle()
        kept = scene.spec.solid()
        controller.on_row_opacity("RIGHT", 0.3)
        moved = scene.spec.body_styles
        controller.on_commit()
        await controller.settle()
        styled = scene.spec.body_styles
        actor = SceneActor.STYLED.of("LEFT") in viewer.plotter.renderer.actors
        rows = cast("list[dict[str, object]]", app.state[FieldVar.BODY_ROWS])

        controller.on_row_visible("RIGHT", False)
        await controller.settle()
        hidden = scene.spec.hidden_bodies
        controller.on_row_visible("RIGHT", True)
        controller.on_row_field("LEFT")
        await controller.settle()
        cleared = scene.spec.solid()

        await app.server.stop()
        await cast("asyncio.Future[object]", started)

        assert overall == 0.4 and mapped == (0.1, 0.8)
        assert set(solid) == {"LEFT"} and solid["LEFT"].startswith("#")
        assert recolored == ({"LEFT": "#808080"}, "#808080"), "the body colormap recolors its bodies live"
        assert kept == {"LEFT": "#ff0000"}, "a color picked by hand stays through a body colormap change"
        assert len(moved) == 1, "a thumb moving redraws nothing until its release"
        assert sorted(styled, key=lambda style: style.body) == [BodyStyle(body="LEFT", paint=BodyPaint.SOLID, color="#ff0000"), BodyStyle(body="RIGHT", opacity=0.3)] and actor
        assert (rows[0][RowKey.COLOR], rows[1][RowKey.OPACITY]) == ("#ff0000", [0.3])
        assert hidden == ("RIGHT",)
        assert cleared == {} and SceneActor.STYLED.of("LEFT") not in viewer.plotter.renderer.actors
        assert scene.spec.body_styles == (BodyStyle(body="RIGHT", opacity=0.3),)

    asyncio.run(run())


def test_a_body_row_cycles_field_then_body_colormap_and_a_hand_picked_color_holds_until_cleared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> list[tuple[str, str | None]]:
        app = _shell("cycle", tmp_path, monkeypatch)
        controller = _controller(app)
        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        steps: list[tuple[str, str | None]] = []

        def note() -> None:
            look = controller.look("LEFT")
            steps.append((str(look.paint), look.color))

        controller.on_row_colormap("LEFT")
        note()
        controller.on_row_color("LEFT", "#00ff00")
        note()
        controller.on_row_field("LEFT")
        note()
        controller.on_row_colormap("LEFT")
        controller.on_body_colormap_pick(SAME_AS_FIELD)
        note()
        assert controller.members("no-such-row") == []
        await controller.settle()

        return steps

    assert asyncio.run(run()) == [("colormap", None), ("solid", "#00ff00"), ("field", None), ("field", None)]


def test_the_controls_offer_a_color_input_per_row_and_a_swatch_per_colormap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("swatches", tmp_path, monkeypatch)
    template = _template(app, FieldPanel.CONTROLS)

    colors = [node for node in find(template, "ff-input") if prop(node, "type") == "color"]
    swatches = [node for node in find(template, "span") if "linear-gradient" in str(prop(node, "style"))]
    conditions = [_bound(node, "value") for node in find(template, "ReactIf")]
    pickers = find(template, "ff-combobox")

    assert len(colors) == 1 and _bound(colors[0], "value") == f"item.{RowKey.COLOR}"
    assert swatches == [], "the swatches travel in the state, not the template"
    assert [_bound(node, "items") for node in pickers] == [str(FieldVar.COLORMAP_ITEMS)] * 2
    assert _bound(pickers[1], "lead") == str(FieldVar.BODY_LEAD)
    assert [prop(node, "compact") for node in pickers] == [None, True], "the body colormap sits in the color column as a swatch"
    reveals = [_bound(node, "open") for node in find(template, "ff-reveal")]
    assert str(FieldVar.OPACITY_MAPPING) in reveals and str(FieldVar.WARP) in reveals, "the controls a switch governs expand and collapse with it"
    assert str(FieldVar.OPACITY_MAPPING) not in str(conditions)
    assert app.state[FieldVar.FIELD_GRADIENT] == colormap_gradient("turbo")


def test_a_startup_field_line_region_names_its_body_on_the_first_run_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> tuple[object, object]:
        app = _shell("region", tmp_path, monkeypatch)
        controller = _controller(app)
        controller.lines_region = 2

        await controller.load(_bundle(tmp_path / "first", _hex_grid()))
        await controller.settle()
        first = app.state[FieldVar.LINES_BODY]

        with app.state:
            app.state[FieldVar.LINES_BODY] = ""

        await controller.load(_bundle(tmp_path / "second", _hex_grid()))
        await controller.settle()

        return first, app.state[FieldVar.LINES_BODY]

    assert asyncio.run(run()) == ("RIGHT", "")


def test_the_field_colormap_restyles_the_field_lines_without_integrating_them_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> tuple[int, object]:
        app = _shell("line-colors", tmp_path, monkeypatch)
        controller = _controller(app)
        started = _start(app)
        _ = await app.server.ready

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        scene = controller.scene
        assert scene is not None
        integrations: list[object] = []
        integrate = scene.integrate_streamlines

        def counted(options: FieldLineOptions) -> object:
            integrations.append(options)
            return integrate(options)

        monkeypatch.setattr(scene, "integrate_streamlines", counted)
        with app.state:
            app.state[FieldVar.LINE_COUNT] = [2]
            app.state[FieldVar.STREAMLINES] = True

        await controller.settle()
        with app.state:
            app.state[FieldVar.COLORMAP] = "lipari"
            app.state[FieldVar.INVERT] = True

        await controller.settle()
        drawn = scene.drawn_lines
        await app.server.stop()
        await cast("asyncio.Future[object]", started)

        return len(integrations), drawn

    count, drawn = asyncio.run(run())

    assert count == 1
    assert isinstance(drawn, FieldLineOptions) and drawn.colormap == "lipari" and drawn.invert is True


def test_the_body_colormap_picker_reads_same_as_field_until_a_pick_paints_the_shown_bodies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> list[tuple[object, dict[str, str]]]:
        app = _shell("body-picker", tmp_path, monkeypatch)
        controller = _controller(app)
        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        steps: list[tuple[object, dict[str, str]]] = []

        def note() -> None:
            steps.append((app.state[FieldVar.BODY_COLORMAP_SHOWN], {name: str(controller.look(name).paint) for name in ("LEFT", "RIGHT")}))

        note()
        with app.state:
            app.state[FieldVar.VISIBLE_BODIES] = ["LEFT"]

        controller.on_body_colormap_pick("gray")
        note()
        controller.on_body_colormap_pick(SAME_AS_FIELD)
        note()
        controller.on_row_color("LEFT", "#00ff00")
        controller.on_body_colormap_pick("viridis")
        note()
        await controller.settle()

        return steps

    assert asyncio.run(run()) == [
        (SAME_AS_FIELD, {"LEFT": "field", "RIGHT": "field"}),
        ("gray", {"LEFT": "colormap", "RIGHT": "field"}),
        (SAME_AS_FIELD, {"LEFT": "field", "RIGHT": "field"}),
        ("viridis", {"LEFT": "colormap", "RIGHT": "field"}),
    ], "with no shown field-colored body, a pick takes every shown body"


# --- symmetry and the tour ------------------------------------------------------


def test_a_flat_run_revolves_into_the_plotter_by_default_and_flattens_on_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> tuple[list[object], list[object], list[object]]:
        app = _shell("symmetry", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        started = _start(app)
        _ = await app.server.ready

        await controller.load(_bundle(tmp_path / "quad", _quad_grid()))
        await controller.settle()
        scene = controller.scene
        assert scene is not None
        revolved: list[object] = [app.state[FieldVar.SYMMETRY], app.state[FieldVar.SOLVED_AS], scene.display_dim, viewer.plotter.bounds[0], controller.drawn_dim]

        with app.state:
            app.state[FieldVar.EXPANSION] = str(Expansion.MIRROR)

        await controller.settle()
        mirrored: list[object] = [scene.display_dim, viewer.plotter.bounds[0], viewer.plotter.bounds[5], controller.drawn_dim]

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        solid: list[object] = [app.state[FieldVar.SYMMETRY], app.state[FieldVar.SOLVED_AS]]

        await app.server.stop()
        await cast("asyncio.Future[object]", started)

        return revolved, mirrored, solid

    revolved, mirrored, solid = asyncio.run(run())

    assert revolved == ["open", "Axisymmetric About Z", 3, pytest.approx(-2.0), 3], "a section opens revolved into a solid"
    assert mirrored == [2, pytest.approx(-2.0), pytest.approx(0.0), 2], "a mirrored section is flat and draws in the same view"
    assert solid == ["fixed", "Solid"]


def test_a_cartesian_section_extrudes_by_default_and_a_revolving_one_takes_the_revolve_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from femforge.frames import Cartesian2D

    async def run() -> tuple[list[object], list[object], list[object]]:
        app = _shell("extrude", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        started = _start(app)
        _ = await app.server.ready

        bundle = _bundle(tmp_path / "planar", _quad_grid())
        config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"), frame=Cartesian2D(thickness=quantity(10, "mm")), physics=PhysicsSelection(selected=Physics.THERMAL))
        _ = write_manifest(bundle, RunManifest.build(config, status=RunStatus.OK, run_id="planar"))
        await controller.load(bundle)
        await controller.settle()
        scene = controller.scene
        assert scene is not None
        extruded: list[object] = [app.state[FieldVar.EXPANSIONS], app.state[FieldVar.EXPANSION], scene.display_dim, controller.display_dim(), viewer.plotter.bounds[4], viewer.plotter.bounds[5]]

        with app.state:
            app.state[FieldVar.EXPANSION] = str(Expansion.SECTION)

        await controller.settle()
        flat: list[object] = [scene.display_dim, controller.display_dim(), controller.drawn_dim]

        with app.state:
            app.state[FieldVar.EXPANSION] = str(Expansion.EXTRUDE)

        await controller.load(_bundle(tmp_path / "quad", _quad_grid()))
        await controller.settle()
        revolving: list[object] = [app.state[FieldVar.EXPANSIONS], app.state[FieldVar.EXPANSION], controller.drawn_dim]

        await app.server.stop()
        await cast("asyncio.Future[object]", started)

        return extruded, flat, revolving

    extruded, flat, revolving = asyncio.run(run())

    assert extruded == [["section", "extrude"], "extrude", 3, 3, pytest.approx(-0.005), pytest.approx(0.005)], "a Cartesian section opens extruded through its thickness"
    assert flat == [2, 2, 2]
    assert revolving == [["section", "mirror", "revolve"], "revolve", 3], "an extrusion asked of a revolving section reads as its revolve"


def test_the_tour_opens_once_after_the_first_run_shows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from femforge_workbench import app as shell
    from femforge_workbench.persist import StateKey

    monkeypatch.setattr(shell, "TOUR_DELAY", 0.0)

    async def run() -> tuple[object, object, object, object, list[str]]:
        app = _shell("tour", tmp_path, monkeypatch)
        controller = _controller(app)
        started = _start(app)
        _ = await app.server.ready

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        await asyncio.sleep(0.05)
        opened = app.state[StateVar.TOUR_OPEN]
        unseen = app.persist.load(app.scope, StateKey.TOUR)

        with app.state:
            app.state[StateVar.TOUR_OPEN] = False

        app.on_tour_finish("skipped")
        seen = app.persist.load(app.scope, StateKey.TOUR)
        app.offer_tour()
        await asyncio.sleep(0.05)
        again = app.state[StateVar.TOUR_OPEN]
        targets = [str(cast("dict[str, object]", step)["target"]) for step in cast("list[object]", app.state[StateVar.TOUR_STEPS])]

        await app.server.stop()
        await cast("asyncio.Future[object]", started)

        return opened, unseen, seen, again, targets

    opened, unseen, seen, again, targets = asyncio.run(run())

    assert opened is True and unseen is None, "a tour counts as seen once it ends, not once it opens"
    assert seen == {"seen": True}
    assert again is False, "a tour finished or skipped once never opens on its own again"
    assert targets[:2] == ["#field3d-view", "#tour-run"] and targets[-1] == "#tour-help"
    assert targets.index("#tour-deformation") == targets.index("#tour-symmetry") + 1


def test_a_moved_range_thumb_pins_the_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> tuple[object, object]:
        app = _shell("pin", tmp_path, monkeypatch)
        controller = _controller(app)
        started = _start(app)
        _ = await app.server.ready

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        app.state[FieldVar.CLIM] = [2.2, 2.8]
        controller.on_clim_commit()
        await controller.settle()
        scene = controller.scene
        pinned = (app.state[FieldVar.AUTO_CLIM], scene.spec.clim if scene is not None else None)

        await app.server.stop()
        await cast("asyncio.Future[object]", started)

        return pinned

    auto, clim = asyncio.run(run())

    assert auto is False and clim == (2.2, 2.8)


# --- the live run picker ---------------------------------------------------------


CREATED = datetime(2026, 10, 1, tzinfo=timezone.utc)
"""When the first indexed test run was created; each later one a minute after."""


def _index(telemetry: Telemetry, runs_dir: Path, run_id: str, minute: int, status: str, *, has_vtu: bool, description: str | None = None) -> None:
    created = CREATED + timedelta(minutes=minute)
    row: dict[str, object] = {name: None for name in RUN_INDEX_SCHEMA.names()}
    row.update(
        {
            RunColumn.MONTH: created.date().replace(day=1),
            RunColumn.RUN_ID: run_id,
            RunColumn.RUNS_DIR: str(runs_dir),
            RunColumn.STATUS: status,
            RunColumn.HAS_VTU: has_vtu,
            RunColumn.DESCRIPTION: description,
            RunColumn.CREATED_AT: created,
            RunColumn.INDEXED_AT: datetime.now(timezone.utc),
            RunColumn.SOURCE: "scan",
        }
    )
    _ = telemetry.runs.append(pl.DataFrame([row], schema=RUN_INDEX_SCHEMA))


def _claim(runs_dir: Path, run_id: str) -> DirLock:
    lock = DirLock(runs_dir / f"{run_id}{LOCK_SUFFIX}")
    assert lock.try_acquire()

    return lock


def _live_shell(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: Path | None = None) -> Workbench:
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)
    app = Workbench(name, title="Fields", server=get_server(f"field-viewer-{name}"), persist=LocalJsonStore(tmp_path / "persist"), user="tester")
    app.add(workbench(run, telemetry=tmp_path / "telemetry"))

    return app


def _feed(controller: FieldViewerController) -> Feed[list[RunEntry] | None]:
    feed = controller.feed
    assert feed is not None

    return feed


def _values(app: Workbench) -> list[object]:
    return [item[ItemField.VALUE] for item in cast("list[dict[str, object]]", app.state[FieldVar.RUN_ITEMS])]


async def _until(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 20.0
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.01)


def test_the_index_offers_runs_with_fields_and_runs_solving_under_a_live_claim(tmp_path: Path) -> None:
    telemetry = Telemetry(tmp_path / "telemetry")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _index(telemetry, runs_dir, "solved", 0, "ok", has_vtu=True, description="1 mm gap")
    _index(telemetry, runs_dir, "live", 1, "running", has_vtu=False)
    _index(telemetry, runs_dir, "crashed", 2, "running", has_vtu=False)
    _index(telemetry, runs_dir, "failed", 3, "error", has_vtu=False)
    claim = _claim(runs_dir, "live")

    entries = run_entries(telemetry)
    claim.release()

    failed = RunEntry("failed", "failed", flag=RunFlag.FAILED)
    assert entries == [failed, RunEntry("live", "live", flag=RunFlag.SOLVING), RunEntry("solved", "solved", "1 mm gap")], "a solve that died without a claim is not listed"
    assert run_entries(telemetry) == [failed, RunEntry("solved", "solved", "1 mm gap")]
    assert entries[1].runs_dir == runs_dir, "a solving run knows where its claim lives"


def test_the_run_feed_reads_the_index_again_only_when_its_shards_changed_or_a_solving_run_lost_its_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _live_shell("gated", tmp_path, monkeypatch)
    controller = _controller(app)
    feed = _feed(controller)
    telemetry = Telemetry(tmp_path / "telemetry")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    assert feed.period == 1.0
    assert feed.poll() == [] and feed.poll() is None, "an idle index costs one glob"

    _index(telemetry, runs_dir, "live", 0, "running", has_vtu=False)
    claim = _claim(runs_dir, "live")
    found = feed.poll()
    controller.indexed = found or []

    assert found == [RunEntry("live", "live", flag=RunFlag.SOLVING)]
    assert feed.poll() is None, "a solve that still holds its claim costs one glob and one claim check"

    claim.release()
    controller.indexed = feed.poll() or []

    assert controller.indexed == [] and feed.poll() is None, "a claim gone reads the index again, which drops the dead solve"


def test_the_newest_run_with_fields_opens_once_and_a_run_that_lands_later_never_replaces_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _live_shell("arrivals", tmp_path, monkeypatch)
        controller = _controller(app)
        feed = _feed(controller)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        telemetry = Telemetry(tmp_path / "telemetry")
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()

        assert app.state[FieldVar.RUN_PLACEHOLDER] == NO_RUNS_YET and app.state[viewer.key(SceneVar.NOTICE)] == NO_RUNS_NOTICE

        _ = _bundle(runs_dir / "solved", _hex_grid())
        _index(telemetry, runs_dir, "solved", 0, "ok", has_vtu=True, description="0.5 mm gap")
        (runs_dir / "solving").mkdir()
        _index(telemetry, runs_dir, "solving", 1, "running", has_vtu=False, description="1 mm gap")
        claim = _claim(runs_dir, "solving")

        controller.publish_runs(feed.poll())
        await controller.settle()

        assert controller.scene is not None and controller.scene.run_id == "solved", "a run with fields opens before a newer one still solving"
        assert app.state[FieldVar.RUN] == "solved" and app.state[FieldVar.RUN_PLACEHOLDER] == NO_RUN_SELECTED
        assert app.state[FieldVar.RUN_ITEMS] == [
            {"title": "solving", "value": "solving", "detail": ["1 mm gap"], "flag": "Solving"},
            {"title": "solved", "value": "solved", "detail": ["0.5 mm gap"]},
        ]

        _ = _bundle(runs_dir / "newer", _hex_grid())
        _index(telemetry, runs_dir, "newer", 2, "ok", has_vtu=True)
        opening = controller.load_task
        controller.publish_runs(feed.poll())

        assert controller.load_task is opening and app.state[FieldVar.RUN] == "solved", "a run that lands never steals the view"
        assert _values(app) == ["newer", "solving", "solved"]

        app.state[FieldVar.RUN_PICKING] = True
        controller.on_picking()
        held = app.state[FieldVar.RUN_ITEMS]
        _index(telemetry, runs_dir, "newest", 3, "ok", has_vtu=True)
        controller.publish_runs(feed.poll())

        assert app.state[FieldVar.RUN_ITEMS] is held, "no row moves under the pointer while the dropdown is open"

        app.state[FieldVar.RUN_PICKING] = False
        controller.on_picking()

        assert _values(app) == ["newest", "newer", "solving", "solved"]

        app.state[FieldVar.RUN_PICKING] = True
        controller.on_picking()
        controller.picked_at -= PICK_HOLD
        _index(telemetry, runs_dir, "latest", 4, "ok", has_vtu=True)
        controller.publish_runs(feed.poll())

        assert _values(app)[0] == "latest", "a dropdown whose page went away without closing holds the list for a while only"

        app.state[FieldVar.RUN_PICKING] = False
        controller.on_picking()

        published = app.state[FieldVar.RUN_ITEMS]
        controller.publish_runs(feed.poll())
        claim.release()

        assert app.state[FieldVar.RUN_ITEMS] is published, "an unchanged index writes nothing"

    asyncio.run(run())


def test_the_newest_run_solving_opens_when_none_has_fields_and_keeps_the_busy_bar_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _live_shell("waiting", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        telemetry = Telemetry(tmp_path / "telemetry")
        runs_dir = tmp_path / "runs"
        (runs_dir / "solving").mkdir(parents=True)
        _index(telemetry, runs_dir, "solving", 0, "running", has_vtu=False, description="0.5 mm gap")
        claim = _claim(runs_dir, "solving")

        controller.publish_runs(_feed(controller).poll())
        await controller.settle()
        busy = app.state[FieldVar.BUSY]
        waiting = controller.wait_task
        controller.release()
        claim.release()

        assert app.state[viewer.key(SceneVar.NOTICE)] == "0.5 mm gap is still solving. Its fields appear here when it finishes; the solver's terminal shows its progress."
        assert busy is True and waiting is not None and app.state[FieldVar.RUN] == "solving"

    asyncio.run(run())


def test_nothing_opens_by_itself_once_a_run_was_asked_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _live_shell("asked", tmp_path, monkeypatch, run=tmp_path / "missing")
        controller = _controller(app)
        runs_dir = tmp_path / "runs"
        _ = _bundle(runs_dir, _hex_grid())
        _index(Telemetry(tmp_path / "telemetry"), tmp_path, "runs", 0, "ok", has_vtu=True)

        controller.publish_runs(_feed(controller).poll())

        assert controller.load_task is None, "the dock was given a run"

        controller.open_newest = True
        app.state[Shared.SELECTED_RUN_ID] = "runs"
        controller.publish_runs(None)

        assert controller.load_task is None, "another panel selected a run"

    asyncio.run(run())


def test_an_unreadable_index_keeps_the_runs_last_read_says_so_and_logs_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_telemetry: Telemetry | None) -> list[RunEntry]:
        raise OSError("disk gone")

    def empty(_telemetry: Telemetry | None) -> list[RunEntry]:
        return []

    app = _live_shell("broken", tmp_path, monkeypatch)
    controller = _controller(app)
    feed = _feed(controller)
    monkeypatch.setattr(field_viewer_module, "run_entries", broken)
    messages: list[str] = []
    sink = log.add(messages.append, level="WARNING", format="{message}")
    try:
        polls = [feed.poll(), feed.poll()]
    finally:
        log.remove(sink)

    controller.publish_runs(None)

    assert polls == [None, None], "the last runs stay"
    assert sum("Run index unavailable." in message for message in messages) == 1, "a failure streak logs once"
    assert app.state[FieldVar.RUN_PLACEHOLDER] == INDEX_UNREADABLE and app.state[FieldVar.RUN_ITEMS] == []

    controller.indexed = [RunEntry("kept", "kept")]

    assert controller.scan_runs() == [RunEntry("kept", "kept")]

    monkeypatch.setattr(field_viewer_module, "run_entries", empty)

    assert feed.poll() == [] and not controller.unreadable


def test_a_failed_poll_leaves_the_offered_runs_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _live_shell("raising", tmp_path, monkeypatch)
        controller = _controller(app)
        feed = _feed(controller)
        controller.open_newest = False
        controller.indexed = [RunEntry("kept", "kept")]
        controller.publish_runs(None)
        items = app.state[FieldVar.RUN_ITEMS]
        calls = 0

        def poll() -> list[RunEntry] | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("shard caught mid-write")

            return None

        feed.poll = poll
        feed.period = 0.01
        task = asyncio.create_task(feed.cycle())
        await _until(lambda: feed.polls >= 1)
        _ = task.cancel()

        assert calls >= 2 and app.state[FieldVar.RUN_ITEMS] is items

    asyncio.run(run())


def test_a_claim_is_live_while_its_holder_beats(tmp_path: Path) -> None:
    assert not claimed(tmp_path, "run")

    lock = _claim(tmp_path, "run")

    assert claimed(tmp_path, "run")

    owner = lock.owner()
    assert owner is not None
    silent = owner.model_copy(update={"heartbeat_at": owner.heartbeat_at - timedelta(hours=1)})
    _ = (lock.path / OWNER_FILE).write_text(silent.model_dump_json(), encoding="utf-8")

    assert not claimed(tmp_path, "run"), "a holder that stopped beating holds nothing"


def test_a_solve_that_dies_ends_the_wait_and_the_busy_bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("femforge_workbench.panels.field_viewer.FIELDS_PERIOD", 0.01)

    async def run() -> None:
        app = _shell("dead", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        runs_dir = tmp_path / "runs"
        pending = runs_dir / "pending"
        pending.mkdir(parents=True)
        claim = _claim(runs_dir, "pending")

        await controller.load(pending)
        waiting = controller.wait_task

        assert waiting is not None and app.state[FieldVar.BUSY] is True

        claim.release()
        await waiting
        await controller.settle()

        assert app.state[FieldVar.BUSY] is False and app.state[viewer.key(SceneVar.NOTICE)] == "pending: The solve wrote no fields"
        assert cast("list[dict[str, object]]", app.state[FieldVar.RUN_ITEMS])[0][ItemField.FLAG] == "No Fields", "a dead solve never reads Solving"

    asyncio.run(run())


def test_the_open_run_loses_its_solving_flag_once_its_solve_ends_and_stays_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("femforge_workbench.panels.field_viewer.FIELDS_PERIOD", 0.01)

    async def run() -> None:
        app = _live_shell("ending", tmp_path, monkeypatch)
        controller = _controller(app)
        feed = _feed(controller)
        telemetry = Telemetry(tmp_path / "telemetry")
        runs_dir = tmp_path / "runs"
        pending = runs_dir / "solving"
        pending.mkdir(parents=True)
        _index(telemetry, runs_dir, "solving", 0, "running", has_vtu=False, description="0.5 mm gap")
        claim = _claim(runs_dir, "solving")

        controller.publish_runs(feed.poll())
        await controller.settle()
        waiting = controller.wait_task

        assert waiting is not None and app.state[FieldVar.RUN] == "solving"

        built = _bundle(tmp_path / "built", _hex_grid())
        for file in built.iterdir():
            _ = file.rename(pending / file.name)

        _index(telemetry, runs_dir, "solving", 0, "ok", has_vtu=True, description="0.5 mm gap")
        claim.release()
        await waiting
        await controller.settle()

        assert controller.seen is None, "an open reads the index itself, so the next tick reads it again"

        controller.publish_runs(feed.poll())
        await controller.settle()

        assert controller.scene is not None and controller.scene.run_id == "solving" and app.state[FieldVar.RUN] == "solving"
        assert app.state[FieldVar.RUN_ITEMS] == [{"title": "solving", "value": "solving", "detail": ["0.5 mm gap"]}]
        assert app.state[FieldVar.BUSY] is False

    asyncio.run(run())


def test_a_failed_run_opens_when_nothing_else_is_listed_and_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _live_shell("failed", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        runs_dir = tmp_path / "runs"
        (runs_dir / "broken").mkdir(parents=True)
        _index(Telemetry(tmp_path / "telemetry"), runs_dir, "broken", 0, "error", has_vtu=False, description="0.5 mm gap")

        controller.publish_runs(_feed(controller).poll())
        await asyncio.sleep(0)
        opening = app.state[viewer.key(SceneVar.NOTICE)]
        await controller.settle()

        assert opening == "Opening 0.5 mm gap", "the run is named the way the picker names it"
        assert app.state[FieldVar.RUN] == "broken" and app.state[FieldVar.BUSY] is False
        assert app.state[viewer.key(SceneVar.NOTICE)] == "broken: The solve wrote no fields"
        assert app.state[FieldVar.RUN_ITEMS] == [{"title": "broken", "value": "broken", "detail": ["0.5 mm gap"], "flag": "Failed"}]

    asyncio.run(run())


def test_the_run_feed_runs_with_the_server_and_stops_with_the_last_panel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        _ = _bundle(runs_dir / "solved", _hex_grid())
        _index(Telemetry(tmp_path / "telemetry"), runs_dir, "solved", 0, "ok", has_vtu=True)
        app = _live_shell("lifecycle", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        feed = _feed(controller)

        assert app.state[viewer.key(SceneVar.NOTICE)] == "No Run Open" and not feed.running

        started = _start(app)
        _ = await app.server.ready
        await _until(lambda: controller.scene is not None)
        running = feed.running
        for panel_id in FieldPanel:
            app.on_panel_removed({"id": str(panel_id)})

        stopped = not feed.running
        await app.server.stop()
        await cast("asyncio.Future[object]", started)

        assert running and stopped
        assert controller.scene is not None and controller.scene.run_id == "solved", "the feed opened the run once the server ran"

    asyncio.run(run())


def test_with_tasks_the_empty_view_points_at_the_strip_and_a_task_newer_run_replaces_its_previous_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)
    app = Workbench("follow", title="Fields", server=get_server("field-viewer-follow"), persist=LocalJsonStore(tmp_path / "persist"), user="tester")
    app.board = TaskBoard(app, "main:tasks", [Task("Solve", time.time)], path=None, roots=[])
    app.add(workbench(telemetry=tmp_path / "telemetry"))
    controller = _controller(app)
    viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
    opened: list[object] = []
    monkeypatch.setattr(controller, "open", opened.append)

    waiting = app.state[viewer.key(SceneVar.NOTICE)]
    app.board.by_label["Solve"] = app.board.by_label["Solve"].model_copy(update={"phase": TaskPhase.FAILED})
    app.board.queue.clear()
    app.board.publish(now=True)
    failed = app.state[viewer.key(SceneVar.NOTICE)]
    app.board.by_label["Solve"] = app.board.by_label["Solve"].model_copy(update={"phase": TaskPhase.STALLED})
    app.board.publish(now=True)
    stalled = app.state[viewer.key(SceneVar.NOTICE)]
    app.board.by_label["Solve"] = app.board.by_label["Solve"].model_copy(update={"phase": TaskPhase.DONE})
    app.board.publish(now=True)
    done = app.state[viewer.key(SceneVar.NOTICE)]

    app.follow_run("r1", "r2")
    controller.opened = RunEntry("r1", "r1")
    app.follow_run("r0", "r2")
    app.follow_run("r1", "r2")
    app.board.by_label["Solve"] = app.board.by_label["Solve"].model_copy(update={"phase": TaskPhase.FAILED})
    app.board.publish(now=True)

    assert waiting == "Waiting for Solve · Queued\nIts progress shows in the task strip below."
    assert failed == "No runs yet. 1 task failed; open the task strip for the reasons."
    assert stalled == "No runs yet. 1 task stalled; open the task strip for the reasons."
    assert done == NO_RUNS_TASKS_NOTICE
    assert app.state[viewer.key(SceneVar.NOTICE)] == NO_RUNS_TASKS_NOTICE, "a viewer showing a run keeps its notice"
    assert opened == ["r2"], "only the run the viewer shows is followed"
    assert app.state[StateVar.STATUS] == "Opened the newer run r2", "a followed run says why the view changed"
    assert [row["label"] for row in cast("list[dict[str, object]]", app.state[StateVar.TASKS])] == ["Solve"]


# --- the Setup tab's glyph layer ------------------------------------------------


MESH_NODES = """\
1 -1 0 0 0
2 -1 1 0 0
3 -1 1 1 0
4 -1 0 1 0
5 -1 2 0 0
6 -1 2 1 0
"""

MESH_ELEMENTS = """\
1 1 303 1 2 3
2 1 303 1 3 4
3 2 303 2 5 6
4 2 303 2 6 3
"""

MESH_BOUNDARY = """\
1 3 2 0 202 5 6
2 4 1 0 202 4 1
"""


def _glyph_bundle(root: Path, *, fields: bool = True) -> Path:
    grid = _elastic_grid()
    cells = np.concatenate([np.asarray(grid.cells, dtype=np.int64), np.array([4, 9, 10, 14, 13])])  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # pyvista types the cell arrays loosely
    types = np.append(np.asarray(grid.celltypes, dtype=np.uint8), np.uint8(pv.CellType.QUAD))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # pyvista types the cell arrays loosely
    tipped = pv.UnstructuredGrid(cells, types, np.asarray(grid.points, dtype=np.float64))
    for name in grid.point_data.keys():
        tipped.point_data[name] = np.asarray(grid.point_data[name])

    tipped.cell_data["GeometryIds"] = np.array([1, 2, 101], dtype=np.int32)
    bundle = _bundle(root, tipped)
    bodies = {1: BodyRecord(name="LEFT", centroid=Vec3(0.5, 0.5, 0.5)), 2: BodyRecord(name="RIGHT", centroid=Vec3(1.5, 0.5, 0.5))}
    boundaries = {3: BoundaryRecord(name="TIP", bodies=("RIGHT",), named=True, centroid=Vec3(2, 0.5, 0.5), normal=Direction(1, 0, 0)), 4: BoundaryRecord(name="LEFT__EXT", bodies=("LEFT",))}
    _ = (bundle / BundleFile.ENTITIES).write_text(EntitiesFile(bodies=bodies, boundaries=boundaries).model_dump_json(), encoding="utf-8")
    _ = (bundle / BundleFile.BOUNDARIES).write_bytes(b"1: TIP_surface_force\n")
    pull = ConditionRecord(
        kind="surface_force",
        condition=SurfaceForce(force=quantity(Vec3(100, 0, 0), "N")),
        section=SifSection.BOUNDARY_CONDITION,
        block=1,
        block_name="TIP_surface_force",
        solve_components=[SolveComponent(index=1, value=100.0), SolveComponent(index=2, value=0.0), SolveComponent(index=3, value=0.0)],
    )
    _ = ConditionsFile(bodies=[GroupConditions(group="LEFT"), GroupConditions(group="RIGHT")], boundaries=[GroupConditions(group="TIP", conditions=[pull])]).write(bundle)
    for name, text in ((BundleFile.MESH_NODES, MESH_NODES), (BundleFile.MESH_ELEMENTS, MESH_ELEMENTS), (BundleFile.MESH_BOUNDARY, MESH_BOUNDARY)):
        _ = (bundle / name).write_bytes(text.encode())

    if not fields:
        (bundle / "case_t0001.vtu").unlink()

    return bundle


def _glyph_actors(viewer: FieldViewerPanel) -> list[str]:
    return sorted(str(name) for name in viewer.plotter.renderer.actors if str(name).startswith("glyph-"))


def _arrow_bounds(viewer: FieldViewerPanel) -> tuple[float, ...]:
    actor = viewer.plotter.renderer.actors["glyph-arrows"]
    assert isinstance(actor, pv.Actor)

    return tuple(actor.GetBounds())


def test_the_glyph_keys_default_to_a_hidden_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("glyph-defaults", tmp_path, monkeypatch)
    controller = _controller(app)

    assert [app.state[key] for key in GLYPH_KEYS] == [False, [], "", True, False]
    assert controller.glyph_view_from_state() is None


def test_the_setup_tab_draws_the_glyphs_while_shown_follows_the_warp_and_removes_them_when_hidden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("glyphs", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)

        await controller.load(_glyph_bundle(tmp_path / "tipped"))
        await controller.settle()
        scene = controller.scene
        assert scene is not None and isinstance(scene.glyphs, GlyphLayer)
        assert _glyph_actors(viewer) == [], "the glyphs show only while the Setup tab does"

        app.state[FieldVar.SETUP_SHOWN] = True
        app.state[FieldVar.GLYPH_KINDS_OFF] = ["thermal"]
        app.state[FieldVar.SELECTED_ENTITY] = "boundary:TIP"
        controller.on_glyphs()
        await controller.settle()

        shown = _glyph_actors(viewer)
        assert {"glyph-arrows", "glyph-halos", "glyph-selection-fill", "glyph-labels-labels"} <= set(shown)
        view = scene.glyph_view
        assert view is not None and view.selected == "boundary:TIP" and "thermal" not in view.kinds and view.scale == viewer.window_scale()
        before = _arrow_bounds(viewer)
        auto = scene.auto_warp_scale("displacement")

        app.state[FieldVar.WARP_SCALE] = [3.0 * auto]
        controller.on_commit()
        await controller.settle()

        assert _arrow_bounds(viewer)[0] > before[0], "the arrow tail rides the displaced face"

        app.state[FieldVar.GLYPH_KINDS_OFF] = [str(kind) for kind in ConditionGlyph]
        controller.on_glyphs()
        await controller.settle()

        assert "glyph-arrows" not in _glyph_actors(viewer) and "glyph-selection-fill" in _glyph_actors(viewer), "All Glyphs off keeps the selection"

        app.state[FieldVar.SETUP_SHOWN] = False
        controller.on_glyphs()
        await controller.settle()

        assert _glyph_actors(viewer) == []

    asyncio.run(run())


def test_the_ghost_turns_the_camera_to_iso_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("plain", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)

        await controller.load(_glyph_bundle(tmp_path / "tipped"))
        await controller.settle()
        viewer.look(View.TOP)
        app.state[FieldVar.SETUP_SHOWN] = True
        app.state[FieldVar.AUTHORED_GHOST] = True
        controller.on_glyphs()
        await controller.settle()

        camera = viewer.plotter.camera
        offset = np.subtract(camera.position, camera.focal_point)
        assert offset / np.linalg.norm(offset) == pytest.approx(np.array(SIDES[View.ISO][0]) / np.linalg.norm(SIDES[View.ISO][0])), "the ghost turns the view to Iso"
        assert controller.iso_pending is False

        viewer.look(View.TOP)
        app.state[FieldVar.SELECTED_ENTITY] = "boundary:TIP"
        controller.on_glyphs()
        await controller.settle()

        offset = np.subtract(camera.position, camera.focal_point)
        assert offset / np.linalg.norm(offset) == pytest.approx(np.array([0.0, 0.0, 1.0])), "only switching the ghost on turns the camera"

    asyncio.run(run())


def test_a_denser_display_rescales_the_glyph_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> int:
        app = _shell("dense-glyphs", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)

        await controller.load(_glyph_bundle(tmp_path / "tipped"))
        await controller.settle()
        app.state[FieldVar.SETUP_SHOWN] = True
        controller.on_glyphs()
        await controller.settle()

        viewer.on_client_size({"width": 400, "height": 300, "ratio": 2})
        await controller.settle()
        scene = controller.scene
        assert scene is not None and scene.glyph_view is not None

        viewer.switch(ViewMode.SERVER, persist=False)
        await controller.settle()
        streamed = scene.glyph_view
        assert streamed is not None and streamed.scale == viewer.window_scale()

        return round(scene.glyph_view.scale) if scene.glyph_view is not None else 0

    assert asyncio.run(run()) == 2


def test_a_run_without_fields_draws_its_mesh_groups_only_while_the_glyphs_are_shown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("mesh-only", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)

        await controller.load(_glyph_bundle(tmp_path / "meshed", fields=False))

        assert isinstance(controller.groups, GroupScene) and controller.scene is None
        assert viewer.renderable is None, "nothing draws while the Setup tab is off screen"

        app.state[FieldVar.SETUP_SHOWN] = True
        controller.on_glyphs()

        assert viewer.renderable is controller.groups
        assert SceneActor.STYLED.of("LEFT") in viewer.plotter.renderer.actors and "glyph-arrows" in _glyph_actors(viewer)
        assert app.state[viewer.key(SceneVar.NOTICE)] == "", "the mesh draws over the empty view's notice"

        app.state[FieldVar.SELECTED_ENTITY] = "boundary:LEFT__EXT"
        controller.on_glyphs()

        assert "glyph-selection-fill" in _glyph_actors(viewer), "a group without a condition is highlighted from the mesh"

        controller.rescaled()
        app.state[FieldVar.SETUP_SHOWN] = False
        controller.on_glyphs()

        assert viewer.renderable is None and _glyph_actors(viewer) == []
        assert app.state[viewer.key(SceneVar.NOTICE)] == controller.note != ""

        await controller.load(_glyph_bundle(tmp_path / "solved"))
        await controller.settle()

        assert controller.groups is None and controller.scene is not None

    asyncio.run(run())


def test_a_glyph_layer_or_mesh_that_fails_to_load_leaves_the_run_shown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("broken-glyphs", tmp_path, monkeypatch)
        controller = _controller(app)

        def broken(*_: object) -> GlyphLayer:
            raise ValueError("unreadable sidecar")

        monkeypatch.setattr(GlyphLayer, "from_run", broken)
        monkeypatch.setattr(GroupScene, "from_run", broken)

        await controller.load(_glyph_bundle(tmp_path / "solved"))
        await controller.settle()

        assert controller.scene is not None and controller.scene.glyphs is None

        await controller.load(_glyph_bundle(tmp_path / "meshed", fields=False))

        assert controller.groups is None

    asyncio.run(run())


def test_showing_glyphs_without_a_view_or_a_run_draws_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("no-view", tmp_path, monkeypatch)
    controller = _controller(app)
    viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)

    controller.show_glyphs(GlyphView())
    controller.rescaled()
    controller.turn_iso()

    assert viewer.renderable is None and controller.glyph_view == GlyphView()
