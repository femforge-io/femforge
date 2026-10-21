"""The Ff client module: the built dist under serve/, its trame descriptor and the URLs of its files."""

import json
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import cast

from trame_server import Server

from femforge_workbench.widgets.dist_hash import source_hash


ROUTE = "__ff"
SERVE_DIR = Path(__file__).with_name("serve")
PACKAGE_ROOT = SERVE_DIR.parents[3]
"""The package directory holding js/ in a source checkout; an installed wheel has none."""
VERSION_FILE = "version.json"
LIBRARY = "Ff"
"""The window global whose install(registerTag) registers the tags."""

BUILD_HINT = "run `npm run build` under packages/femforge-workbench/js"


class VersionKey(StrEnum):
    """The fields of version.json."""

    HASH = "hash"
    BUNDLE = "bundle"
    CSS = "css"


@cache
def version() -> dict[str, str]:
    """The dist's version.json: the source hash and the bundle and stylesheet names."""
    path = SERVE_DIR / VERSION_FILE
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing: {BUILD_HINT}")

    return cast("dict[str, str]", json.loads(path.read_text(encoding="utf-8")))


def stale() -> tuple[str, str] | None:
    """The built and the source hash when a source checkout's dist is older than its sources; None for a current dist or a wheel."""
    if not (PACKAGE_ROOT / "js" / "src").is_dir():
        return None

    built = version()[VersionKey.HASH]
    expected = source_hash(PACKAGE_ROOT)

    return None if built == expected else (built, expected)


def asset_url(name: str) -> str:
    """The URL of a file under the served directory."""
    return f"/{ROUTE}/{name}"


def served(name: str) -> str:
    """The route-relative URL of a built file with the source hash as its cache buster."""
    return f"{ROUTE}/{name}?v={version()[VersionKey.HASH]}"


def assets() -> list[str]:
    """The absolute URLs of the bundle and the stylesheet as the page loads them."""
    return [f"/{served(version()[key])}" for key in (VersionKey.BUNDLE, VersionKey.CSS)]


@cache
def descriptor() -> dict[str, object]:
    """The trame module: the served directory, the bundle and stylesheet to load, and the global to install."""
    info = version()

    return {
        "serve": {ROUTE: str(SERVE_DIR)},
        "scripts": [served(info[VersionKey.BUNDLE])],
        "styles": [served(info[VersionKey.CSS])],
        "react_use": [LIBRARY],
    }


def initialize(server: Server) -> None:
    """Enables the module before the first client connects."""
    _ = server.enable_module(descriptor())
