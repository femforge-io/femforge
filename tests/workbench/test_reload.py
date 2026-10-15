"""Reload on save: the trailing-edge debounce, the src-layout roots, a card rebuilt in place with its legacy carried over, a changed id set, a broken factory, the watch lifecycle, the supervisor and the dev page script."""

import asyncio
import os
import sys
import threading
import time
import types
from collections.abc import Callable
from pathlib import Path
from typing import cast

import psutil
import pytest
import pyvista as pv
from watchdog.events import DirModifiedEvent, FileClosedEvent, FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent, FileSystemEvent

import femforge
import femforge_workbench
from femforge_workbench import dev
from femforge_workbench.dev import DEV_ENV, DEV_FLAG, Supervisor, child_command, reload_script
from femforge_workbench.reload import (
    PERSISTENT_PACKAGES,
    REBUILD_LOCK,
    SHELL_MODULES,
    Reloader,
    SaveWatcher,
    default_roots,
    is_source_save,
    outermost,
    package_root,
    persistent,
    shell_files,
    source_root,
)
from femforge_workbench.widgets.module import assets
from tests.workbench.tree import find, prop


pytest.importorskip("trame")
pytest.importorskip("pyvista")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import StateVar as AppVar, Workbench  # noqa: E402
from femforge_workbench.demo import DemoPanel, workbench as demo  # noqa: E402
from femforge_workbench.dock import Panel, Split, layout_json, template_name  # noqa: E402
from femforge_workbench.launcher import workbench as launcher  # noqa: E402
from femforge_workbench.panels import Carry  # noqa: E402
from femforge_workbench.panels.figure import FigurePanel  # noqa: E402
from femforge_workbench.panels.launcher import LauncherPanelId  # noqa: E402
from femforge_workbench.panels.scene import ScenePanel, View, ViewMode  # noqa: E402
from femforge_workbench.panels.table import TablePanel  # noqa: E402
from femforge_workbench.persist import LocalJsonStore, StateKey  # noqa: E402
from femforge_workbench.registry import DEMO, panel  # noqa: E402
from femforge_workbench.widgets.dock import Dock  # noqa: E402


TIMEOUT = 20
DEBOUNCE = 0.1
SCENE = str(LauncherPanelId.SCENE)
LAUNCHER_IDS = [str(panel_id) for panel_id in LauncherPanelId]
DEMO_IDS = [str(panel_id) for panel_id in DemoPanel]
BOARD = (
    "from femforge_workbench.dock import Panel, Split\n"
    "from femforge_workbench.panels.figure import FigurePanel\n"
    "VERSION = {version}\n"
    "def build(app, id, title):\n"
    "    return FigurePanel(app, id, title)\n"
    "def workbench():\n"
    "    return Split([Panel('Board', build, id='board', weight=VERSION)])\n"
)
BROKEN = "def workbench(:\n"
VERSIONS = (1, 22, 333)
"""Source texts of three lengths, so a rewrite within the same second never matches a cached bytecode file."""


def _shell(name: str, root: Path) -> Workbench:
    return Workbench(name, title="Reload", server=get_server(f"reload-{name}", client_type="react"), persist=LocalJsonStore(root), user="tester")


def _package(tmp_path: Path, name: str) -> Path:
    """A src-layout package on the path whose board module is at VERSION 1; returns the module file."""
    src = tmp_path / "src"
    package = src / name
    package.mkdir(parents=True)
    _ = (package / "__init__.py").write_text("", encoding="utf-8")
    board = package / "board.py"
    _ = board.write_text(BOARD.format(version=VERSIONS[0]), encoding="utf-8")
    sys.path.insert(0, str(src))

    return board


def _forget(name: str, board: Path) -> None:
    """Drops the package's modules and its src directory from the interpreter."""
    for module in [module for module in sys.modules if module == name or module.startswith(f"{name}.")]:
        del sys.modules[module]

    sys.path.remove(str(board.parents[1]))


