"""Refuses a femforge-workbench sdist or wheel whose client dist is missing or older than its sources."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

from hatchling.builders.config import BuilderConfig
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


REQUIRED = ("ff.umd.js", "ff.css", "popout.html", "props.json", "theme.json", "icons.json", "version.json", "THIRD_PARTY_LICENSES.txt")
HINT = "run `npm ci && npm run build` in packages/femforge-workbench/js"
HASH_MODULE = Path(__file__).parent / "src" / "femforge_workbench" / "widgets" / "dist_hash.py"


def dist_hash() -> ModuleType:
    """The package's dist_hash module, loaded by path because the package is not importable while it builds."""
    spec = importlib.util.spec_from_file_location("femforge_workbench_dist_hash", HASH_MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {HASH_MODULE}.")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


class ClientDistHook(BuildHookInterface[BuilderConfig]):
    """Checks the client dist before hatchling packs it."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        """Raises unless every dist file exists and version.json carries the current source hash; editable installs skip the check."""
        if version == "editable":
            return

        root = Path(self.root)
        hashing = dist_hash()
        serve = cast("Path", hashing.SERVE)
        missing = [name for name in REQUIRED if not (root / serve / name).is_file()]
        if missing:
            raise RuntimeError(f"The client dist lacks {', '.join(missing)}: {HINT}.")

        manifest = cast("object", json.loads((root / serve / "version.json").read_text(encoding="utf-8")))
        built = cast("dict[str, object]", manifest).get("hash") if isinstance(manifest, dict) else None
        source_hash = cast("Callable[[Path], str]", hashing.source_hash)
        expected = source_hash(root)
        if built != expected:
            raise RuntimeError(f"The client dist is stale (version.json {built}, sources {expected}): {HINT}.")
