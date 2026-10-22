"""The shell's guards and fallbacks: a broken catalogue, refused cards, failed builds, a missing dock, the Cancel countdown, the title menu and the app window."""

import asyncio
import signal
import sys
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

import pytest

from femforge_workbench.persist import LocalJsonStore
from tests.workbench.tree import find


pytest.importorskip("trame")
pytest.importorskip("pyvista")

from trame.app import get_server  # noqa: E402
from trame.widgets import html  # noqa: E402

from femforge_workbench import app as app_module  # noqa: E402
from femforge_workbench.app import APP_PROFILE, APP_WINDOW_SIZE, CONFIRM_S, Hook, StateVar, Window, Workbench  # noqa: E402
from femforge_workbench.demo import workbench as demo  # noqa: E402
from femforge_workbench.dock import Panel, Split  # noqa: E402
from femforge_workbench.panels.figure import FigurePanel  # noqa: E402
from femforge_workbench.registry import DEMO, LAUNCHER, WorkbenchKind  # noqa: E402
from femforge_workbench.tasks import Task, TaskBoard  # noqa: E402


JOIN_S = 10.0
NOT_CALLABLE = "a card target that is no function"


def not_a_split() -> str:
    return "not a split"


def _shell(name: str, root: Path) -> Workbench:
    return Workbench(name, title="Shell", server=get_server(f"shell-{name}", client_type="react"), persist=LocalJsonStore(root), user="tester")


def _figure(app: Workbench, id: str, title: str) -> FigurePanel:
    return FigurePanel(app, id, title)


def _broken(app: Workbench, id: str, title: str) -> FigurePanel:
    with app.header:
        html.Span("half built")

    raise RuntimeError("build failed")


def _calls(app: Workbench, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[object, ...]]]:
    calls: list[tuple[str, tuple[object, ...]]] = []

    def record(ref: str | None = None, method: str | None = None, *args: object) -> None:
        calls.append((str(method), args))

    monkeypatch.setattr(app.server, "js_call", record)

    return calls


@dataclass
class _Loop:
    """The server loop as the countdown sees it: each delayed call is recorded, not run."""

    later: list[tuple[float, Callable[..., object], tuple[object, ...]]] = field(default_factory=list)

    def call_later(self, delay: float, fn: Callable[..., object], *args: object) -> None:
        self.later.append((delay, fn, args))


def test_a_broken_entry_point_leaves_the_catalogue_empty_and_the_title_plain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> list[object]:
        raise ImportError("no module named lab")

    monkeypatch.setattr(app_module, "discover", broken)

    app = _shell("broken", tmp_path)

    assert app.catalogue == [] and app.state[StateVar.WORKBENCHES] == []
    assert find(cast("dict[str, object]", app.ui.html), "ff-dropdown-menu") == []


def test_a_card_that_is_no_dock_or_builds_no_split_is_refused(tmp_path: Path) -> None:
    app = _shell("refused", tmp_path)

    with pytest.raises(ValueError, match="not a dock"):
        app.open_spec(replace(DEMO, kind=WorkbenchKind.SCRIPT))
    with pytest.raises(TypeError, match="not callable"):
        app.open_spec(replace(DEMO, target=f"{__name__}:NOT_CALLABLE"))
    with pytest.raises(TypeError, match="returned str"):
        app.open_spec(replace(DEMO, target=f"{__name__}:not_a_split"))

    assert app.opened == {} and app.panels == []


def test_a_failed_build_disposes_what_it_built_and_a_failed_rebuild_drops_its_header_widgets(tmp_path: Path) -> None:
    app = _shell("failed", tmp_path)

    with pytest.raises(RuntimeError, match="build failed"):
        app.add(Split([Panel("Good", _figure, id="good"), Panel("Bad", _broken, id="bad")]))

    assert app.panels == []

    with pytest.raises(RuntimeError, match="add"):
        app.replace_root(Split([Panel("Good", _figure, id="good")]))

    app.add(Split([Panel("Good", _figure, id="good")]))
    header = list(app.header.root.children)

    with pytest.raises(RuntimeError, match="build failed"):
        app.replace_root(Split([Panel("Good", _broken, id="good")]))

    assert app.header.root.children == header and app.panels == []


def test_without_a_dock_or_an_authored_split_the_dock_handlers_do_nothing(tmp_path: Path) -> None:
    app = _shell("dockless", tmp_path)

    app.on_dock_ready()
    with pytest.raises(RuntimeError, match="add"):
        _ = app.authored_layout()

    assert not app.docked

    root = Split([Panel("Good", _figure, id="good")])
    app.add(root)
    app.dock = None
    app.on_dock_ready()
    app.activate("good")
    app.place(root)
    app.reset_layout()
    app.on_break(signal.SIGTERM, None)

    assert not app.docked and app.front is None and app.pending == set()
    assert app.state[StateVar.STATUS] == "ready"


