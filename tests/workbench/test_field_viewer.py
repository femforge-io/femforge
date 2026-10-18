"""The field viewer: the built-in spec, the controller's drawing path by model dimension, the controls the shell builds and the state they bind."""

import asyncio
import time
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from femforge.common.files import BundleFile
from femforge.common.log import TELEMETRY_ENV
from femforge.config import RunMeta, SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.run.bundle import write_manifest
from femforge.run.entities import Bodies, Body, BodyRecord, EntitiesFile
from femforge.run.inspect import Run
from femforge.run.manifest import RunManifest
from femforge.telemetry.paths import Telemetry
from femforge.viz.field_display import BodyPaint, BodyStyle, DisplayMode, DisplaySpec, SceneActor
from femforge.viz.field_lines import FieldLineOptions, SeedPlacement, StreamlineActor
from femforge.viz.symmetry import Expansion
from femforge_workbench.dock import Split, panels, template_name
from femforge_workbench.persist import LocalJsonStore
from femforge_workbench.registry import FIELD_VIEWER, resolve
from tests.workbench.tree import Node, find, prop, props, texts


pytest.importorskip("trame")
pytest.importorskip("pyvista")

import pyvista as pv  # noqa: E402
from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Hook, StateVar, Template, Workbench  # noqa: E402
from femforge_workbench.colormaps import COLORMAPS, FAVORITES  # noqa: E402
from femforge_workbench.panels import Shared  # noqa: E402
from femforge_workbench.panels import field_viewer as field_viewer_module  # noqa: E402
from femforge_workbench.panels.scene import StateVar as SceneVar  # noqa: E402
from femforge_workbench.panels.field_viewer import (  # noqa: E402
    FieldControlsPanel,
    FieldPanel,
    FieldVar,
    FieldViewerController,
    FieldViewerPanel,
    OPEN_SECTIONS,
    RowKey,
    body_rows,
    SAME_AS_FIELD,
    colormap_gradient,
    colormap_items,
    names_of,
    number_of,
    pair_of,
    run_items,
    bundle_label,
    seed_count,
    similar_bounds,
    workbench,
)


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


