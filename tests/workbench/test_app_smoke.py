"""The shell builds the demo dock on a bare React server: the tree, templates, typed panel access, callbacks, persisted layout scope, and a task-mode start and stop."""

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import cast

import pytest

from femforge_workbench.dock import Panel, Split, layout_json, restorable, template_name
from femforge_workbench.persist import LocalJsonStore, StateKey
from femforge_workbench.theme import hex_color
from femforge_workbench.widgets import module
from tests.workbench.tree import find, prop, texts


pytest.importorskip("trame")
pytest.importorskip("pyvista")

import pyvista as pv  # noqa: E402
from trame.app import get_server  # noqa: E402

from femforge_workbench.app import QUIET_LIBRARIES, StateVar, Template, Workbench  # noqa: E402
from femforge_workbench.demo import ALL_SERIES, ROWS, SERIES_COLUMN, DemoPanel, DemoVar, workbench  # noqa: E402
from femforge_workbench.launcher import workbench as launcher  # noqa: E402
from femforge_workbench.panels.figure import FigurePanel  # noqa: E402
from femforge_workbench.panels.launcher import LauncherPanelId  # noqa: E402
from femforge_workbench.panels.scene import STILL_SCALE, ScenePanel, View, ViewMode  # noqa: E402
from femforge_workbench.panels.table import TablePanel  # noqa: E402
from femforge_workbench.registry import DEMO  # noqa: E402
from femforge_workbench.tasks import Task, TaskBoard, TaskPhase  # noqa: E402


def _shell(name: str, root: Path) -> Workbench:
    return Workbench(name, title="Smoke", server=get_server(f"workbench-{name}", client_type="react"), persist=LocalJsonStore(root), user="tester")


def test_a_shell_gates_the_chatty_libraries_whichever_entry_point_built_it(tmp_path: Path) -> None:
    for name in QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(logging.NOTSET)

    _ = _shell("smoke-quiet", tmp_path)

    assert {name: logging.getLogger(name).level for name in QUIET_LIBRARIES} == dict.fromkeys(QUIET_LIBRARIES, logging.WARNING)
    assert "aiohttp.access" in QUIET_LIBRARIES and "trame_rca" in QUIET_LIBRARIES and "trame_vtklocal" in QUIET_LIBRARIES


def test_the_shell_serves_the_dist_and_enables_every_widget_library_before_a_client_connects(tmp_path: Path) -> None:
    app = _shell("smoke-widgets", tmp_path)
    info = module.version()

    served = "".join(app.server.serve)
    scripts = cast("list[str]", app.server.state["trame__scripts"])

    assert module.ROUTE in app.server.serve and app.server.serve[module.ROUTE] == str(module.SERVE_DIR)
    assert f"{module.ROUTE}/{info['bundle']}?v={info['hash']}" in scripts
    assert "Ff" in cast("list[str]", app.server.state["trame__react_use"])
    assert "trame_rca" in served and "trame_vtklocal" in served
    assert app.server.client_type == "react"


