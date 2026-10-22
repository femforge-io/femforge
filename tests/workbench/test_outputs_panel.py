"""The outputs panel shows images, picks an editor language per suffix, tails long text, and refuses unserved paths."""

from pathlib import Path

import pytest

from femforge_workbench.registry import OutputSpec, script


pytest.importorskip("trame")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Workbench  # noqa: E402
from femforge_workbench.panels.outputs import StateVar, Viewer, WorkbenchOutputsPanel, language, read_tail  # noqa: E402
from femforge_workbench.persist import LocalJsonStore  # noqa: E402


def _panel(name: str, root: Path, outputs: list[OutputSpec]) -> WorkbenchOutputsPanel:
    app = Workbench(name, title="Outputs", server=get_server(f"outputs-{name}"), persist=LocalJsonStore(root), user="tester")
    spec = script(name, name.title(), target=f"lab.{name}", outputs=outputs)
    panel = WorkbenchOutputsPanel(app, "outputs", "Outputs", catalogue=[spec])
    panel.show(spec)

    return panel


def test_an_image_output_is_served_into_the_image_viewer(tmp_path: Path) -> None:
    image = tmp_path / "plot.png"
    _ = image.write_bytes(b"\x89PNG")

    panel = _panel("image", tmp_path, [OutputSpec.at(image)])

    assert panel.state[panel.key(StateVar.VIEWER)] == Viewer.IMAGE
    assert panel.state[panel.key(StateVar.URL)] == "/wb-image/plot.png"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.py", "python"), ("a.yaml", "yaml"), ("a.yml", "yaml"), ("a.toml", "ini"), ("a.cfg", "ini"), ("a.txt", "plaintext")],
)
def test_language_follows_the_suffix(name: str, expected: str) -> None:
    assert language(Path(name)) == expected


def test_read_tail_keeps_only_the_last_bytes(tmp_path: Path) -> None:
    path = tmp_path / "long.log"
    _ = path.write_bytes(b"head tail")

    assert read_tail(path, limit=4) == "tail"
    assert read_tail(path) == "head tail"


def test_a_path_outside_the_catalogue_is_not_served(tmp_path: Path) -> None:
    panel = _panel("served", tmp_path, [])
    stray = OutputSpec.at(tmp_path / "elsewhere" / "page.html")

    with pytest.raises(KeyError, match="not served"):
        _ = panel.url(stray)


def test_dispose_releases_the_hosted_table(tmp_path: Path) -> None:
    panel = _panel("dispose", tmp_path, [])
    table_listeners = panel.table.listeners

    panel.dispose()

    assert table_listeners == [] and panel.listeners == []
