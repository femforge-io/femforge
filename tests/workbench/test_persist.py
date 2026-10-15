"""The local JSON UI-state store: round trip, debounced writes and scope isolation."""

import json
import time
from pathlib import Path

from femforge_workbench.persist import APP_NAME, LocalJsonStore, StateKey, UiStateStore, default_root, default_user


SCOPE = ("tester", "demo")


def _store(root: Path, debounce: float = 10.0) -> LocalJsonStore:
    return LocalJsonStore(root, debounce=debounce)


def test_a_save_is_visible_before_the_flush_and_lands_in_one_file_after(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.save(SCOPE, StateKey.LAYOUT, {"grid": {"root": 1}})
    pending = store.load(SCOPE, StateKey.LAYOUT)
    store.flush()

    path = tmp_path / "tester" / "demo" / "layout.json"
    assert pending == {"grid": {"root": 1}}
    assert json.loads(path.read_text(encoding="utf-8")) == {"grid": {"root": 1}}
    assert _store(tmp_path).load(SCOPE, StateKey.LAYOUT) == {"grid": {"root": 1}}
    assert store.timer is None and store.pending == {}


def test_saves_inside_the_window_collapse_into_one_write(tmp_path: Path) -> None:
    store = _store(tmp_path, debounce=0.05)
    path = store.path(SCOPE, StateKey.LAYOUT)

    store.save(SCOPE, StateKey.LAYOUT, {"n": 1})
    store.save(SCOPE, StateKey.LAYOUT, {"n": 2})
    unwritten = path.exists()
    deadline = time.monotonic() + 3.0
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)

    assert not unwritten
    assert json.loads(path.read_text(encoding="utf-8")) == {"n": 2}
    assert not list((tmp_path / "tester" / "demo").glob("*.tmp"))


def test_scopes_and_keys_do_not_collide(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.save(SCOPE, StateKey.LAYOUT, {"who": "tester"})
    store.save(("other", "demo"), StateKey.LAYOUT, {"who": "other"})
    store.save(SCOPE, "brush", {"range": [1, 2]})
    store.flush()

    assert store.load(SCOPE, StateKey.LAYOUT) == {"who": "tester"}
    assert store.load(("other", "demo"), StateKey.LAYOUT) == {"who": "other"}
    assert store.load(SCOPE, "brush") == {"range": [1, 2]}
    assert store.load(("tester", "elsewhere"), StateKey.LAYOUT) is None


def test_missing_or_malformed_documents_load_as_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = store.path(SCOPE, StateKey.LAYOUT)
    path.parent.mkdir(parents=True)
    _ = path.write_text("[1, 2]", encoding="utf-8")

    assert store.load(SCOPE, StateKey.LAYOUT) is None
    assert store.load(SCOPE, "absent") is None


def test_the_store_satisfies_the_protocol_and_defaults_to_the_user_config_dir() -> None:
    store: UiStateStore = LocalJsonStore()

    assert isinstance(store, LocalJsonStore)
    assert APP_NAME in str(default_root())
    assert default_user()
