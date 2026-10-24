"""The workbench client's package-lock.json keeps every nested entry its bundled dependencies need."""

import json
from pathlib import Path
from typing import cast


JS_DIR = Path(__file__).parents[2] / "packages" / "femforge-workbench" / "js"


def test_every_bundled_dependency_keeps_its_nested_lock_entry() -> None:
    lock = cast("dict[str, dict[str, dict[str, object]]]", json.loads((JS_DIR / "package-lock.json").read_text(encoding="utf-8")))
    packages: dict[str, dict[str, object]] = lock["packages"]

    missing: list[str] = []
    for path, entry in packages.items():
        bundled = entry.get("bundleDependencies")
        names: list[str] = cast("list[str]", bundled) if isinstance(bundled, list) else []
        missing += [f"{path}/node_modules/{name}" for name in names if f"{path}/node_modules/{name}" not in packages]

    assert missing == [], f"package-lock.json lost {missing}, so npm ci fails; regenerate it on Linux as docs/contributing/index.md describes"
