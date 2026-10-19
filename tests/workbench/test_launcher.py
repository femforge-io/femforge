"""The launcher: the script runner streams and stops, the cards render from the catalogue, the outputs and log panels beside them, and cards opened and closed."""

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import polars as pl
import psutil
import pytest

from femforge_workbench.registry import DEMO, LAUNCHER, OutputKind, OutputSpec, WorkbenchKind, panel, script
from femforge_workbench.process_tree import CREATION_FLAGS, NEW_SESSION, ProcessTree
from femforge_workbench.runner import ScriptRunner, ScriptRun, ScriptField, ScriptStatus
from tests.workbench.tree import find, prop, texts


pytest.importorskip("trame")
pytest.importorskip("pyvista")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Hook, OpenedKey, StateVar as AppVar, Workbench  # noqa: E402
from femforge_workbench.cli import select  # noqa: E402
from femforge_workbench.demo import DemoPanel, DemoVar, workbench as demo  # noqa: E402
from femforge_workbench.dock import Key, template_name  # noqa: E402
from femforge_workbench.launcher import workbench as launcher  # noqa: E402
from femforge_workbench.panels.launcher import CardField, GroupField, LauncherPanel, LauncherPanelId, StateVar as LauncherVar  # noqa: E402
from femforge_workbench.panels.log import LogPanel, StateVar as LogVar  # noqa: E402
from femforge_workbench.panels.outputs import StateVar as OutputsVar, TabField, Viewer, WorkbenchOutputsPanel, language, serve_roots  # noqa: E402
from femforge_workbench.panels.scene import SceneViewerPanel  # noqa: E402
from femforge_workbench.panels.table import TablePanel  # noqa: E402
from femforge_workbench.persist import LocalJsonStore, StateKey  # noqa: E402


COUNTING = "import sys\nfor i in range(50):\n    print(i)\nsys.exit(3)\n"
SLEEPING_TREE = "import subprocess, sys, time\nchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\nprint(child.pid, flush=True)\ntime.sleep(60)\n"
SPAWNING_TREE = "import subprocess, sys, time\nwhile True:\n    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n    print(child.pid, flush=True)\n    time.sleep(0.05)\n"
LEAVING_TREE = "import subprocess, sys\nchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\nprint(child.pid, flush=True)\n"
TIMEOUT = 60
OPENED_ENTRIES = str(OpenedKey.ENTRIES)
OPENED_NAME = str(OpenedKey.NAME)
OPENED_REFERENCE = str(OpenedKey.REFERENCE)


def _shell(name: str, root: Path, server: str | None = None) -> Workbench:
    return Workbench(
        name,
        title="Launcher",
        server=get_server(server or f"launcher-{name}", client_type="react"),
        persist=LocalJsonStore(root),
        user="tester",
    )


def test_the_runner_streams_lines_in_order_and_records_the_exit_code() -> None:
    lines: list[str] = []
    changes: list[ScriptStatus] = []

    async def run() -> ScriptRun:
        runner = ScriptRunner(lambda _, line: lines.append(line), lambda task: changes.append(task.status))

        return await runner.run("count", [sys.executable, "-c", COUNTING])

    task = asyncio.run(run())

    assert lines == [str(i) for i in range(50)]
    assert task.status is ScriptStatus.FAILED and task.returncode == 3
    assert changes == [ScriptStatus.RUNNING, ScriptStatus.FAILED]
    assert task.row()[ScriptField.STATUS] == str(ScriptStatus.FAILED) and task.row()[ScriptField.PID] == task.pid


def test_stop_kills_the_sleeping_child_tree_and_ends_the_run_as_stopped() -> None:
    async def run() -> tuple[ScriptRun, int, int, bool]:
        got_pid = asyncio.Event()
        pids: list[int] = []

        def on_line(_: str, line: str) -> None:
            pids.append(int(line))
            got_pid.set()

        runner = ScriptRunner(on_line, lambda _: None)
        job = asyncio.ensure_future(runner.run("tree", [sys.executable, "-c", SLEEPING_TREE]))
        await asyncio.wait_for(got_pid.wait(), timeout=TIMEOUT)
        parent = runner.tasks["tree"].pid
        assert runner.running("tree")

        await runner.stop("tree")
        task = await asyncio.wait_for(job, timeout=TIMEOUT)

        return task, parent, pids[0], runner.running("tree")

    task, parent, child, running = asyncio.run(run())

    assert task.status is ScriptStatus.STOPPED and not running
    assert not psutil.pid_exists(child)
    assert not psutil.pid_exists(parent)


