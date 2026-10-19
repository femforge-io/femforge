"""Watchdog-driven module reload: a .py save under the roots purges and re-imports a workbench target, builds its result on a worker thread and hands it to the server loop."""

import importlib
import linecache
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Protocol

from watchdog.events import EVENT_TYPE_CREATED, EVENT_TYPE_MODIFIED, EVENT_TYPE_MOVED, FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

import femforge
import femforge_workbench
from femforge.common.log import get_console, log
from femforge_workbench.registry import MAIN_MODULE, resolve, split_target


DEBOUNCE_SECONDS = 0.3
"""Seconds after the last save of a burst before it is acted on: an editor writes one file in several events, a save-all several files."""

PYCACHE = "__pycache__"
SOURCE_SUFFIX = ".py"
SRC_LAYOUT = "src"
PACKAGE_MARKER = "__init__.py"
SAVE_EVENTS = (EVENT_TYPE_MODIFIED, EVENT_TYPE_CREATED, EVENT_TYPE_MOVED)

REBUILD_LOCK = threading.Lock()
"""Serializes purge-and-import across the reloaders of every open card: two threads evicting and importing the same modules at once corrupt each other."""

WATCHING_LOCK = threading.Lock()
"""Guards the watching list alone, never held across a rebuild, so stopping a reloader does not wait for one."""

SHELL_MODULES = ("app", "cli", "dev", "dock", "persist", "registry", "reload", "runner", "serve", "settings", "tasks", "widgets.element", "widgets.module")
"""The shell core, relative to the workbench package: never purged in process, restarted by the --dev supervisor instead."""

PERSISTENT_PACKAGES = (*(f"{femforge_workbench.__name__}.{name}" for name in SHELL_MODULES), "femforge.common.log")
"""Modules the purge leaves alive: the shell core and the logging singleton it shares with the reloaded code."""

type Apply[R] = Callable[[R, float], None]
"""Receives the fresh result and the seconds the purge, import and factory took."""

type Failed = Callable[[Exception], None]
type Marshal = Callable[[Callable[[], None]], None]


class Watched(Protocol):
    """What the shared purge needs of a reloader: the roots it watches."""

    @property
    def roots(self) -> list[Path]:
        """The directories the reloader watches."""
        ...


WATCHING: list[Watched] = []
"""The reloaders of the open cards, whose roots the shared purge covers."""


@dataclass
class Purge:
    """The last eviction the reloaders shared: when it started and the roots it covered."""

    at: float = 0.0
    roots: tuple[Path, ...] = ()

    def covers(self, roots: Sequence[Path], requested: float) -> bool:
        """Whether this purge followed the save at `requested` and evicted every one of those roots."""
        if self.at < requested:
            return False

        return all(any(root.is_relative_to(purged) for purged in self.roots) for root in roots)


PURGED = Purge()
"""Read and written under REBUILD_LOCK only."""


def package_root(module_name: str) -> Path:
    """The directory of the top-level package a module belongs to."""
    top = importlib.import_module(module_name.split(".", 1)[0])
    file = top.__file__
    if file is None:
        raise ValueError(f"{module_name!r} has no package directory to watch")

    return Path(file).resolve().parent


def source_root(module_name: str) -> Path:
    """The directory to watch for a module: the `src` directory of a src layout, so its sibling packages reload with it, else the package itself."""
    package = package_root(module_name)
    parent = package.parent
    if parent.name == SRC_LAYOUT and not (parent / PACKAGE_MARKER).exists():
        return parent

    return package


def persistent(name: str) -> bool:
    """Whether a module stays loaded across a purge."""
    return any(name == package or name.startswith(package + ".") for package in PERSISTENT_PACKAGES)


def shell_files() -> list[Path]:
    """The source files of the shell core."""
    root = package_root(femforge_workbench.__name__)

    return [root.joinpath(*name.split(".")).with_suffix(SOURCE_SUFFIX) for name in SHELL_MODULES]


def default_roots(workbench_module: str) -> list[Path]:
    """The femforge core's source root, the workbench package's (its panels, widgets and theme rebuild in process) and the module's own."""
    roots = [source_root(femforge.__name__), source_root(femforge_workbench.__name__)]
    own = source_root(workbench_module)
    if own not in roots:
        roots.append(own)

    return roots