def test_the_tree_mounts_the_theme_first_then_the_header_and_the_dock_hosting_server_templates(tmp_path: Path) -> None:
    app = _shell("smoke-tree", tmp_path)
    root = cast("dict[str, object]", app.ui.html)

    children = cast("list[dict[str, object]]", root["children"])
    dock = find(root, "ff-dock")
    switcher = find(root, "ff-dropdown-menu-item")
    close_all = find(root, "ReactIf")

    assert prop(root, "className") == "flex h-full flex-col"
    assert children[0]["tag"] == "ff-theme" and prop(children[0], "dark") == {"js": str(StateVar.DARK)}
    assert [child["tag"] for child in children[1:]] == ["header", "ff-tour", "main", "ReactIf"]
    assert prop(children[-1], "value") == {"js": f"{StateVar.TASKS}.length"}, "the task strip shows only when a script passed tasks"
    header = cast("list[dict[str, object]]", children[1]["children"])
    assert [child["tag"] for child in header[:2]] == ["div", "div"], "the brand and a divider lead the title"
    assert [prop(node, "src") for node in find(header[0], "img")] == ["/__ff/ff-logo.svg"] and texts(find(header[0], "span")[0]) == ["femforge"]
    assert app.state["trame__favicon"] == "/__ff/favicon.png"
    assert len(dock) == 1 and prop(dock[0], "dark") == {"js": str(StateVar.DARK)} and prop(dock[0], "popoutUrl") == "/__ff/popout.html"
    render = cast("dict[str, object]", prop(dock[0], "renderPanel"))
    slot = cast("dict[str, object]", render["slot"])
    assert slot["params"] == ["panel"]
    assert [prop(node, "templateName") for node in find(slot, "trame-template")] == [{"js": "panel.templateName"}]
    assert [node.get("props") for node in find(root, "ReactFor")] == [{"items": {"js": str(StateVar.WORKBENCHES)}, "name": "card"}, {"items": {"js": str(StateVar.TASKS)}, "name": "task"}]
    assert cast("dict[str, object]", cast("dict[str, object]", prop(switcher[0], "onSelect"))["callback"])["args"] == {"js": "[card.name]"}
    assert prop(close_all[0], "value") == {"js": f"{StateVar.OPENED}.length"} and texts(find(close_all[0], "ff-button")[0]) == ["Close All"]
    assert [prop(node, "templateName") for node in find(root, "trame-template")][0] == str(Template.HEADER)
    assert [prop(node, "name") for node in find(root, "ff-icon")] == ["chevron-down", "layout-grid", "circle-help"]
    assert find(root, "trame-script") == [], "the reload script is served only under the dev flag"


def test_the_dock_callbacks_are_registered_as_triggers_and_the_status_line_flushes(tmp_path: Path) -> None:
    app = _shell("smoke-wiring", tmp_path)
    app.add(workbench())

    app.status("hello")

    for handler in (app.on_dock_ready, app.on_layout_changed, app.on_panel_visibility, app.on_panel_removed, app.close_all, app.reset_layout, app.open_workbench):
        assert app.ctrl.trigger_fn(app.ctrl.trigger_name(handler)) == handler

    assert app.state[StateVar.STATUS] == "hello"
    assert app.palette.accent.lower() == "#16fe8e", "the default palette is the stylesheet's, accent included"
    assert [spec.name for spec in app.catalogue] == ["demo", "field-viewer", "launcher", "monitors", "browsers"]
    assert app.state[StateVar.WORKBENCHES] == [{"name": spec.name, "title": spec.title, "description": spec.description, "group": "Built-in"} for spec in app.catalogue]
    assert app.catalogue[0] == DEMO


def test_a_closed_tab_disposes_its_panel_and_reset_layout_builds_it_again(tmp_path: Path) -> None:
    app = _shell("smoke-closed", tmp_path)
    app.add(workbench())
    closed = str(DemoPanel.ROWS)
    table = app.panel(closed, TablePanel)

    app.on_panel_removed({"id": closed})

    assert closed not in app.panel_ids and table.bound == [], "the closed panel is disposed, not left live"
    assert app.state.has(f"trame__template_{template_name(closed)}"), "its template stays until a rebuild overwrites it"

    app.on_panel_removed({"id": closed})
    app.rebuild_closed()

    assert app.panel_ids == [str(panel) for panel in DemoPanel]
    assert app.panel(closed, TablePanel) is not table


def test_add_registers_a_template_per_panel_and_types_the_components(tmp_path: Path) -> None:
    app = _shell("smoke-add", tmp_path)

    app.add(workbench())

    assert app.panel_ids == [str(panel) for panel in DemoPanel]
    assert all(app.state.has(f"trame__template_{template_name(panel)}") for panel in DemoPanel)
    assert isinstance(app.panel(DemoPanel.SPHERE, ScenePanel), ScenePanel)
    assert isinstance(app.panel(DemoPanel.SIGNAL, FigurePanel), FigurePanel)
    assert isinstance(app.panel(DemoPanel.ROWS, TablePanel), TablePanel)
    with pytest.raises(KeyError):
        _ = app.panel(DemoPanel.SPHERE, TablePanel)

    sphere = cast("dict[str, object]", app.state[f"trame__template_{template_name(str(DemoPanel.SPHERE))}"])
    assert prop(sphere, "style") == {"height": "100%", "width": "100%", "display": "flex", "flexDirection": "column", "minHeight": "0"}
    first = cast("list[dict[str, object]]", sphere["children"])[0]
    assert first["tag"] == "ff-theme" and prop(first, "dark") == {"js": str(StateVar.DARK)}, "a standalone ?ui= page paints the scheme too"
    assert len(find(sphere, "vtk-local")) == 1, "the demo sphere mirrors in the browser by default"
    assert [texts(node) for node in find(sphere, "ff-button")][:3] == [["Fit"], ["Top"], ["Bottom"]]