def test_stop_kills_a_tree_that_keeps_spawning() -> None:
    async def run() -> tuple[ScriptRun, list[int]]:
        got_pid = asyncio.Event()
        pids: list[int] = []

        def on_line(_: str, line: str) -> None:
            pids.append(int(line))
            got_pid.set()

        runner = ScriptRunner(on_line, lambda _: None)
        job = asyncio.ensure_future(runner.run("spawner", [sys.executable, "-c", SPAWNING_TREE]))
        await asyncio.wait_for(got_pid.wait(), timeout=TIMEOUT)
        await runner.stop("spawner")
        task = await asyncio.wait_for(job, timeout=TIMEOUT)

        return task, pids

    task, pids = asyncio.run(run())

    assert task.status is ScriptStatus.STOPPED and pids
    assert not any(psutil.pid_exists(pid) for pid in pids)


def test_killing_an_exited_tree_signals_nothing() -> None:
    exited = subprocess.Popen([sys.executable, "-c", ""], creationflags=CREATION_FLAGS, start_new_session=NEW_SESSION)
    tree = ProcessTree.adopt(exited.pid)
    _ = exited.wait(timeout=TIMEOUT)

    assert tree.kill() == 0
    tree.close()


def test_a_finished_script_leaves_no_process_behind() -> None:
    async def run() -> tuple[ScriptRun, int]:
        pids: list[int] = []
        runner = ScriptRunner(lambda _, line: pids.append(int(line)), lambda _: None)
        task = await runner.run("leaver", [sys.executable, "-c", LEAVING_TREE])

        return task, pids[0]

    task, child = asyncio.run(run())
    deadline = time.monotonic() + TIMEOUT
    while psutil.pid_exists(child) and time.monotonic() < deadline:
        time.sleep(0.05)

    assert task.status is ScriptStatus.DONE and not psutil.pid_exists(child)


def test_the_outputs_panel_maps_each_kind_and_reports_missing_files(tmp_path: Path) -> None:
    csv = tmp_path / "sweep.csv"
    parquet = tmp_path / "rows.parquet"
    page = tmp_path / "report.html"
    note = tmp_path / "notes.md"
    summary = tmp_path / "summary.json"
    _ = csv.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    pl.DataFrame({"x": [1.0, 2.0, 3.0]}).write_parquet(parquet)
    _ = page.write_text("<p/>", encoding="utf-8")
    _ = note.write_text("# hi", encoding="utf-8")
    _ = summary.write_text("{}", encoding="utf-8")
    outputs = [OutputSpec.at(csv), OutputSpec.at(parquet), OutputSpec.at(page), OutputSpec.at(tmp_path / "plot.png"), OutputSpec.at(note), OutputSpec.at(summary)]
    spec = script("sweep", "Sweep", target="lab.sweep", outputs=outputs)
    app = _shell("outputs", tmp_path)

    panel = WorkbenchOutputsPanel(app, "outputs", "Outputs", catalogue=[spec])
    panel.show(spec)

    tabs = cast("list[dict[str, object]]", app.state[panel.key(OutputsVar.TABS)])
    assert [tab[TabField.KIND] for tab in tabs] == [str(kind) for kind in (OutputKind.TABLE, OutputKind.TABLE, OutputKind.HTML, OutputKind.IMAGE, OutputKind.MARKDOWN, OutputKind.TEXT)]
    assert [tab[TabField.INDEX] for tab in tabs] == [str(index) for index in range(6)]
    assert [tab[TabField.EXISTS] for tab in tabs] == [True, True, True, False, True, True]
    assert serve_roots([spec]) == {"wb-sweep": tmp_path} and app.server.serve["wb-sweep"] == str(tmp_path)
    assert panel.table.frame.to_dicts() == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    assert app.state[panel.key(OutputsVar.VIEWER)] == Viewer.TABLE

    panel.select(1)
    assert panel.table.frame["x"].to_list() == [1.0, 2.0, 3.0]
    panel.select(2)
    assert app.state[panel.key(OutputsVar.URL)] == "/wb-sweep/report.html" and app.state[panel.key(OutputsVar.VIEWER)] == Viewer.FRAME
    panel.select(3)
    assert app.state[panel.key(OutputsVar.VIEWER)] == Viewer.MISSING, "the png was never written"
    panel.select(4)
    assert app.state[panel.key(OutputsVar.TEXT)] == "# hi" and app.state[panel.key(OutputsVar.LANGUAGE)] == "markdown"
    assert app.state[panel.key(OutputsVar.VIEWER)] == Viewer.CODE

    app.state[panel.key(OutputsVar.SELECTED)] = "5"
    panel.on_selected()

    assert app.state[panel.key(OutputsVar.TEXT)] == "{}" and app.state[panel.key(OutputsVar.LANGUAGE)] == language(summary)

    template = cast("dict[str, object]", app.state[f"trame__template_{panel.template_name}"])
    conditions = [prop(node, "value") for node in find(template, "ReactIf")]
    assert conditions == [{"js": f"!tab.{TabField.EXISTS}"}, *({"js": f'{panel.key(OutputsVar.VIEWER)} === "{viewer}"'} for viewer in Viewer)], (
        "every viewer reads the same key: a condition behind a && is never re-evaluated, because the client only subscribes to the keys it read"
    )