class _DockCalls:
    """Records the dock's add and remove calls in place of the client round trip; patched on the class, since an attribute set on the element would become a prop."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, str]] = []
        monkeypatch.setattr(Dock, "add_panel", self.add_panel)
        monkeypatch.setattr(Dock, "remove_panel", self.remove_panel)

    def add_panel(self, panel_id: str, *args: object, **kwargs: object) -> None:
        self.calls.append(("add", panel_id))

    def remove_panel(self, panel_id: str) -> None:
        self.calls.append(("remove", panel_id))


def _built(app: Workbench, panel_id: str) -> object:
    """The built panel of that id whatever its class: a purge replaces the classes the test module imported."""
    return next(panel for panel in app.panels if panel.id == panel_id)


class _Modules:
    """Snapshots the reloadable modules so a purge inside one test leaves the others their classes."""

    PREFIXES = (femforge.__name__, femforge_workbench.__name__)

    def __enter__(self) -> "_Modules":
        self.saved = {name: module for name, module in sys.modules.items() if name.startswith(self.PREFIXES)}

        return self

    def __exit__(self, *_: object) -> None:
        sys.modules.update(self.saved)


def _wait(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + TIMEOUT
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met")

        time.sleep(0.05)


async def _await(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + TIMEOUT
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met")

        await asyncio.sleep(0.05)


def _save(path: Path, version: int) -> None:
    """Writes the file with an mtime of its own, so the event that follows is a real save."""
    _ = path.write_text(f"VERSION = {version}\n", encoding="utf-8")
    stamp = 1_700_000_000_000_000_000 + version * 1_000_000_000
    os.utime(path, ns=(stamp, stamp))


def test_a_burst_of_saves_fires_once_after_the_last_one_and_a_stop_cancels_the_pending_fire(tmp_path: Path) -> None:
    fired: list[float] = []
    watcher = SaveWatcher([tmp_path], lambda: fired.append(time.monotonic()), debounce=DEBOUNCE)
    board = tmp_path / "board.py"
    saved = str(board)

    started = time.monotonic()
    for version in range(4):
        _save(board, version)
        watcher.on_any_event(FileModifiedEvent(saved))
        time.sleep(0.02)

    last = time.monotonic()
    _save(board, 4)
    watcher.on_any_event(FileModifiedEvent(saved))
    watcher.on_any_event(DirModifiedEvent(str(tmp_path)))
    watcher.on_any_event(FileModifiedEvent(str(tmp_path / "notes.txt")))
    assert fired == []

    time.sleep(DEBOUNCE * 3)
    assert len(fired) == 1
    assert fired[0] - last >= DEBOUNCE - 0.01 and fired[0] - started < DEBOUNCE * 3

    _save(board, 5)
    watcher.on_any_event(FileModifiedEvent(saved))
    watcher.stop()
    time.sleep(DEBOUNCE * 3)
    assert len(fired) == 1


def test_an_event_whose_file_kept_its_mtime_is_not_a_save(tmp_path: Path) -> None:
    fired: list[int] = []
    board = tmp_path / "board.py"
    _save(board, 1)
    watcher = SaveWatcher([tmp_path], lambda: fired.append(1), debounce=DEBOUNCE)
    assert watcher.changed(board.resolve()), "a file never seen counts as changed"

    watcher.on_any_event(FileModifiedEvent(str(board)))
    time.sleep(DEBOUNCE * 3)
    assert fired == [], "NTFS reports a read as a modification through the access time"

    _save(board, 2)
    watcher.on_any_event(FileModifiedEvent(str(board)))
    time.sleep(DEBOUNCE * 3)
    assert fired == [1]

    watcher.on_any_event(FileModifiedEvent(str(board)))
    watcher.on_any_event(FileModifiedEvent(str(tmp_path / "vanished.py")))
    time.sleep(DEBOUNCE * 3)
    assert fired == [1], "an unchanged file and a vanished one are not saves"

    fresh = tmp_path / "fresh.py"
    _save(fresh, 1)
    watcher.on_any_event(FileCreatedEvent(str(fresh)))
    time.sleep(DEBOUNCE * 3)
    assert fired == [1, 1]


def test_starting_a_watcher_records_every_watched_file_so_the_first_access_time_event_is_a_no_op(tmp_path: Path) -> None:
    board = tmp_path / "pkg" / "board.py"
    board.parent.mkdir()
    _save(board, 1)
    _ = (tmp_path / "pkg" / "notes.md").write_text("", encoding="utf-8")
    cache = tmp_path / "pkg" / "__pycache__"
    cache.mkdir()
    _save(cache / "board.cpython-314.py", 1)
    fired: list[int] = []
    everything = SaveWatcher([tmp_path], lambda: fired.append(1), debounce=DEBOUNCE)
    only = SaveWatcher([tmp_path], lambda: fired.append(2), debounce=DEBOUNCE, files=[board])

    everything.start()
    only.start()
    try:
        assert everything.stamps == {board.resolve(): board.stat().st_mtime_ns}
        assert only.stamps == {board.resolve(): board.stat().st_mtime_ns}
        everything.on_any_event(FileModifiedEvent(str(board)))
        only.on_any_event(FileModifiedEvent(str(board)))
        time.sleep(DEBOUNCE * 3)
        assert fired == []
    finally:
        everything.stop()
        only.stop()


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (FileModifiedEvent("lab/board.py"), "lab/board.py"),
        (FileCreatedEvent("lab/board.py"), "lab/board.py"),
        (FileMovedEvent("lab/board.py.tmp", "lab/board.py"), "lab/board.py"),
        (FileClosedEvent("lab/board.py"), None),
        (FileDeletedEvent("lab/board.py"), None),
        (FileModifiedEvent("lab/__pycache__/board.cpython-314.pyc"), None),
        (FileModifiedEvent("lab/notes.md"), None),
        (DirModifiedEvent("lab"), None),
    ],
)
def test_only_a_saved_source_file_counts(event: FileSystemEvent, expected: str | None) -> None:
    assert is_source_save(event) == expected


def test_the_roots_are_the_core_and_the_src_directory_of_a_src_layout_without_the_shell(tmp_path: Path) -> None:
    board = _package(tmp_path, "rl_roots")
    flat = tmp_path / "flat" / "rl_flat"
    flat.mkdir(parents=True)
    _ = (flat / "__init__.py").write_text("", encoding="utf-8")
    sys.path.insert(0, str(tmp_path / "flat"))
    core = source_root(femforge.__name__)
    shell = source_root(femforge_workbench.__name__)
    try:
        assert core == package_root(femforge.__name__).parent and core.name == "src"
        assert shell == package_root(femforge_workbench.__name__).parent and shell.name == "src"
        assert source_root("rl_roots.board") == board.parents[1]
        assert source_root("rl_flat") == flat
        assert default_roots("rl_roots.board") == [core, shell, board.parents[1]]
        assert default_roots("femforge_workbench.demo") == [core, shell]
        assert default_roots("femforge.viz.scene") == [core, shell]
        assert outermost([board.parents[1], board.parent, board.parents[1], core]) == [board.parents[1], core]
    finally:
        _forget("rl_roots", board)
        _forget("rl_flat", flat / "board.py")


def test_the_reloader_purges_and_rebuilds_the_target_and_hands_over_a_failure(tmp_path: Path) -> None:
    board = _package(tmp_path, "rl_target")
    results: list[tuple[float, float]] = []
    errors: list[Exception] = []
    delivered = threading.Event()

    def apply(root: Split, seconds: float) -> None:
        results.append((root.children[0].weight, seconds))
        delivered.set()

    def failed(error: Exception) -> None:
        errors.append(error)
        delivered.set()

    def marshal(fn: Callable[[], None]) -> None:
        fn()

    def rebuilt() -> None:
        delivered.clear()
        reloader.request()
        assert delivered.wait(TIMEOUT)

    reloader = Reloader("rl_target.board:workbench", [board.parents[1]], apply, kind=Split, marshal=marshal, failed=failed)
    try:
        rebuilt()
        assert results[0][0] == VERSIONS[0] and results[0][1] > 0 and "rl_target.board" in sys.modules

        _ = board.write_text(BOARD.format(version=VERSIONS[1]), encoding="utf-8")
        rebuilt()
        assert [weight for weight, _ in results] == [VERSIONS[0], VERSIONS[1]]

        _ = board.write_text(BROKEN, encoding="utf-8")
        rebuilt()
        assert len(results) == 2 and isinstance(errors[0], SyntaxError)

        reloader.start()
        assert reloader.roots == [board.parents[1]] and reloader.watcher.observer is not None
        delivered.clear()
        _ = board.write_text(BOARD.format(version=VERSIONS[2]), encoding="utf-8")
        assert delivered.wait(TIMEOUT)
        assert results[2][0] == VERSIONS[2]
    finally:
        reloader.stop()
        _forget("rl_target", board)

    assert reloader.watcher.observer is None


def test_two_reloaders_purge_and_build_one_at_a_time_and_never_evict_main(tmp_path: Path) -> None:
    board = _package(tmp_path, "rl_lock")
    main = types.ModuleType("__main__")
    main.__file__ = str(board.parent / "__main__.py")
    saved_main = sys.modules["__main__"]
    results: list[Split] = []
    errors: list[Exception] = []

    def apply(root: Split, seconds: float) -> None:
        results.append(root)

    def marshal(fn: Callable[[], None]) -> None:
        fn()

    reloaders = [Reloader("rl_lock.board:workbench", [board.parents[1]], apply, kind=Split, marshal=marshal, failed=errors.append) for _ in range(3)]
    threads = [threading.Thread(target=reloader.rebuild) for reloader in reloaders]
    sys.modules["__main__"] = main
    try:
        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(TIMEOUT)

        assert errors == [] and len(results) == 3
        assert sys.modules["__main__"] is main, "the entry module lives under the root but is never purged"
        assert not REBUILD_LOCK.locked()
    finally:
        sys.modules["__main__"] = saved_main
        _forget("rl_lock", board)


def test_one_save_purges_once_however_many_cards_watch_it(tmp_path: Path) -> None:
    board = _package(tmp_path, "rl_shared")
    generations: list[types.ModuleType] = []
    weights: list[float] = []
    errors: list[Exception] = []

    def apply(root: Split, seconds: float) -> None:
        generations.append(sys.modules["rl_shared.board"])
        weights.append(root.children[0].weight)

    def marshal(fn: Callable[[], None]) -> None:
        fn()

    def one_save(reloaders: list[Reloader[Split]]) -> None:
        observed = time.monotonic()
        for reloader in reloaders:
            reloader.requested = observed
            reloader.rebuild()

    reloaders = [
        Reloader(
            "rl_shared.board:workbench",
            [board.parents[1]],
            apply,
            kind=Split,
            marshal=marshal,
            failed=errors.append,
        )
        for _ in range(3)
    ]
    try:
        one_save(reloaders)

        assert errors == [] and weights == [VERSIONS[0]] * 3
        assert len(set(generations)) == 1, "a purge per card would leave every card on its own class generation"

        _ = board.write_text(BOARD.format(version=VERSIONS[1]), encoding="utf-8")
        one_save(reloaders)

        assert weights[3:] == [VERSIONS[1]] * 3, "the next save purges again"
        assert len(set(generations[3:])) == 1 and generations[0] not in generations[3:]
    finally:
        _forget("rl_shared", board)


def test_a_card_rebuilt_under_the_same_ids_swaps_its_panels_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("same-ids", tmp_path)
    app.add(launcher())
    demo_card = app.find_spec(DEMO.name)
    assert demo_card is not None
    app.open_spec(demo_card, reference=SCENE)
    dock = _DockCalls(monkeypatch)

    old_table = app.panel(DemoPanel.ROWS, TablePanel)
    old_sphere = app.panel(DemoPanel.SPHERE, ScenePanel)
    old_sphere.look(View.ISO)
    position = (3.0, -3.0, 3.0)
    reported: dict[str, object] = {"Id": old_sphere.camera_id, "Position": list(position), "FocalPoint": [0.8, 0.0, 0.0], "ViewUp": [0.0, 0.0, 1.0], "ViewAngle": 30.0, "ParallelScale": 1.5}
    old_sphere.on_camera(reported)
    layout_trigger = app.ctrl.trigger_name(old_table.on_layout)
    template_var = f"trame__template_{template_name(str(DemoPanel.ROWS))}"
    template_before = app.state[template_var]
    old_header = app.opened[str(DemoPanel.SPHERE)].header
    header_count = len(app.header.root.children)
    root = app.root
    assert root is not None
    app.on_layout_changed(layout_json(root))
    app.persist.flush()
    saved = app.persist.load(app.scope, StateKey.LAYOUT)
    assert len(old_header) == 1 and old_table.listeners and [watch.name for watch in app.watches] == [DEMO.name]

    app.rebuild(DEMO.name, demo(), seconds=1.0)

    table = app.panel(DemoPanel.ROWS, TablePanel)
    sphere = app.panel(DemoPanel.SPHERE, ScenePanel)
    assert app.panel_ids == [*LAUNCHER_IDS, *DEMO_IDS]
    assert table is not old_table and app.ctrl.trigger_fn(layout_trigger) is None and app.ctrl.trigger_fn(app.ctrl.trigger_name(table.on_layout)) == table.on_layout
    assert old_table.listeners == [] and table.listeners
    assert app.state[template_var] != template_before
    assert dock.calls == [] and app.persist.load(app.scope, StateKey.LAYOUT) == saved
    assert len(app.header.root.children) == header_count
    assert app.opened[str(DemoPanel.SPHERE)].header != old_header and old_header[0] not in app.header.root.children
    assert sphere is not old_sphere and isinstance(old_sphere.legacy[Carry.CAMERA], pv.Camera)
    assert old_sphere.legacy[Carry.MODE] == "client" and sphere.mode is ViewMode.CLIENT, "the card opened after the launcher, so its scene took the page's one mirror"
    assert app.panel(LauncherPanelId.SCENE, ScenePanel).mode is ViewMode.SERVER, "and the launcher's preview was streamed when it did"
    assert sphere.plotter.camera.position == pytest.approx(position), "the camera the mirror last reported came over, not the preset the server last set"
    assert str(app.state[AppVar.STATUS]).startswith(f"rebuilt {DEMO.name} in 1.")
    assert app.state[AppVar.OPENED] == [DEMO.name]


def test_a_panel_rebuilt_in_place_hears_that_it_is_on_screen(tmp_path: Path) -> None:
    seen: list[tuple[str, bool]] = []

    class Watched(FigurePanel):
        def on_visible(self, visible: bool) -> None:
            seen.append((self.id, visible))

    def build(app: Workbench, id: str, title: str) -> Watched:
        return Watched(app, id, title)

    def card() -> Split:
        return Split([Panel("Shown", build, id="shown"), Panel("Hidden", build, id="hidden"), Panel("Closed", build, id="closed")])

    app = _shell("on-screen", tmp_path)
    app.add(launcher())
    app.open(card(), name="watched")
    app.on_panel_visibility({"id": "shown", "visible": True})
    app.on_panel_visibility({"id": "hidden", "visible": True})
    app.on_panel_visibility({"id": "hidden", "visible": False})
    app.on_panel_visibility({"id": "closed", "visible": True})
    app.on_panel_removed({"id": "closed"})
    seen.clear()

    app.rebuild("watched", card())

    assert seen == [("shown", True)], "the dock reports a panel only when it mounts, so the shell tells the rebuilt one what it last heard"


def test_a_card_rebuilt_with_other_ids_closes_and_reopens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("other-ids", tmp_path)
    app.add(launcher())
    demo_card = app.find_spec(DEMO.name)
    assert demo_card is not None
    app.open_spec(demo_card, reference=SCENE)
    dock = _DockCalls(monkeypatch)
    old_table = app.panel(DemoPanel.ROWS, TablePanel)

    def build(app: Workbench, id: str, title: str) -> FigurePanel:
        return FigurePanel(app, id, title)

    app.rebuild(DEMO.name, Split([Panel("Extra", build, id="extra")]))

    assert app.panel_ids == [*LAUNCHER_IDS, "extra"]
    assert old_table.listeners == [] and old_table.bound == [] and old_table.on_layout not in app.ctrl._triggers_fn2name
    assert dock.calls == [*(("remove", panel_id) for panel_id in DEMO_IDS), ("add", "extra")]
    assert list(app.opened) == ["extra"] and app.opened["extra"].name == DEMO.name and app.opened["extra"].reference == SCENE
    assert app.state[AppVar.OPENED] == [DEMO.name]
    assert str(app.state[AppVar.STATUS]).startswith(f"rebuilt {DEMO.name} in")


def test_a_broken_build_keeps_the_card_reports_and_the_next_good_save_rebuilds_it(tmp_path: Path) -> None:
    board = _package(tmp_path, "rl_card")
    spec = panel("board", "Board", target="rl_card.board:workbench")

    async def run() -> None:
        app = _shell("broken", tmp_path)
        app.add(launcher())
        app.catalogue.append(spec)
        app.loop = asyncio.get_running_loop()

        app.open_spec(spec, reference=SCENE)
        watch = app.watches[0]
        before = app.panel("board", FigurePanel)
        assert watch.name == spec.name and watch.reloader.watcher.observer is not None

        _ = board.write_text(BROKEN, encoding="utf-8")
        await _await(lambda: str(app.state[AppVar.STATUS]).startswith("SyntaxError"))
        assert _built(app, "board") is before and app.state[AppVar.OPENED] == [spec.name]

        _ = board.write_text(BOARD.format(version=VERSIONS[1]), encoding="utf-8")
        await _await(lambda: str(app.state[AppVar.STATUS]).startswith("rebuilt board in"))
        rebuilt = _built(app, "board")
        assert rebuilt is not before and type(rebuilt).__name__ == "FigurePanel" and type(rebuilt) is not FigurePanel, "the save purged the panels package too"
        assert app.opened["board"].root.children[0].weight == VERSIONS[1]

        app.close(spec.name)
        assert app.watches == [] and watch.reloader.watcher.observer is None
        assert app.panel_ids == LAUNCHER_IDS

    try:
        with _Modules():
            asyncio.run(run())
    finally:
        _forget("rl_card", board)


def test_a_spec_served_standalone_is_rebuilt_as_the_authored_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("standalone", tmp_path)
    layouts: list[object] = []

    def restore_layout(layout: object) -> None:
        layouts.append(layout)

    app.add(demo(), spec=DEMO)

    assert app.authored is not None and app.authored.name == DEMO.name and len(app.authored.header) == 1
    assert [watch.name for watch in app.watches] == [DEMO.name] and app.watches[0].reloader.roots == default_roots(DEMO.module), "the demo purges and rebuilds in process"
    assert app.dock is not None
    monkeypatch.setattr(app.dock, "restore_layout", restore_layout)
    old_table = app.panel(DemoPanel.ROWS, TablePanel)
    first_root = app.root

    app.rebuild(DEMO.name, demo())

    assert app.root is not first_root and app.panel_ids == DEMO_IDS and app.panel(DemoPanel.ROWS, TablePanel) is not old_table
    assert layouts == [] and app.opened == {}
    assert str(app.state[AppVar.STATUS]).startswith(f"rebuilt {DEMO.name} in")

    def build(app: Workbench, id: str, title: str) -> FigurePanel:
        return FigurePanel(app, id, title)

    app.rebuild(DEMO.name, Split([Panel("Only", build, id="only")]))

    assert app.panel_ids == ["only"] and len(layouts) == 1
    assert old_table.on_layout not in app.ctrl._triggers_fn2name

    lab = panel("board", "Board", target="rl_lab.board:workbench")
    board = _package(tmp_path, "rl_lab")
    try:
        app.add(Split([Panel("Board", build, id="board")]), spec=lab, watch=[tmp_path / "extra"])
        expected = [*default_roots(lab.module), (tmp_path / "extra").resolve()]
    finally:
        _forget("rl_lab", board)

    assert [watch.name for watch in app.watches] == [DEMO.name, lab.name] and app.watches[1].reloader.watcher.observer is None
    assert app.watches[1].reloader.roots == expected and board.parents[1] in expected


def test_the_supervisor_restarts_its_child_after_a_save_and_stops_on_request(tmp_path: Path) -> None:
    supervisor = Supervisor([sys.executable, "-c", "import time; time.sleep(60)"], [tmp_path])
    thread = threading.Thread(target=supervisor.serve, daemon=True)

    thread.start()
    _wait(lambda: supervisor.child is not None)
    first = supervisor.child
    assert first is not None and first.poll() is None

    _ = (tmp_path / "shell.py").write_text("x = 1\n", encoding="utf-8")
    _wait(lambda: supervisor.starts == 2)
    second = supervisor.child
    assert second is not None and second is not first and second.pid != first.pid
    assert first.poll() is not None and second.poll() is None

    supervisor.stop()
    thread.join(TIMEOUT)

    assert not thread.is_alive() and second.poll() is not None and supervisor.watcher.observer is None
    assert child_command("femforge_workbench.cli", ["demo", DEV_FLAG, "--port", "8096"]) == [sys.executable, "-m", "femforge_workbench.cli", "demo", "--port", "8096"]


GRACEFUL_CHILD = (
    "import signal, sys, time\n"
    "marker = sys.argv[1]\n"
    "def stop(*_):\n"
    "    open(marker, 'w').write('clean')\n"
    "    sys.exit(0)\n"
    "signal.signal(getattr(signal, 'SIGBREAK', signal.SIGTERM), stop)\n"
    "while True:\n"
    "    time.sleep(0.05)\n"
)
"""A child that exits cleanly on the supervisor's break, leaving a marker, as the workbench flushes its store."""

