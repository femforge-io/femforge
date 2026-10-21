"""The source hash a built client dist records in version.json, shared by the build hook and the startup check."""

import hashlib
import os
from pathlib import Path


SERVE = Path("src", "femforge_workbench", "widgets", "serve")
HASHED_DIRS = ("src", "public", "scripts")
HASHED_FILES = ("package.json", "package-lock.json", "vite.config.ts", "components.json", "tsconfig.json")
SCANNED = Path("src", "femforge_workbench")
DIGEST_CHARS = 16


def source_hash(root: Path) -> str:
    """The hash version.json must carry for the package at ``root``: sha256 over the sorted (posix path, LF bytes) of every input, first 16 hex characters."""
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
