"""The built-in demo workbench: a sphere in the scene, a line figure and a grouped table, exercising the shell without lab data."""

from enum import StrEnum
from typing import TYPE_CHECKING

import numpy as np
import plotly.graph_objects as go
import polars as pl
import pyvista as pv

from femforge_workbench.dock import Panel, Split
from femforge_workbench.grid import group_rows
from femforge_workbench.panels import labelled
from femforge_workbench.panels.figure import FigurePanel
from femforge_workbench.panels.scene import ScenePanel
from femforge_workbench.panels.table import TablePanel
from femforge_workbench.registry import DEMO
from femforge_workbench.theme import Palette
from femforge_workbench.widgets.select import Select, SelectContent, SelectItem, SelectTrigger, SelectValue


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


SPEC = DEMO
"""The catalogue card the workbench entry point exposes."""

ROWS = 100
SERIES = ("alpha", "beta", "gamma", "delta", "epsilon")
SERIES_COLUMN = "series"
ALL_SERIES = "all"
SAMPLES = 400
SEED = 7


class DemoPanel(StrEnum):
    """The demo's panel ids."""

    SPHERE = "sphere"
    SIGNAL = "signal"
    ROWS = "rows"


class DemoVar(StrEnum):
    """The demo's state variables."""

    SERIES = "demo_series"


class Sphere:
    """A Renderable: a faceted sphere beside a smaller smooth one, painted from the palette."""

    def __init__(self, palette: Palette) -> None:
        """Takes the palette the spheres are painted from."""
        self.palette = palette

    def render(self, plotter: pv.Plotter) -> None:
        """Draws the two spheres into `plotter` and fits the camera."""
        big, small = self.palette.colorway(2)
        plotter.clear()
        _ = plotter.add_mesh(pv.Sphere(radius=1.0), color=big, show_edges=True, edge_color=self.palette.grid)  # pyright: ignore[reportUnknownMemberType]  # pyvista's add_mesh and Sphere are partially untyped
        _ = plotter.add_mesh(pv.Sphere(radius=0.4, center=(1.6, 0.0, 0.0)), color=small, smooth_shading=True)  # pyright: ignore[reportUnknownMemberType]
        plotter.renderer.ResetCamera()


def signal_figure() -> go.Figure:
    """Two damped sinusoids named for export through layout.meta.title."""
    x = np.linspace(0.0, 4.0 * np.pi, SAMPLES)
    damping = np.exp(-x / 8.0)
    figure = go.Figure([go.Scatter(x=x, y=np.sin(x) * damping, name="sin"), go.Scatter(x=x, y=np.cos(x) * damping, name="cos")])
    _ = figure.update_layout(meta={"title": "Damped signal"}, uirevision="demo", legend={"orientation": "h"})

    return figure


def rows_frame() -> pl.DataFrame:
    """A hundred rows over five series with a group label carrying the count and the peak y."""
    rng = np.random.default_rng(SEED)
    index = np.arange(ROWS)
    x = index / 10.0
    frame = pl.DataFrame(
        {
            "index": index,
            SERIES_COLUMN: [SERIES[i % len(SERIES)] for i in range(ROWS)],
            "x": x,
            "y": np.sin(x) + rng.normal(0.0, 0.1, ROWS),
            "z": x * x,
        }
    )

    return group_rows(frame, SERIES_COLUMN, max_=["y"])


def series_rows(frame: pl.DataFrame, selected: str) -> pl.DataFrame:
    """The grouped rows of one series, or all of them."""
    if selected == ALL_SERIES:
        return frame

    return frame.filter(pl.col(SERIES_COLUMN) == selected)


def workbench() -> Split:
    """The demo dock: the sphere on the left, the figure over the table on the right, with a series filter in the app bar."""
    frame = rows_frame()

    def sphere(app: "Workbench", id: str, title: str) -> ScenePanel:
        return ScenePanel(app, id, title, renderable=Sphere(app.palette))

    def signal(app: "Workbench", id: str, title: str) -> FigurePanel:
        return FigurePanel(app, id, title, signal_figure())

    def rows(app: "Workbench", id: str, title: str) -> TablePanel:
        table = TablePanel(app, id, title, frame, grouped=True, filterable=True, lead=(SERIES_COLUMN,))
        _ = app.state.setdefault(DemoVar.SERIES, ALL_SERIES)

        with app.header, labelled("Series"), Select(model=DemoVar.SERIES):
            with SelectTrigger(size="sm", classes="w-28"):
                SelectValue()

            with SelectContent():
                for name in (ALL_SERIES, *SERIES):
                    SelectItem(name, value=name)

        @table.listen(str(DemoVar.SERIES))
        def on_series(**_: object) -> None:
            table.set_rows(series_rows(frame, str(app.state[DemoVar.SERIES])))

        return table

    return Split(
        [
            Panel("Sphere", sphere, id=DemoPanel.SPHERE, weight=1.2),
            Split([Panel("Signal", signal, id=DemoPanel.SIGNAL), Panel("Rows", rows, id=DemoPanel.ROWS)]),
        ]
    )