def test_a_mirrored_scene_fits_the_canvas_the_browser_reports_and_pushes_every_preset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("smoke-refit", tmp_path)
    app.add(workbench())
    panel = app.panel(DemoPanel.SPHERE, ScenePanel)
    calls: list[str | None] = []

    def record(ref: str | None = None, method: str | None = None, *args: object) -> None:
        calls.append(method)

    monkeypatch.setattr(app.server, "js_call", record)
    panel.on_client_size({"width": 300, "height": 600, "ratio": 1})

    assert calls == ["update"] and tuple(panel.window().GetSize()) == (300, 600), "the first canvas sizes the window and fits the camera to it, server-side"

    calls.clear()
    panel.look(View.ISO)

    assert calls == ["update"], "a preset fits against that window and pushes the camera; the mirror never resets its own"
    assert panel.plotter.camera.up == pytest.approx((0.0, 0.0, 1.0))


def test_the_mirrored_sphere_shows_the_triad_and_a_streamed_scene_the_marker_with_palette_labels(tmp_path: Path) -> None:
    app = _shell("smoke-axes", tmp_path)
    app.add(workbench())
    mirrored = app.panel(DemoPanel.SPHERE, ScenePanel)
    streamed = ScenePanel(app, "marker", "Marker", mode=ViewMode.SERVER)

    mirrored.toggle_axes()
    streamed.toggle_axes()

    assert mirrored.triad.actor.GetVisibility() and not mirrored.plotter.renderer.axes_enabled
    assert len(find(cast("dict[str, object]", app.state[f"trame__template_{mirrored.template_name}"]), "ff-scene-sync")) == 1
    actor = streamed.plotter.renderer.axes_actor
    assert actor is not None and not streamed.triad.actor.GetVisibility()
    label = cast("tuple[float, float, float]", actor.GetXAxisCaptionActor2D().GetCaptionTextProperty().GetColor())  # pyright: ignore[reportUnknownMemberType]  # vtk caption actors ship no types
    assert label == pytest.approx(pv.Color(hex_color(app.palette.text)).float_rgb)
    assert streamed.plotter.renderer.axes_enabled

    streamed.toggle_axes()
    assert not streamed.plotter.renderer.axes_enabled


def test_a_server_streamed_scene_scales_its_fonts_by_the_still_scale_without_touching_the_global_theme(tmp_path: Path) -> None:
    default = pv.global_theme.font
    before = (default.size, default.title_size, default.label_size)
    app = _shell("smoke-fonts", tmp_path)

    streamed = ScenePanel(app, "streamed", "Streamed", mode=ViewMode.SERVER)
    mirrored = ScenePanel(app, "mirrored", "Mirrored")

    font = streamed.plotter.theme.font
    assert (font.size, font.title_size, font.label_size) == tuple(size * STILL_SCALE for size in before)
    assert (mirrored.plotter.theme.font.size, mirrored.plotter.theme.font.label_size) == (before[0], before[2])
    assert (default.size, default.title_size, default.label_size) == before
    assert streamed.handler is not None and streamed.view is None
    assert mirrored.view is not None and mirrored.handler is None
    assert len(find(cast("dict[str, object]", app.state[f"trame__template_{streamed.template_name}"]), "remote-controlled-area")) == 1


