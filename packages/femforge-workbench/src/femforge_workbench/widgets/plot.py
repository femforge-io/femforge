"""The plot: a plotly figure reporting restyle, relayout and click events."""

from collections.abc import Mapping
from typing import Unpack

from femforge_workbench.widgets.element import Element, Event, Prop, Shared


class Plot(Element):
    """A figure whose `figure` holds data, layout and config; `on_click` (shared) carries the picked points."""

    def __init__(self, *, figure: Prop[Mapping[str, object]] = None, on_restyle: Event = None, on_relayout: Event = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the plot; a prop left None keeps the component's default.

        Args:
            figure: The plotly figure as JSON: data, layout and config.
            on_restyle: Receives `[update, trace_indices]` after a trace changed, as when the legend toggles one.
            on_relayout: Receives the changed layout keys after a zoom, a pan or a resize.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-plot", locals(), None, **kwargs)