def test_the_log_panel_buffers_the_lines_and_replays_them_to_a_client_that_mounts(tmp_path: Path) -> None:
    app = _shell("log", tmp_path)

    panel = LogPanel(app, "log", "Log", scrollback=2)
    panel.write("first")
    panel.write("second")
    panel.write("third")

    assert panel.text() == "second\nthird" and panel.pending == ["first", "second", "third"]

    panel.flush()

    assert panel.pending == [] and app.state[panel.key(LogVar.COUNT)] == 2

    panel.clear()

    assert panel.text() == "" and panel.pending == [] and panel.follow and app.state[panel.key(LogVar.COUNT)] == 0


def test_open_adds_a_split_to_the_built_launcher_and_registers_the_hook(tmp_path: Path) -> None:
    app = _shell("launcher", tmp_path)
    app.add(launcher())
    assert app.panel_ids == [str(panel) for panel in LauncherPanelId]
    assert app.ctrl[Hook.OPEN_WORKBENCH].exists()

    app.open(demo(), reference=str(LauncherPanelId.SCENE))
    app.open(demo())

    assert app.panel_ids == [*(str(panel) for panel in LauncherPanelId), *(str(panel) for panel in DemoPanel)]
    assert all(app.state.has(f"trame__template_{template_name(panel)}") for panel in DemoPanel)
    assert list(app.opened) == [str(DemoPanel.SPHERE)] and app.opened[str(DemoPanel.SPHERE)].reference is None
    assert isinstance(app.panel(LauncherPanelId.SCENE, SceneViewerPanel), SceneViewerPanel)


def test_a_card_opened_from_the_launcher_is_remembered_and_a_fresh_shell_rebuilds_it(tmp_path: Path) -> None:
    first = _shell("launcher", tmp_path)
    first.add(launcher())
    demo_card = first.find_spec(DEMO.name)
    assert demo_card is not None

    first.open_spec(demo_card, reference=str(LauncherPanelId.SCENE))
    first.persist.flush()

    assert first.opened[str(DemoPanel.SPHERE)].name == DEMO.name
    assert first.persist.load(first.scope, StateKey.OPENED) == {OPENED_ENTRIES: [{OPENED_NAME: DEMO.name, OPENED_REFERENCE: str(LauncherPanelId.SCENE)}]}

    second = _shell("launcher", tmp_path, server="launcher-second")
    second.add(launcher())

    assert second.panel_ids == [*(str(panel) for panel in LauncherPanelId), *(str(panel) for panel in DemoPanel)]
    restored = second.opened[str(DemoPanel.SPHERE)]
    assert (restored.name, restored.reference) == (DEMO.name, str(LauncherPanelId.SCENE))


def test_restoring_drops_cards_that_are_gone_or_fail_to_build(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path)
    entries: list[dict[str, str | None]] = [
        {OPENED_NAME: "vanished", OPENED_REFERENCE: None},
        {OPENED_NAME: "broken", OPENED_REFERENCE: str(LauncherPanelId.SCENE)},
        {OPENED_NAME: "sweep", OPENED_REFERENCE: None},
        {OPENED_NAME: DEMO.name, OPENED_REFERENCE: "nowhere"},
    ]
    store.save(("tester", "launcher"), StateKey.OPENED, {OPENED_ENTRIES: entries})
    store.flush()
    app = _shell("launcher", tmp_path, server="launcher-restore")
    app.catalogue.append(panel("broken", "Broken", target="femforge_workbench.demo:missing"))
    app.catalogue.append(script("sweep", "Sweep", target="lab.sweep"))

    app.add(launcher())
    app.persist.flush()

    assert list(app.opened) == [str(DemoPanel.SPHERE)]
    assert app.opened[str(DemoPanel.SPHERE)].reference is None
    assert store.load(app.scope, StateKey.OPENED) == {OPENED_ENTRIES: [{OPENED_NAME: DEMO.name, OPENED_REFERENCE: None}]}