def test_layout_changes_persist_under_the_user_and_workbench_scope(tmp_path: Path) -> None:
    app = _shell("smoke-persist", tmp_path)
    app.add(workbench())
    root = app.root
    assert root is not None

    app.on_layout_changed(layout_json(root))
    app.persist.flush()

    assert app.scope == ("tester", "smoke-persist")
    assert (tmp_path / "tester" / "smoke-persist" / "layout.json").exists()
    assert restorable(app.persist.load(app.scope, StateKey.LAYOUT), app.panel_ids) == layout_json(root)


def test_repeated_panel_ids_are_rejected_before_anything_is_built(tmp_path: Path) -> None:
    app = _shell("smoke-ids", tmp_path)

    def build(app: Workbench, id: str, title: str) -> FigurePanel:
        return FigurePanel(app, id, title)

    with pytest.raises(ValueError, match="twice"):
        app.add(Split([Panel("A", build, id="twice"), Panel("B", build, id="twice")]))

    assert app.panels == []


def test_the_server_starts_and_stops_as_a_task(tmp_path: Path) -> None:
    async def run() -> None:
        app = _shell("smoke-run", tmp_path)
        app.add(workbench())

        started = app.server.start(exec_mode="task", port=0, open_browser=False, show_connection_info=False, disable_logging=True, timeout=0)
        assert started is not None
        _ = await app.server.ready
        running = app.server.running and app.loop is asyncio.get_running_loop()
        await app.server.stop()
        await started

        assert running
        assert not app.server.running

    asyncio.run(run())


def test_run_installs_the_break_handler_that_the_supervisor_relies_on(tmp_path: Path) -> None:
    app = _shell("smoke-handler", tmp_path)
    previous = signal.getsignal(signal.SIGTERM)

    app.install_break_handler()
    try:
        assert signal.getsignal(signal.SIGTERM) == app.on_break
        if sys.platform == "win32":
            assert signal.getsignal(signal.SIGBREAK) == app.on_break
    finally:
        _ = signal.signal(signal.SIGTERM, previous)
        if sys.platform == "win32":
            _ = signal.signal(signal.SIGBREAK, signal.SIG_DFL)


def test_a_break_stops_the_server_cleanly_so_the_exit_hooks_flush_the_store(tmp_path: Path) -> None:
    document = tmp_path / "tester" / "smoke-break" / "opened.json"

    async def run() -> tuple[bool, bool, bool]:
        app = Workbench(
            "smoke-break",
            title="Smoke",
            server=get_server("workbench-smoke-break", client_type="react"),
            persist=LocalJsonStore(tmp_path, debounce=60.0),
            user="tester",
        )
        app.add(workbench())
        started = app.server.start(exec_mode="task", port=0, open_browser=False, show_connection_info=False, disable_logging=True, timeout=0)
        assert started is not None
        _ = await app.server.ready
        late: dict[str, object] = {"entries": [{"name": "late", "reference": None}]}
        app.persist.save(app.scope, StateKey.OPENED, late)
        pending = not document.exists()

        app.on_break(signal.SIGTERM, None)
        await started
        await asyncio.sleep(0)

        return pending, app.server.running, document.exists() and "late" in document.read_text(encoding="utf-8")

    assert asyncio.run(run()) == (True, False, True)


def test_cards_open_and_close_through_the_running_shell(tmp_path: Path) -> None:
    async def run() -> None:
        app = _shell("smoke-cards", tmp_path)
        app.add(launcher())
        demo_card = app.find_spec(DEMO.name)
        assert demo_card is not None
        launcher_ids = [str(panel) for panel in LauncherPanelId]

        started = app.server.start(exec_mode="task", port=0, open_browser=False, show_connection_info=False, disable_logging=True, timeout=0)
        assert started is not None
        _ = await app.server.ready

        app.open_workbench(DEMO.name)
        opened_ids = list(app.panel_ids)
        opened_state = list(cast("list[str]", app.state[StateVar.OPENED]))

        app.close(DEMO.name)
        closed_ids = list(app.panel_ids)

        app.open_spec(demo_card, reference=str(LauncherPanelId.SCENE))
        app.close_all()
        status = str(app.state[StateVar.STATUS])
        remaining = list(app.panel_ids)

        await app.server.stop()
        await started

        assert opened_ids == [*launcher_ids, *(str(panel) for panel in DemoPanel)] and opened_state == [DEMO.name]
        assert closed_ids == launcher_ids
        assert remaining == launcher_ids and status == "1 cards closed" and app.state[StateVar.OPENED] == []

    asyncio.run(run())