def test_the_builtin_spec_resolves_to_a_split_with_the_two_panel_ids(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert switches == [str(var) for var in (FieldVar.INVERT, FieldVar.LOG_SCALE, FieldVar.OUTLINE, FieldVar.AUTO_CLIM, FieldVar.OPACITY_MAPPING, FieldVar.STREAMLINES)]
    assert [_bound(node, "value") for node in sliders] == [
        str(FieldVar.SWEEP),
        str(FieldVar.CLIM),
        str(FieldVar.OPACITY_RANGE),
        str(FieldVar.LINE_COUNT),
        str(FieldVar.MAX_LENGTH),
        str(FieldVar.OPACITY),
        f"item.{RowKey.OPACITY}",
    ]
    assert all(prop(node, "onValueCommit") is not None for node in sliders)
    assert _bound(sliders[1], "min") == str(FieldVar.CLIM_MIN) and prop(sliders[1], "disabled") is None, "a moved thumb pins the range instead"
    assert str(FieldVar.FIELD_GRADIENT) in str(prop(sliders[1], "style")), "the range track wears the colormap"
    assert [_bound(node, "value") for node in find(template, "ff-toggle-group")] == [str(FieldVar.EXPANSION), str(FieldVar.LEGEND)]
    assert [texts(node) for node in find(template, "ff-toggle-group-item")] == [["Section"], ["Mirrored"], ["Revolved"], ["Hidden"], ["Vertical"], ["Horizontal"]]
    assert [prop(node, "aria-label") for node in find(template, "ff-toggle-group")] == ["Show as", "Legend"]
    assert len(rows) == 1 and find(template, "ff-tree") == []
    assert [texts(node)[0] for node in find(template, "ff-accordion-trigger")] == ["Symmetry", "Field", "Field Lines", "Bodies"]
    assert [prop(node, "id") for node in find(template, "ff-accordion-item")] == ["tour-symmetry", "tour-field", "tour-lines", "tour-bodies"]
    assert [prop(node, "htmlFor") for node in find(template, "ff-field-label") if prop(node, "htmlFor")] == [prop(node, "id") for node in find(template, "ff-switch")]
    assert _bound(find(template, "ff-progress")[0], "value") == 100.0


def test_the_run_picker_and_the_bundle_path_field_sit_in_the_app_bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("header", tmp_path, monkeypatch)
    header = cast("Node", app.state[f"trame__template_{Template.HEADER}"])

    picker = find(header, "ff-select")[0]
    path = find(header, "ff-input")[0]

    assert _bound(picker, "value") == str(FieldVar.RUN)
    assert _bound(find(header, "ReactFor")[0], "items") == str(FieldVar.RUN_ITEMS)
    assert _bound(path, "value") == str(FieldVar.RUN_TEXT) and prop(path, "readOnly") is True, "the path follows the open run and is not typed into"
    assert find(header, "ff-tree") == []


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
        assert app.state[FieldVar.VISIBLE_BODIES] == ["LEFT", "RIGHT"] and app.state[FieldVar.HAS_FLUX] is True
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

    assert asyncio.run(run()) == ["magnetic field strength", None, "magnetic flux density"]


def test_a_run_without_a_flux_array_draws_with_the_field_line_switch_left_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("no-flux", tmp_path, monkeypatch)
        controller = _controller(app)
        grid = _hex_grid()
        del grid.point_data["magnetic flux density"]
        app.state[FieldVar.STREAMLINES] = True

        await controller.load(_bundle(tmp_path / "plain", grid))
        await controller.settle()

        assert app.state[FieldVar.HAS_FLUX] is False and app.state[FieldVar.STREAMLINES] is True
        assert controller.scene is not None and controller.scene.spec.streamlines is None
        assert str(app.state[StateVar.STATUS]) == "plain: 1 fields, 2 bodies, 3D"

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

        notice = "pending: No fields yet: the run is still solving"
        assert controller.scene is None and app.state[FieldVar.FIELDS] == [] and app.state[FieldVar.BODY_ROWS] == []
        assert str(SceneActor.BODIES) not in viewer.plotter.renderer.actors, "nothing of the last run is left to read as this one"
        assert app.state[viewer.key(SceneVar.NOTICE)] == notice and app.state[StateVar.STATUS] == notice

        _ = held.rename(pending / "case_t0001.vtu")
        solved = True
        waiting = controller.wait_task
        assert waiting is not None
        await waiting
        await controller.settle()

        assert controller.scene is not None and controller.scene.run_id == "pending"
        assert app.state[viewer.key(SceneVar.NOTICE)] == ""

    asyncio.run(run())


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
        assert app.state[viewer.key(SceneVar.NOTICE)] == "No Run Open"

        await controller.load(tmp_path / "missing")

        assert controller.wait_task is None and app.state[FieldVar.BUSY] is False
        assert str(app.state[viewer.key(SceneVar.NOTICE)]).startswith("FileNotFoundError")

        fieldless = tmp_path / "fieldless"
        manifest = RunManifest.build(SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step")), status="ok", run_id="fieldless")
        _ = write_manifest(fieldless, manifest)
        await controller.load(fieldless)

        assert controller.wait_task is None
        assert app.state[viewer.key(SceneVar.NOTICE)] == "fieldless: The solve wrote no fields"

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

    assert prop(accordion, "defaultValue") == OPEN_SECTIONS == ["symmetry", "field", "lines", "bodies"]
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




def test_the_last_panel_to_leave_the_dock_releases_the_controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def listening() -> bool:
        return any(fn == controller.on_field for fn, _ in app.state._change_callbacks.get(str(FieldVar.FIELD), []))

    app = _shell("dispose", tmp_path, monkeypatch)
    controller = _controller(app)
    controls = app.panel(FieldPanel.CONTROLS, FieldControlsPanel)

    assert app.ctrl[Hook.FIELD_OPEN].exists() and listening()
    assert controller.attached == {str(panel_id) for panel_id in FieldPanel}

    controls.dispose()

    assert controller.live and listening(), "the viewer is still on screen"
    assert app.ctrl[Hook.FIELD_OPEN].exists()

    app.panel(FieldPanel.VIEW_3D, FieldViewerPanel).dispose()

    assert not app.ctrl[Hook.FIELD_OPEN].exists() and not listening()
    assert controller.viewer is None and controls.bound == [] and not controller.live




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
        app.state[FieldVar.RUN_ITEMS] = [{"title": "indexed", "value": "indexed"}]

        controller.open(bundle)
        await controller.settle()

        items = cast("list[dict[str, object]]", app.state[FieldVar.RUN_ITEMS])
        assert items == [{"title": "hex", "value": str(bundle)}, {"title": "indexed", "value": "indexed"}]

        controller.open(bundle)
        await controller.settle()

        assert cast("list[object]", app.state[FieldVar.RUN_ITEMS]) == items, "the same run is offered once"

        app.state[FieldVar.RUN_ITEMS] = [{"title": "4 mm gap - hex", "value": "hex"}]
        controller.open(bundle)
        await controller.settle()

        assert app.state[FieldVar.RUN] == "hex" and len(cast("list[object]", app.state[FieldVar.RUN_ITEMS])) == 1, "a bundle the index lists is picked as its indexed run"

        app.state[FieldVar.RUN_ITEMS] = [{"title": "hex", "value": str(bundle)}, {"title": "old", "value": "old"}]
        by_id = controller.offered("hex", Run(bundle), "hex")

        assert by_id == (str(bundle), app.state[FieldVar.RUN_ITEMS]), "a bundle added by path is picked again when opened by id"

        def indexed(_telemetry: Telemetry | None) -> list[dict[str, object]]:
            return [{"title": "4 mm gap - hex", "value": "hex"}, {"title": "new", "value": "new"}]

        monkeypatch.setattr(field_viewer_module, "run_items", indexed)
        controller.telemetry = Telemetry(tmp_path)
        refreshed = controller.offered(str(bundle), Run(bundle), "hex")

        assert refreshed == ("hex", [{"title": "old", "value": "old"}, {"title": "4 mm gap - hex", "value": "hex"}, {"title": "new", "value": "new"}]), "the index is read again and an added bundle it now lists is dropped"

    asyncio.run(run())


def test_a_bundle_is_labelled_from_its_manifest(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "hex", _hex_grid())
    plain = bundle_label(Run(bundle), "hex")
    _ = write_manifest(bundle, RunManifest.build(SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"), meta=RunMeta(description="4 mm gap")), status="ok", run_id="hex"))

    assert plain == "hex" and bundle_label(Run(bundle), "hex") == "4 mm gap · hex"


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


def test_run_items_are_empty_without_a_telemetry_root() -> None:
    assert run_items(None) == []


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
    assert str(FieldVar.OPACITY_MAPPING) in str(conditions)
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