STUBBORN_CHILD = "import signal, time\nsignal.signal(getattr(signal, 'SIGBREAK', signal.SIGTERM), signal.SIG_IGN)\nwhile True:\n    time.sleep(0.05)\n"


def test_the_supervisor_breaks_its_child_first_and_kills_one_that_ignores_the_break(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "clean.txt"
    supervisor = Supervisor([sys.executable, "-c", GRACEFUL_CHILD, str(marker)], [tmp_path])
    thread = threading.Thread(target=supervisor.serve, daemon=True)
    thread.start()
    _wait(lambda: supervisor.child is not None)
    child = supervisor.child
    assert child is not None
    time.sleep(0.5)

    supervisor.stop()
    thread.join(TIMEOUT)

    assert not thread.is_alive() and child.returncode == 0
    assert marker.read_text(encoding="utf-8") == "clean", "the child ran its exit path instead of being terminated"

    monkeypatch.setattr(dev, "STOP_GRACE", 0.5)
    stubborn = Supervisor([sys.executable, "-c", STUBBORN_CHILD], [tmp_path])
    thread = threading.Thread(target=stubborn.serve, daemon=True)
    thread.start()
    _wait(lambda: stubborn.child is not None)
    child = stubborn.child
    assert child is not None
    time.sleep(0.5)

    stubborn.stop()
    thread.join(TIMEOUT)

    assert not thread.is_alive() and child.returncode != 0 and not psutil.pid_exists(child.pid)


def test_the_shell_core_is_the_supervisors_and_everything_else_rebuilds_in_process(tmp_path: Path) -> None:
    fired: list[float] = []
    core_file = tmp_path / "app.py"
    _save(core_file, 1)
    watcher = SaveWatcher([tmp_path], lambda: fired.append(time.monotonic()), debounce=DEBOUNCE, files=[core_file])

    watcher.on_any_event(FileModifiedEvent(str(tmp_path / "panels" / "scene.py")))
    time.sleep(DEBOUNCE * 3)
    assert fired == [], "a save outside the listed files never wakes the supervisor"

    watcher.on_any_event(FileModifiedEvent(str(core_file)))
    time.sleep(DEBOUNCE * 3)
    assert len(fired) == 1

    files = shell_files()
    assert [file.name for file in files] == ["app.py", "cli.py", "dev.py", "dock.py", "persist.py", "registry.py", "reload.py", "runner.py", "serve.py", "settings.py", "element.py", "module.py"]
    assert all(file.exists() for file in files) and len(SHELL_MODULES) == len(files)
    assert persistent("femforge_workbench.app") and persistent("femforge_workbench.widgets.element") and persistent("femforge.common.log")
    assert not persistent("femforge_workbench.panels") and not persistent("femforge_workbench.widgets.button") and not persistent("femforge_workbench.theme")
    assert not persistent("femforge_workbench.demo") and not persistent("femforge_workbench.widgets")
    assert len(PERSISTENT_PACKAGES) == len(SHELL_MODULES) + 1


def test_a_save_under_the_panels_purges_them_and_rebuilds_the_open_cards_with_fresh_classes(tmp_path: Path) -> None:
    app = _shell("purge", tmp_path)
    app.add(launcher())
    demo_card = app.find_spec(DEMO.name)
    assert demo_card is not None
    app.open_spec(demo_card, reference=SCENE)
    watch = app.watches[0]
    old_sphere = app.panel(DemoPanel.SPHERE, ScenePanel)
    old_sphere.look(View.TOP)
    position = old_sphere.plotter.camera.position
    with _Modules() as modules:
        root = watch.reloader.build()
        purged = [name for name in modules.saved if name not in sys.modules or sys.modules[name] is not modules.saved[name]]
        app.rebuild(DEMO.name, root)

        sphere = next(panel for panel in app.panels if panel.id == str(DemoPanel.SPHERE))
        assert "femforge_workbench.panels.scene" in purged and "femforge_workbench.demo" in purged and "femforge_workbench.theme" in purged
        assert "femforge_workbench.app" not in purged and "femforge_workbench.widgets.element" not in purged and "femforge_workbench.dock" not in purged
        assert type(sphere).__name__ == "ScenePanel" and type(sphere) is not ScenePanel, "the rebuilt panel is of the freshly imported class"
        assert cast("ScenePanel", sphere).plotter.camera.position == pytest.approx(position), "the camera came over through legacy, not through an isinstance on the old class"
        assert old_sphere.bound == [] and app.panel_ids == [*LAUNCHER_IDS, *DEMO_IDS]
        assert str(app.state[AppVar.STATUS]).startswith(f"rebuilt {DEMO.name} in")
        app.close(DEMO.name)


def test_the_page_follows_a_supervised_server_only_under_dev(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def scripts(app: Workbench) -> list[object]:
        """The inline scripts of the page, whose contents trame keeps in the state variables the script props bind."""
        bindings = [cast("dict[str, str]", prop(node, "script")) for node in find(app.ui.html, "trame-script")]

        return [app.state[binding["js"]] for binding in bindings]

    monkeypatch.delenv(DEV_ENV, raising=False)
    plain = _shell("dev-off", tmp_path)
    assert scripts(plain) == []

    monkeypatch.setenv(DEV_ENV, "1")
    dev = _shell("dev-on", tmp_path)
    script = reload_script()
    assert scripts(dev) == [script]
    assert "method: 'HEAD'" in script and "location.reload()" in script
    assert "/__ff/version.json" in script and "cache: 'no-store'" in script and "cache: 'reload'" in script
    assert all(asset in script for asset in assets()) and "window.Ff" in script
