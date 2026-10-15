"""Figure conventions in the dock: the tab carries the title on screen, layout.meta.title names the figure for export."""

import re
from collections.abc import Mapping
from typing import cast

import plotly.graph_objects as go

from femforge_workbench.theme import section, text


EXPORT_FORMAT = "png"
EXPORT_SCALE = 2
TITLED_TOP_MARGIN = 60
DEFAULT_NAME = "figure"

type Json = dict[str, object]


def layout_json(figure: go.Figure) -> Json:
    """The figure's layout as plain JSON."""
    encoded = cast("Json", figure.to_plotly_json())
    layout = encoded.get("layout")
    if not isinstance(layout, Mapping):
        return {}

    return dict(cast("Mapping[str, object]", layout))


def title_of(figure: go.Figure) -> str:
    """The name a figure gives itself: layout.meta.title, else its on-screen title, else empty."""
    layout = layout_json(figure)
    named = text(section(layout, "meta"), "title", "")
    if named:
        return named

    title = layout.get("title")
    plain = title if isinstance(title, str) else ""

    return text(section(layout, "title"), "text", plain)


def export_options(figure: go.Figure) -> Json:
    """The modebar's toImageButtonOptions: a 2x PNG named after the figure's title."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", title_of(figure)).strip("-")

    return {"format": EXPORT_FORMAT, "scale": EXPORT_SCALE, "filename": stem or DEFAULT_NAME}


def with_title(figure: go.Figure) -> go.Figure:
    """A copy of the figure carrying its export title centred at the top, with the margin that needs."""
    text = title_of(figure)
    titled = go.Figure(figure)
    if not text:
        return titled

    titled.update_layout(title={"text": text, "x": 0.5, "xanchor": "center"}, margin={"t": TITLED_TOP_MARGIN})

    return titled