def outermost(roots: Sequence[Path]) -> list[Path]:
    """The resolved roots without repeats and without any lying under another, since each is watched recursively."""
    resolved = list(dict.fromkeys(root.resolve() for root in roots))

    return [root for root in resolved if not any(root != other and root.is_relative_to(other) for other in resolved)]


def is_source_save(event: FileSystemEvent) -> str | None:
    """The saved .py path an event reports, None for anything else; an editor saves as a modification, or as a create or move of a temp file."""
    if event.event_type not in SAVE_EVENTS:
        return None

    path = os.fsdecode(event.dest_path or event.src_path)
    if event.is_directory or not path.endswith(SOURCE_SUFFIX) or PYCACHE in path:
        return None

    return path


class SaveWatcher(FileSystemEventHandler):
    """Calls `on_save` once per burst of .py saves under the roots (only `files` when given), `debounce` seconds after the last one; a save is an event whose file's mtime changed."""

    def __init__(self, roots: Sequence[Path], on_save: Callable[[], None], *, debounce: float = DEBOUNCE_SECONDS, files: Sequence[Path] | None = None) -> None:
        """Sets the watcher up stopped; `start` begins observing.

        Args:
            roots: The directories watched recursively.
            on_save: Called once per burst of saves.
            debounce: Seconds after the last save of a burst before `on_save` runs.
            files: The only files whose saves count; None counts every .py file under the roots.
        """
        super().__init__()
        self.roots = outermost(roots)
        self.files = {file.resolve() for file in files} if files is not None else None
        self.on_save = on_save
        self.debounce = debounce
        self.stamps: dict[Path, int] = {}
        """The mtime last seen per file: watchdog also reports access-time updates, which NTFS writes on reads."""
        self.timer: threading.Timer | None = None
        self.lock = threading.Lock()
        self.observer: BaseObserver | None = None
        self.observed = 0.0
        """When the last save of the current burst was seen, which every reloader watching it reads before any purge."""

    def watched(self) -> list[Path]:
        """The files whose saves count: `files` when given, else every source file under the roots."""
        if self.files is not None:
            return sorted(self.files)

        return [path for root in self.roots for path in root.rglob(f"*{SOURCE_SUFFIX}") if PYCACHE not in path.parts]

    def changed(self, path: Path) -> bool:
        """Whether the file's mtime differs from the last one seen; a file never seen counts as changed, a vanished one as unchanged."""
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return False

        if self.stamps.get(path) == stamp:
            return False

        self.stamps[path] = stamp

        return True

    def start(self) -> None:
        """Records every watched file's mtime and starts observing the roots."""
        for path in self.watched():
            _ = self.changed(path)

        observer: BaseObserver = Observer()
        for root in self.roots:
            _ = observer.schedule(self, str(root), recursive=True)

        observer.start()
        self.observer = observer
        log.bind(roots=[str(root) for root in self.roots]).info("Watching for saves.")

    def stop(self) -> None:
        """Cancels a pending call and stops observing."""
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None

        if self.observer is None:
            return

        self.observer.stop()
        self.observer.join()
        self.observer = None

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Every save restarts the timer, so only the last of a burst fires."""
        path = is_source_save(event)
        if path is None:
            return

        resolved = Path(path).resolve()
        if self.files is not None and resolved not in self.files:
            return

        if not self.changed(resolved):
            return

        self.observed = time.monotonic()
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()

            self.timer = threading.Timer(self.debounce, self.fire)
            self.timer.daemon = True
            self.timer.start()

        log.bind(path=path).debug("File changed.")

    def fire(self) -> None:
        """Calls `on_save` for the burst that just settled."""
        with self.lock:
            self.timer = None

        self.on_save()


class Reloader[R]:
    """Rebuilds the target on a worker thread after each burst of saves under the roots, one rebuild at a time with at most one more queued."""

    def __init__(self, target: str, roots: Sequence[Path], apply: Apply[R], *, kind: type[R], marshal: Marshal, failed: Failed | None = None) -> None:
        """Sets the reloader up stopped; `start` watches the roots.

        Args:
            target: The `pkg.module:attr` factory rebuilt after every save.
            roots: The directories whose .py saves trigger a rebuild.
            apply: Receives each result and the seconds it took, on the server loop.
            kind: The type the factory must return.
            marshal: Runs a callable on the server loop.
            failed: Receives a failed build's error on the server loop; None drops it.
        """
        self.target = target
        self.kind = kind
        self.apply = apply
        self.failed = failed
        self.marshal = marshal
        self.watcher = SaveWatcher(roots, self.request)
        self.running = False
        self.queued = False
        self.requested = 0.0
        """When the save that asked for the pending rebuild was seen; a purge older than that is not this rebuild's."""
        self.lock = threading.Lock()

    @property
    def roots(self) -> list[Path]:
        """The directories the watcher observes."""
        return self.watcher.roots

    def start(self) -> None:
        """Watches the roots; `request` builds now."""
        with WATCHING_LOCK:
            if self not in WATCHING:
                WATCHING.append(self)

        self.watcher.start()

    def stop(self) -> None:
        """Stops watching and leaves the shared purge."""
        with WATCHING_LOCK:
            if self in WATCHING:
                WATCHING.remove(self)

        self.watcher.stop()

    def request(self) -> None:
        """Starts a rebuild, or queues one more when a rebuild is running."""
        self.requested = self.watcher.observed or time.monotonic()
        with self.lock:
            if self.running:
                self.queued = True
                return

            self.running = True

        threading.Thread(target=self.work, name="workbench-reload", daemon=True).start()

    def work(self) -> None:
        """Rebuilds until no request is queued."""
        while True:
            self.rebuild()

            with self.lock:
                if not self.queued:
                    self.running = False
                    return

                self.queued = False

    def rebuild(self) -> None:
        """Builds once and delivers the result, or the failure, to the server loop."""
        started = time.perf_counter()
        requested = self.requested or time.monotonic()
        try:
            result = self.build(requested)
        except Exception as error:
            get_console().print_exception()
            failure = error
            self.deliver(lambda: self.report(failure))
            return

        seconds = time.perf_counter() - started
        log.bind(target=self.target, seconds=round(seconds, 2)).info("Target rebuilt.")
        self.deliver(lambda: self.apply(result, seconds))

    def deliver(self, outcome: Callable[[], None]) -> None:
        """Runs `outcome` on the server loop, dropping it when the loop is gone."""
        try:
            self.marshal(outcome)
        except RuntimeError:
            log.bind(target=self.target).warning("No server loop to deliver the rebuild to; dropped.")

    def report(self, error: Exception) -> None:
        """Hands a failed build's error to `failed`."""
        if self.failed is not None:
            self.failed(error)

    def build(self, requested: float | None = None) -> R:
        """Purges the watched modules, imports the target afresh and calls it, one reloader at a time; a build without a save behind it always purges."""
        with REBUILD_LOCK:
            self.purge(time.monotonic() if requested is None else requested)

            factory = resolve(self.target)
            if not callable(factory):
                raise TypeError(f"{self.target} is not callable")

            result = factory()
        if not isinstance(result, self.kind):
            raise TypeError(f"{self.target}() returned {type(result).__name__}, not a {self.kind.__name__}")

        return result

    def purge(self, requested: float) -> None:
        """Drops every loaded module under the watched roots except the persistent packages, once per save however many cards are open."""
        global PURGED
        with WATCHING_LOCK:
            roots = outermost([*self.roots, *(root for watched in WATCHING for root in watched.roots)])

        # One purge per card would evict what the card before it just imported, leaving every card on its own class
        # generation; the roots of every open card are covered here, so the cards that follow skip their own.
        if PURGED.covers(roots, requested):
            log.bind(module=split_target(self.target)[0]).debug("Modules already purged for this save.")
            return

        def evictable(name: str, module: ModuleType) -> bool:
            if name == MAIN_MODULE or persistent(name):
                return False

            file = getattr(module, "__file__", None)
            if not isinstance(file, str):
                return False

            path = Path(file).resolve()

            return any(path.is_relative_to(root) for root in roots)

        at = time.monotonic()
        stale = [name for name, module in list(sys.modules.items()) if evictable(name, module)]
        for name in stale:
            _ = sys.modules.pop(name, None)

        linecache.checkcache()
        PURGED = Purge(at, tuple(roots))
        log.bind(module=split_target(self.target)[0], purged=len(stale), roots=len(roots)).debug("Modules purged.")
