"""The figure panel routes plotly events to handlers once its plot is built."""

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import plotly.graph_objects as go
import pytest


pytest.importorskip("trame")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Workbench  # noqa: E402
from femforge_workbench.panels.figure import FIGURE_KEY, FigurePanel  # noqa: E402
from femforge_workbench.persist import LocalJsonStore  # noqa: E402


def _panel(name: str, root: Path) -> FigurePanel:
    app = Workbench(name, title="Figure", server=get_server(f"figure-{name}"), persist=LocalJsonStore(root), user="tester")

    return FigurePanel(app, "figure", "Figure")


def test_bind_routes_the_event_payload_to_the_handler(tmp_path: Path) -> None:
    panel = _panel("bind", tmp_path)
    heard: list[object] = []

    panel.bind("click", heard.append)

    assert panel.plot is not None
    node = cast("Mapping[str, object]", panel.plot.html)
    props = cast("Mapping[str, Mapping[str, Mapping[str, object]]]", node["props"])
    assert props["onClick"]["callback"]["args"] == {"js": "[$event]"}
    assert heard.append in panel.bound


def test_bind_without_a_plot_does_nothing(tmp_path: Path) -> None:
    panel = _panel("unbuilt", tmp_path)
    panel.plot = None

    panel.bind("relayout", print)

    assert print not in panel.bound


def test_update_swaps_the_figure_in_the_state(tmp_path: Path) -> None:
    panel = _panel("update", tmp_path)

    panel.update(go.Figure(layout={"title": {"text": "swapped"}}))

    document = cast("Mapping[str, Mapping[str, Mapping[str, object]]]", panel.state[panel.key(FIGURE_KEY)])
    assert document["layout"]["title"] == {"text": "swapped"}
