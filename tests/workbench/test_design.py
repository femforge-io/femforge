"""The checks of packages/femforge-workbench/DESIGN.md that a machine can run."""

import re
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from femforge_workbench.persist import LocalJsonStore
from tests.workbench.tree import nodes, props


pytest.importorskip("trame")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Workbench  # noqa: E402
from femforge_workbench.dock import Split  # noqa: E402
from femforge_workbench.registry import WorkbenchKind, resolve  # noqa: E402


PACKAGE = Path(__file__).parents[2] / "packages" / "femforge-workbench"
SOURCES = [
    *(PACKAGE / "js" / "src").rglob("*.ts"),
    *(PACKAGE / "js" / "src").rglob("*.tsx"),
    *(PACKAGE / "src" / "femforge_workbench").glob("**/*.py"),
]
SERVE = PACKAGE / "src" / "femforge_workbench" / "widgets" / "serve"
STYLESHEET = PACKAGE / "js" / "src" / "styles.css"
COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b(?!\])|oklch\(|rgba?\(\s*[0-9]|hsla?\(")
PALETTE_CLASS = re.compile(r"\b(?:bg|text|border)-(?:white|black|\[#)|-(?:red|green|blue|gray|zinc|slate)-[0-9]")
# React and trame render these tags from components; a lowercase tag is a plain HTML element.
COMPONENT = re.compile(r"^(?:ff-|trame-|vtk-|React|remote-)")


def sources() -> list[Path]:
    return [path for path in SOURCES if SERVE not in path.parents and "node_modules" not in path.parts]


def offending(pattern: re.Pattern[str], skip: Callable[[Path], bool] = lambda _: False) -> list[str]:
    return [
        f"{path.relative_to(PACKAGE)}:{number}: {line.strip()}"
        for path in sources()
        if not skip(path)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]


def test_no_transition_lists_all_properties() -> None:
    assert offending(re.compile(r"transition-all")) == [], "transition-all also animates visibility, which keeps a hidden dock panel painted"


def test_colors_come_only_from_the_stylesheet_tokens() -> None:
    assert offending(COLOR, skip=lambda path: path == STYLESHEET) == []


def test_no_raw_palette_class() -> None:
    assert offending(PALETTE_CLASS) == []


def _trees(name: str, root: Split, tmp_path: Path) -> list[object]:
    app = Workbench(name, title=name, server=get_server(f"design-{name}", client_type="react"), persist=LocalJsonStore(tmp_path / name), user="tester")
    app.add(root)
    templates = [value for key, value in app.state.to_dict().items() if key.startswith("trame__template_")]

    return [app.ui.html, *templates]


def test_no_plain_element_carries_a_native_title_tooltip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEMFORGE_TELEMETRY", str(tmp_path / "telemetry"))
    catalogue = Workbench("design-catalogue", title="Catalogue", server=get_server("design-catalogue", client_type="react"), persist=LocalJsonStore(tmp_path / "catalogue"), user="tester").catalogue
    trees = [tree for spec in catalogue if spec.kind is not WorkbenchKind.SCRIPT for tree in _trees(spec.name, cast("Callable[[], Split]", resolve(spec.target))(), tmp_path)]

    titled = [str(node.get("tag")) for tree in trees for node in nodes(tree) if not COMPONENT.match(str(node.get("tag"))) and "title" in props(node)]

    assert trees and titled == [], "a tooltip is the Tip component, never the browser's title"
