"""Serving a workbench: a bare Split, a factory rebuilt on save, a card, the launcher, the window it opens in, and the command's hand-over."""

import asyncio
import sys
from pathlib import Path
from typing import cast

import pytest

from femforge_workbench.dock import Split
from femforge_workbench.persist import LocalJsonStore
from femforge_workbench.registry import DEMO, LAUNCHER, WorkbenchSpec, discover, panel, script
from tests.workbench.tree import find, prop


pytest.importorskip("trame")
pytest.importorskip("pyvista")

from trame.app import get_server  # noqa: E402

from femforge.viz.scene import Renderable  # noqa: E402
from femforge_workbench import app as app_module, cli, serve  # noqa: E402
from femforge_workbench.app import StateVar, Window, Workbench  # noqa: E402
from femforge_workbench.demo import Sphere, workbench as demo  # noqa: E402
from femforge_workbench.panels.scene import SceneViewerPanel  # noqa: E402
from femforge_workbench.serve import VIEWER_PANEL, NotAWorkbench, build, card, show  # noqa: E402
from femforge_workbench.tasks import Task, TaskBoard  # noqa: E402
from femforge_workbench.theme import Palette  # noqa: E402


type Served = list[tuple[Workbench, int, Window]]

NOT_CALLABLE = 1


def bearing_seat() -> Split:
    return demo()


def sphere() -> Renderable:
    return Sphere(Palette.load())


def not_a_dock() -> int:
    return 1


def solves() -> list[Task]:
    return [Task("Solve", not_a_dock)]


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Served:
    """The shells show() hands to run(), captured instead of served; the launcher discovers the built-in cards only."""
    runs: Served = []

    def run(self: Workbench, *, port: int = 8080, window: Window = Window.BROWSER) -> None:
        runs.append((self, port, window))

    monkeypatch.setattr(Workbench, "run", run)
    monkeypatch.setattr(serve, "discover", lambda: discover([]))

    return runs


def _shell(name: str, tmp_path: Path) -> Workbench:
    return Workbench(name, server=get_server(f"serve-{name}", client_type="react"), persist=LocalJsonStore(tmp_path), user="tester")


def test_a_split_is_served_as_built_under_its_title_with_a_plain_title_for_a_header(served: Served, tmp_path: Path) -> None:
    show(demo(), title="Bare demo", port=8123, persist=tmp_path)

    [(app, port, window)] = served
    root = cast("dict[str, object]", app.ui.html)
    titles = [node for node in find(root, "span") if prop(node, "className") == "gap-1 font-semibold"]

    assert app.name == "bare-demo" and app.title == "Bare demo" and port == 8123 and window is Window.APP
    assert app.catalogue == [] and app.watches == [], "a Split carries no target to import again on save"
    assert find(root, "ff-dropdown-menu") == [] and len(titles) == 1


def test_a_factory_is_imported_by_its_target_and_rebuilt_on_save(served: Served, tmp_path: Path) -> None:
    show(bearing_seat, window=Window.BROWSER, persist=tmp_path)

    [(app, _, window)] = served

    assert app.name == "bearing_seat" and app.title == "bearing seat" and window is Window.BROWSER
    assert [(watch.name, watch.reloader.target) for watch in app.watches] == [("bearing_seat", "tests.workbench.test_serve:bearing_seat")]
    assert app.catalogue == []


def test_the_launcher_lists_the_discovered_cards_and_the_registered_ones(served: Served, tmp_path: Path) -> None:
    flux = panel("flux", "Flux", target=bearing_seat)

    show(workbenches=[flux], persist=tmp_path)

    [(app, _, _)] = served

    cards = cast("list[dict[str, object]]", app.state[StateVar.WORKBENCHES])

    assert app.name == LAUNCHER.name and DEMO in app.catalogue and app.catalogue[-1] == flux
    assert find(app.ui.html, "ff-dropdown-menu") != [] and cards[-1]["name"] == "flux"


def test_a_single_card_lists_only_the_cards_it_is_given_under_the_title_asked_for(served: Served, tmp_path: Path) -> None:
    flux = panel("flux", "Flux", target=bearing_seat)

    show(DEMO, title="Demo and flux", workbenches=[flux], persist=tmp_path)

    [(app, _, _)] = served

    assert app.name == DEMO.name and app.title == "Demo and flux" and app.catalogue == [flux]