def test_a_workbench_can_put_controls_in_the_header_bound_to_the_state(tmp_path: Path) -> None:
    async def run() -> list[str]:
        app = _shell("smoke-header", tmp_path)
        app.add(workbench())
        table = app.panel(DemoPanel.ROWS, TablePanel)
        header = cast("dict[str, object]", app.state[f"trame__template_{Template.HEADER}"])
        select = find(header, "ff-select")
        assert len(select) == 1 and prop(select[0], "value") == {"js": str(DemoVar.SERIES)}
        assert prop(select[0], "onValueChange") == {"callback": {"js": f'trame.state.set("{DemoVar.SERIES}", $event)'}}
        assert [texts(node) for node in find(header, "ff-field-label")] == [["Series"]]
        assert [prop(node, "value") for node in find(header, "ff-select-item")][:2] == ["all", "alpha"]
        assert app.state[DemoVar.SERIES] == ALL_SERIES
        assert table.frame.height == ROWS

        started = app.server.start(exec_mode="task", port=0, open_browser=False, show_connection_info=False, disable_logging=True, timeout=0)
        assert started is not None
        _ = await app.server.ready

        with app.state:
            app.state[DemoVar.SERIES] = "beta"

        shown = table.frame[SERIES_COLUMN].unique().to_list()
        await app.server.stop()
        await started

        return shown

    assert asyncio.run(run()) == ["beta"]


def test_the_task_strip_shows_the_board_and_its_buttons_act_on_it(tmp_path: Path) -> None:
    app = _shell("smoke-tasks", tmp_path)
    app.board = TaskBoard(app, "main:tasks", [Task("Solve 3D", sys.gettrace), Task("Planar 2D", sys.gettrace)], path=None, roots=[])
    root = cast("dict[str, object]", app.ui.html)

    toggles = [node for node in find(root, "button") if prop(node, "aria-controls") == "task-sheet"]
    summary = [node for node in find(root, "span") if prop(node, "role") == "status"]
    assert len(toggles) == 1 and prop(toggles[0], "aria-expanded") == {"js": str(StateVar.TASK_SHEET)}
    assert len(summary) == 1 and prop(summary[0], "aria-live") == "polite"
    assert app.state[StateVar.TASK_SUMMARY] == "0 of 2 Done"
    assert cast("dict[str, object]", app.state[StateVar.TASK_FOCUS])["label"] == "Solve 3D"

    app.toggle_tasks()
    assert app.state[StateVar.TASK_SHEET] is True and app.persist.load(app.scope, StateKey.TASK_SHEET) == {"open": True}
    app.on_task_key("Escape")
    assert app.state[StateVar.TASK_SHEET] is False

    app.cancel_task("Planar 2D")
    armed = app.state[StateVar.TASK_CONFIRM]
    app.cancel_task("Planar 2D")
    assert armed == "Planar 2D" and app.state[StateVar.TASK_CONFIRM] == "", "the second press cancels"
    assert app.board.rows["Planar 2D"].phase is TaskPhase.STOPPED

    app.cancel_task("Solve 3D")
    app.disarm_cancel("Solve 3D")
    assert app.state[StateVar.TASK_CONFIRM] == "" and app.board.rows["Solve 3D"].phase is TaskPhase.QUEUED

    app.retry_task("Planar 2D")
    assert app.board.queue == ["Solve 3D", "Planar 2D"]

    app.toggle_task_details("Planar 2D")
    assert app.state[StateVar.TASK_DETAILS] == "Planar 2D"
    app.toggle_task_details("Planar 2D")
    assert app.state[StateVar.TASK_DETAILS] == ""

    app.open_task_run("r1")
    assert app.state[StateVar.STATUS] == "no field viewer to open the run in"
