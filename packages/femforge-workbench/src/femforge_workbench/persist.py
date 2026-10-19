"""UI state persistence: the UiStateStore protocol and a debounced per-user JSON implementation under the user config directory."""

import getpass
import json
import threading
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

import platformdirs

from femforge.common.files import write_atomic


APP_NAME = "femforge-workbench"
DEBOUNCE_SECONDS = 0.5

type Scope = tuple[str, str]
"""The (user_id, workbench_name) a saved document belongs to."""

type Data = dict[str, object]
"""A persisted document: JSON-ready values by key."""


class StateKey(StrEnum):
    """The documents the shell itself persists per workbench."""

    LAYOUT = "layout"
    OPENED = "opened"
    TOUR = "tour"
    TASK_SHEET = "task_sheet"


class UiStateStore(Protocol):
    """Where a workbench keeps its UI state per user: layouts, dropdowns, brushes."""

    def load(self, scope: Scope, key: str) -> Data | None:
        """The document saved under `key` in `scope`, None when there is none."""
        ...

    def save(self, scope: Scope, key: str, data: Mapping[str, object]) -> None:
        """Saves `data` as the document under `key` in `scope`.

        Args:
            scope: The user and workbench the document belongs to.
            key: The document's name within the scope.
            data: The JSON-ready document.
        """
        ...

    def flush(self) -> None:
        """Writes every pending save now."""
        ...


def default_user() -> str:
    """The OS user, the local stand-in for an authenticated user id."""
    return getpass.getuser()


def default_root() -> Path:
    """The per-user config directory the local store writes under."""
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


class LocalJsonStore:
    """One JSON file per (user, workbench, key), written at most once per debounce window; a load sees unflushed saves."""

    def __init__(self, root: Path | None = None, *, debounce: float = DEBOUNCE_SECONDS) -> None:
        """Stores under `root`, the per-user config directory by default, writing at most once per `debounce` seconds."""
        self.root = root or default_root()
        self.debounce = debounce
        self.pending: dict[tuple[Scope, str], Data] = {}
        self.timer: threading.Timer | None = None
        self.lock = threading.Lock()

    def path(self, scope: Scope, key: str) -> Path:
        """The JSON file of one document."""
        user, workbench = scope

        return self.root / user / workbench / f"{key}.json"

    def load(self, scope: Scope, key: str) -> Data | None:
        """The document: a pending save first, else the file's, None when neither exists."""
        with self.lock:
            pending = self.pending.get((scope, key))

        if pending is not None:
            return dict(pending)

        path = self.path(scope, key)
        if not path.exists():
            return None

        loaded = cast("object", json.loads(path.read_text(encoding="utf-8")))
        if not isinstance(loaded, dict):
            return None

        return cast("Data", loaded)

    def save(self, scope: Scope, key: str, data: Mapping[str, object]) -> None:
        """Queues the document and restarts the debounce timer that writes it.

        Args:
            scope: The user and workbench the document belongs to.
            key: The document's name within the scope.
            data: The JSON-ready document.
        """
        with self.lock:
            self.pending[(scope, key)] = dict(data)
            if self.timer is not None:
                self.timer.cancel()

            self.timer = threading.Timer(self.debounce, self.flush)
            self.timer.daemon = True
            self.timer.start()

    def flush(self) -> None:
        """Writes every pending document now."""
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None

            pending = self.pending
            self.pending = {}

        for (scope, key), data in pending.items():
            path = self.path(scope, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(path, json.dumps(data, indent=1))