def test_closing_an_opened_card_forgets_it_once_the_layout_has_settled(tmp_path: Path) -> None:
    app = _shell("launcher", tmp_path, server="launcher-close")
    app.add(launcher())
    demo_card = app.find_spec(DEMO.name)
    assert demo_card is not None
    app.open_spec(demo_card, reference=str(LauncherPanelId.SCENE))

    everything: dict[str, object] = {str(panel): {} for panel in (*LauncherPanelId, *DemoPanel)}
    launcher_only: dict[str, object] = {str(panel): {} for panel in LauncherPanelId}

    app.on_layout_changed({str(Key.PANELS): launcher_only})
    assert list(app.opened) == [str(DemoPanel.SPHERE)]

    app.on_layout_changed({str(Key.PANELS): everything})
    app.on_layout_changed({str(Key.PANELS): launcher_only})
    app.persist.flush()

    assert app.opened == {}
    assert app.persist.load(app.scope, StateKey.OPENED) == {OPENED_ENTRIES: []}


def test_closing_a_card_takes_its_panels_header_widgets_and_registrations_out_of_the_shell(tmp_path: Path) -> None:
    app = _shell("launcher", tmp_path, server="launcher-close-card")
    app.add(launcher())
    demo_card = app.find_spec(DEMO.name)
    assert demo_card is not None
    launcher_ids = [str(panel) for panel in LauncherPanelId]
    header_before = len(app.header.root.children)
    series_listeners = app.state._change_callbacks.setdefault(str(DemoVar.SERIES), [])

    app.open_spec(demo_card, reference=str(LauncherPanelId.SCENE))
    table = app.panel(DemoPanel.ROWS, TablePanel)
    layout_trigger = app.ctrl.trigger_name(table.on_layout)

    assert app.state[AppVar.OPENED] == [DEMO.name]
    assert len(app.header.root.children) == header_before + 1
    assert app.ctrl.trigger_fn(layout_trigger) is not None
    assert len(series_listeners) == 1 and table.listeners == [fn for fn, _ in series_listeners]

    app.close(DEMO.name)
    app.persist.flush()

    assert app.panel_ids == launcher_ids
    assert app.opened == {} and app.state[AppVar.OPENED] == []
    assert len(app.header.root.children) == header_before
    assert app.ctrl.trigger_fn(layout_trigger) is None
    assert series_listeners == [] and table.listeners == []
    assert app.persist.load(app.scope, StateKey.OPENED) == {OPENED_ENTRIES: []}
    with pytest.raises(KeyError):
        app.close(DEMO.name)

    app.open_spec(demo_card, reference=str(LauncherPanelId.SCENE))

    assert app.panel_ids == [*launcher_ids, *(str(panel) for panel in DemoPanel)]

    app.close_all()

    assert app.panel_ids == launcher_ids and app.opened == {} and app.state[AppVar.STATUS] == "1 cards closed"
    app.open_spec(demo_card, reference=str(LauncherPanelId.SCENE))

    assert app.panel_ids == [*launcher_ids, *(str(panel) for panel in DemoPanel)]
    assert app.panel(DemoPanel.ROWS, TablePanel) is not table
    assert app.ctrl.trigger_fn(app.ctrl.trigger_name(app.panel(DemoPanel.ROWS, TablePanel).on_layout)) is not None and len(series_listeners) == 1


def test_the_launcher_cards_group_the_catalogue_without_itself_and_render_through_the_loops(tmp_path: Path) -> None:
    app = _shell("launcher", tmp_path)
    app.add(launcher())
    panel = app.panel(LauncherPanelId.LAUNCHER, LauncherPanel)

    groups = panel.groups()
    template = cast("dict[str, object]", app.state[f"trame__template_{panel.template_name}"])

    cards = [card for group in groups for card in cast("list[dict[str, object]]", group[GroupField.CARDS])]
    names = [card[CardField.NAME] for card in cards]
    assert "demo" in names and LAUNCHER.name not in names
    assert all(card[CardField.KIND] == str(WorkbenchKind.PANEL) for card in cards)
    assert app.state[panel.key(LauncherVar.GROUPS)] == groups and app.state[panel.key(LauncherVar.CAPTION)] == "4 workbenches"
    assert [node["props"] for node in find(template, "ReactFor")] == [{"items": {"js": panel.key(LauncherVar.GROUPS)}, "name": "group"}, {"items": {"js": "group.cards"}, "name": "card"}]
    buttons = {texts(node)[0]: node for node in find(template, "ff-button") if texts(node)}
    assert set(buttons) == {"Open", "Close", "Run", "Stop", "Outputs", "Reload"}
    for node in buttons.values():
        assert cast("dict[str, object]", cast("dict[str, object]", prop(node, "onClick"))["callback"])["args"] == {"js": "[card.name]"}

    assert len(find(template, "ff-card")) == 1 and [prop(node, "name") for node in find(template, "ff-icon")] == ["refresh-cw"]
    assert select(None, None) is LAUNCHER
    assert {fn for fn in panel.bound} >= {panel.on_open, panel.on_close, panel.on_run, panel.on_stop, panel.on_outputs, panel.on_reload, panel.push}
