"""The icon: one of the curated lucide names."""

from typing import Literal, Unpack

from femforge_workbench.widgets.element import Element, Prop, Shared


type IconName = Literal[
    "anchor",
    "app-window",
    "arrow-big-right-dash",
    "box",
    "check",
    "chevron-down",
    "chevron-right",
    "circle-dot",
    "circle-help",
    "circle-x",
    "columns-3",
    "copy",
    "external-link",
    "eye",
    "eye-off",
    "file",
    "filter",
    "flame",
    "folder",
    "grip-vertical",
    "info",
    "layout-grid",
    "magnet",
    "maximize-2",
    "minimize-2",
    "minus",
    "picture-in-picture-2",
    "play",
    "plus",
    "refresh-cw",
    "search",
    "settings",
    "sliders-horizontal",
    "square",
    "terminal",
    "trash-2",
    "triangle-alert",
    "x",
    "zap",
]
"""The names in the dist's icons.json."""


class Icon(Element):
    """A lucide icon; an unknown name draws circle-help with a console warning."""

    def __init__(self, name: IconName, *, size: Prop[float] = None, color: Prop[str] = None, stroke_width: Prop[float] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the icon; a prop left None keeps the component's default.

        Args:
            name: The lucide icon's name.
            size: The icon's width and height, in pixels.
            color: The stroke colour; the text colour by default.
            stroke_width: The stroke width, in pixels.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-icon", locals(), None, **kwargs)