def test_the_ready_dock_and_a_reset_place_the_opened_cards_and_rebuild_a_closed_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("opened", tmp_path)
    calls = _calls(app, monkeypatch)
    app.add(Split([Panel("Main", _figure, id="main")]))
    app.open(Split([Panel("Extra", _figure, id="extra")]), reference="main", name="extra")
    calls.clear()

    app.on_dock_ready()

    assert [args[0] for method, args in calls if method == "addPanel"] == ["main", "extra"]

    extra = app.panel("extra", FigurePanel)
    app.on_panel_removed({"id": "extra"})
    calls.clear()

    app.reset_layout()

    assert app.panel("extra", FigurePanel) is not extra and app.state[StateVar.STATUS] == "layout reset"
    assert [args[0] for method, args in calls if method == "addPanel"] == ["extra"]

    app.replace_card("late", Split([Panel("Late", _figure, id="late")]), "main")

    assert app.opened_first("late") == "late" and app.state[StateVar.OPENED] == ["extra", "late"]


def test_the_title_menu_names_an_unknown_card_the_open_one_and_the_command_of_another(tmp_path: Path) -> None:
    app = _shell(DEMO.name, tmp_path)

    app.open_workbench("nope")
    unknown = app.state[StateVar.STATUS]
    app.open_workbench(DEMO.name)
    same = app.state[StateVar.STATUS]
    app.open_workbench(LAUNCHER.name)

    assert (unknown, same) == ("unknown workbench nope", f"{DEMO.title} is open")
    assert app.state[StateVar.STATUS] == f"open with: {' '.join(LAUNCHER.command())}"


def test_an_armed_cancel_counts_down_on_the_server_loop_and_a_field_viewer_opens_a_task_run(tmp_path: Path) -> None:
    app = _shell("countdown", tmp_path)

    app.cancel_task("a")

    assert app.state[StateVar.TASK_CONFIRM] == "", "without tasks Cancel does nothing"

    app.board = TaskBoard(app, "main:tasks", [Task("a", sys.gettrace)], path=None, roots=[])
    loop = _Loop()
    app.loop = cast("asyncio.AbstractEventLoop", loop)

    app.cancel_task("a")
    _, tick, args = loop.later[-1]
    _ = tick(*args)

    assert [(delay, args) for delay, _, args in loop.later] == [(1.0, ("a", 1, CONFIRM_S - 1)), (1.0, ("a", 1, CONFIRM_S - 2))]
    assert app.state[StateVar.TASK_CONFIRM] == "a" and app.state[StateVar.TASK_CONFIRM_LEFT] == CONFIRM_S - 1

    opened: list[str] = []
    app.ctrl[Hook.FIELD_OPEN] = opened.append
    app.open_task_run("r1")

    assert opened == ["r1"]


def test_a_card_of_the_shell_core_is_left_to_the_supervisor(tmp_path: Path) -> None:
    app = _shell("core", tmp_path)

    app.watch(replace(DEMO, name="core", target="femforge_workbench.dock:panels"))

    assert app.watches == []


def test_the_server_starts_the_card_watch_and_the_app_window_and_its_exit_stops_the_watch_and_the_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    windows: list[str] = []

    async def run() -> tuple[bool, bool, bool]:
        app = _shell("lifecycle", tmp_path)
        app.add(demo(), spec=DEMO)
        board = TaskBoard(app, "main:tasks", [Task("a", sys.gettrace)], path=None, roots=[])
        app.board = board
        app.window = Window.APP
        monkeypatch.setattr(app, "open_app_window", lambda: windows.append("app"))

        started = app.server.start(exec_mode="task", port=0, open_browser=False, show_connection_info=False, disable_logging=True, timeout=0)
        assert started is not None
        _ = await app.server.ready
        watcher = app.watches[0].reloader.watcher
        watching = watcher.observer is not None
        await app.server.stop()
        await started

        return watching, watcher.observer is None, board.closed

    assert asyncio.run(run()) == (True, True, True)
    assert windows == ["app"]


class _Browser:
    """The app window's process: it closes at once."""

    pid = 4242
    launched: list[list[str]] = []

    def __init__(self, command: list[str]) -> None:
        _Browser.launched.append(command)

    def wait(self) -> int:
        return 0


def test_the_app_window_runs_on_its_own_profile_and_stops_the_server_when_it_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("chrome", tmp_path)
    browser = tmp_path / "chrome.exe"
    _ = browser.write_text("", encoding="utf-8")
    stopping: list[object] = []

    def run_threadsafe(coroutine: Coroutine[object, object, object], loop: asyncio.AbstractEventLoop) -> None:
        coroutine.close()
        stopping.append(loop)

    def cache_dir(*args: object, **kwargs: object) -> str:
        return str(tmp_path / "cache")

    monkeypatch.setattr(_Browser, "launched", [])
    monkeypatch.setattr(app_module, "BROWSERS", (tmp_path / "missing.exe", browser))
    monkeypatch.setattr(app_module.platformdirs, "user_cache_dir", cache_dir)
    monkeypatch.setattr(app_module.subprocess, "Popen", _Browser)
    monkeypatch.setattr(app_module.asyncio, "run_coroutine_threadsafe", run_threadsafe)
    loop = asyncio.new_event_loop()
    app.loop = loop
    try:
        app.open_app_window()
        for thread in [thread for thread in threading.enumerate() if thread.name == "app-window"]:
            thread.join(JOIN_S)
    finally:
        loop.close()

    profile = tmp_path / "cache" / APP_PROFILE
    url = f"http://localhost:{app.server.port}/"
    assert _Browser.launched == [[str(browser), f"--app={url}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", f"--window-size={APP_WINDOW_SIZE}"]]
    assert profile.is_dir() and stopping == [loop]
