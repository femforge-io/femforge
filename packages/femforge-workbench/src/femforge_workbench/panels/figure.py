"""The plotly panel: a figure that fills its dock tab, updated in place and bound to plotly events."""

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, cast

import plotly.graph_objects as go
from trame.widgets import react

from femforge_workbench.panels import PanelComponent
from femforge_workbench.plotly import export_options
from femforge_workbench.widgets.plot import Plot


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


type EventHandler = Callable[[object], None]
"""Receives the plotly event payload: `[update, trace_indices]` for restyle, `{points: [...]}` for click, and so on."""

type PlotEvent = Literal["restyle", "relayout", "click"]
"""The plotly events `bind` routes."""

type Json = dict[str, object]
"""A JSON object."""

FIGURE_KEY = "figure"
EVENT_PREFIX = "on_"


class FigurePanel(PanelComponent):
    """Shows one plotly figure; `update` swaps it (uirevision keeps brushes and cameras), `bind` routes its events to handlers."""

    def __init__(self, app: "Workbench", id: str, title: str, figure: go.Figure | None = None, *, mode_bar: bool = True) -> None:
        """Builds the panel around its first figure.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            figure: The figure shown first; None starts empty.
            mode_bar: Whether plotly's modebar shows.
        """
        self.initial = figure if figure is not None else go.Figure()
        self.mode_bar = mode_bar
        self.plot: Plot | None = None

        super().__init__(app, id, title)

    def build(self) -> None:
        """Pushes the figure into the state and builds the plot bound to it."""
        # A rebuilt panel must push its figure: a bound default only fills a variable the last build left empty.
        self.state[self.key(FIGURE_KEY)] = self.encode(self.initial)
        self.plot = Plot(figure=react.Bind(self.key(FIGURE_KEY)))

    def encode(self, figure: go.Figure) -> Json:
        """The figure as plain JSON with the modebar switch and the export name it carries."""
        document = cast("Json", json.loads(figure.to_json()))
        document["config"] = {"displayModeBar": self.mode_bar, "toImageButtonOptions": export_options(figure)}

        return document

    def update(self, figure: go.Figure) -> None:
        """Replaces the figure."""
        with self.state:
            self.state[self.key(FIGURE_KEY)] = self.encode(figure)

    def bind(self, event: PlotEvent, handler: EventHandler) -> None:
        """Routes a plotly event to `handler` with the event payload as its one argument."""
        if self.plot is None:
            return

        setattr(self.plot, f"{EVENT_PREFIX}{event}", self.callback(handler, args="[$event]"))
        self.refresh()
