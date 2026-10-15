"""Refuses a femforge-workbench sdist or wheel whose client dist is missing or older than its sources."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import cast

from hatchling.builders.config import BuilderConfig
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


SERVE = Path("src", "femforge_workbench", "widgets", "serve")
REQUIRED = ("ff.umd.js", "ff.css", "popout.html", "props.json", "theme.json", "icons.json", "version.json", "THIRD_PARTY_LICENSES.txt")
HINT = "run `npm ci && npm run build` in packages/femforge-workbench/js"

HASHED_DIRS = ("src", "public", "scripts")
HASHED_FILES = ("package.json", "package-lock.json", "vite.config.ts", "components.json", "tsconfig.json")
SCANNED = Path("src", "femforge_workbench")
DIGEST_CHARS = 16


def source_hash(root: Path) -> str:
    """The hash version.json must carry: sha256 over the sorted (posix path, LF bytes) of every input, first 16 hex characters."""
    js = root / "js"
    serve = root / SERVE
    files = [path for name in HASHED_DIRS for path in (js / name).rglob("*") if path.is_file()]
    files += [path for path in (root / SCANNED).rglob("*.py") if path.is_file() and serve not in path.parents]
    files += [js / name for name in HASHED_FILES]

    digest = hashlib.sha256()
    for relative in sorted(Path(os.path.relpath(path, js)).as_posix() for path in files):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((js / relative).read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")

    return digest.hexdigest()[:DIGEST_CHARS]


class ClientDistHook(BuildHookInterface[BuilderConfig]):
    """Checks the client dist before hatchling packs it."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        """Raises unless every dist file exists and version.json carries the current source hash; editable installs skip the check."""
        if version == "editable":
            return

        root = Path(self.root)
        missing = [name for name in REQUIRED if not (root / SERVE / name).is_file()]
        if missing:
            raise RuntimeError(f"The client dist lacks {', '.join(missing)}: {HINT}.")

        manifest = cast("object", json.loads((root / SERVE / "version.json").read_text(encoding="utf-8")))
        built = cast("dict[str, object]", manifest).get("hash") if isinstance(manifest, dict) else None
        expected = source_hash(root)
        if built != expected:
            raise RuntimeError(f"The client dist is stale (version.json {built}, sources {expected}): {HINT}.")