def test_a_script_card_runs_in_a_subprocess_instead_of_a_shell(served: Served, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> None:
        calls.append((command, kwargs))

    monkeypatch.setattr(serve.subprocess, "run", run)

    show(script("sweep", "Sweep", target="lab.sweep", cwd=Path("/lab")))

    assert served == [] and calls == [([sys.executable, "-m", "lab.sweep"], {"cwd": Path("/lab"), "check": False})]


def test_card_opens_the_launcher_for_nothing_and_names_a_factory_after_itself() -> None:
    spec = card(bearing_seat)

    assert card(None) is LAUNCHER and card(DEMO) is DEMO
    assert (spec.name, spec.title, spec.target) == ("bearing_seat", "bearing seat", "tests.workbench.test_serve:bearing_seat")


def test_a_factory_returning_a_renderable_opens_in_one_scene_panel(tmp_path: Path) -> None:
    app = build(card(sphere), catalogue=[], persist=LocalJsonStore(tmp_path))

    assert [panel.id for panel in app.panels] == [VIEWER_PANEL] and isinstance(app.panels[0], SceneViewerPanel)


def test_a_target_that_builds_no_workbench_is_named(tmp_path: Path) -> None:
    constant = panel("constant", "Constant", target="tests.workbench.test_serve:NOT_CALLABLE")

    with pytest.raises(NotAWorkbench, match="is not callable"):
        _ = build(constant, catalogue=[], persist=LocalJsonStore(tmp_path))

    with pytest.raises(NotAWorkbench, match="returned int; a Split or a Renderable is needed"):
        _ = build(card(not_a_dock), catalogue=[], persist=LocalJsonStore(tmp_path))  # pyright: ignore[reportArgumentType]  # the wrong return type is the point


def test_the_command_hands_its_flags_to_show(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[WorkbenchSpec, dict[str, object]]] = []

    def show(spec: WorkbenchSpec, **kwargs: object) -> None:
        calls.append((spec, kwargs))

    monkeypatch.setattr(cli, "show", show)
    monkeypatch.setattr(cli, "discover", lambda: discover([]))

    cli.main(["demo", "--app", "--port", "9001", "--telemetry", str(tmp_path), "--persist", str(tmp_path), "--watch", "lab"])
    cli.main(["--webview"])
    cli.main(["-m", "tests.workbench.test_serve:bearing_seat"])

    assert calls[0] == (DEMO, {"window": Window.APP, "port": 9001, "telemetry": str(tmp_path), "persist": str(tmp_path), "watch": ["lab"]})
    assert calls[1][0] is LAUNCHER and calls[1][1]["window"] is Window.WEBVIEW
    assert calls[2][0].target == "tests.workbench.test_serve:bearing_seat" and calls[2][1]["window"] is Window.BROWSER


def test_the_command_exits_2_naming_a_target_that_builds_no_workbench(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["-m", "tests.workbench.test_serve:NOT_CALLABLE", "--persist", str(tmp_path)])

    assert exited.value.code == 2 and "NOT_CALLABLE is not callable" in capsys.readouterr().err


def test_run_opens_the_window_asked_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("window", tmp_path)
    starts: list[dict[str, object]] = []

    def start(**kwargs: object) -> None:
        starts.append(kwargs)

    monkeypatch.setattr(app.server, "start", start)
    monkeypatch.setattr(app, "install_break_handler", lambda: None)

    for window in Window:
        app.run(port=8124, window=window)

    assert [(start["exec_mode"], start["open_browser"]) for start in starts] == [("main", True), ("main", False), ("desktop", False)]
    assert app.window is Window.WEBVIEW


def test_an_app_window_without_chrome_or_edge_opens_the_system_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("no-chrome", tmp_path)
    opened: list[str] = []

    def open_url(url: str) -> None:
        opened.append(url)

    monkeypatch.setattr(app_module, "BROWSERS", ())
    monkeypatch.setattr(app_module.webbrowser, "open", open_url)

    app.open_app_window()

    assert opened == [f"http://localhost:{app.server.port}/"]


def test_when_ready_waits_for_the_server_loop_then_calls_at_once(tmp_path: Path) -> None:
    async def run() -> tuple[list[str], list[str]]:
        app = _shell("ready", tmp_path)
        calls: list[str] = []
        app.when_ready(lambda: calls.append("queued"))
        before = list(calls)

        started = app.server.start(exec_mode="task", port=0, open_browser=False, show_connection_info=False, disable_logging=True, timeout=0)
        _ = await app.server.ready
        app.when_ready(lambda: calls.append("now"))
        await app.server.stop()
        await cast("asyncio.Future[object]", started)

        return before, calls

    before, calls = asyncio.run(run())

    assert before == [] and calls == ["queued", "now"]


def test_tasks_attach_a_board_on_the_factory_target_that_stops_with_the_server(served: Served, tmp_path: Path) -> None:
    show(bearing_seat, tasks=solves, persist=tmp_path)
    show(demo(), tasks=solves, persist=tmp_path)

    [(card_app, _, _), (split_app, _, _)] = served
    boards = [app.board for app in (card_app, split_app)]
    assert all(board is not None and board.factory == f"{__name__}:solves" and board.closed for board in boards)
    assert [row["label"] for row in cast("list[dict[str, object]]", card_app.state[StateVar.TASKS])] == ["Solve"]


def test_tasks_must_come_from_a_module_level_function_and_never_go_to_a_script_card(served: Served, tmp_path: Path) -> None:
    def nested() -> list[Task]:
        return []

    with pytest.raises(ValueError, match="module attribute"):
        show(bearing_seat, tasks=nested, persist=tmp_path)

    with pytest.raises(ValueError, match="takes no tasks"):
        show(script("sweep", "Sweep", target="lab.sweep"), tasks=solves)

    assert served == []


def test_the_board_stops_when_the_server_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stopped: list[TaskBoard] = []

    def run(self: Workbench, *, port: int = 8080, window: Window = Window.BROWSER) -> None:
        raise OSError("port taken")

    def shutdown(self: TaskBoard, grace: float = 10.0) -> None:
        stopped.append(self)

    monkeypatch.setattr(Workbench, "run", run)
    monkeypatch.setattr(TaskBoard, "shutdown", shutdown)

    with pytest.raises(OSError, match="port taken"):
        show(bearing_seat, tasks=solves, persist=tmp_path)

    assert len(stopped) == 1
