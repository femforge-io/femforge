"""The icon: one of the curated lucide names."""

from typing import Literal, Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import Element, Prop, Shared


type IconName = Literal[
    "anchor",
    "app-window",
    "arrow-big-right-dash",
    "arrow-down-to-line",
    "arrow-left-to-line",
    "arrow-right-to-line",
    "arrow-up-right",
    "arrow-up-to-line",
    "axis-3d",
    "box",
    "camera",
    "check",
    "chevron-down",
    "chevron-up",
    "chevron-right",
    "circle-dashed",
    "circle-dot",
    "circle-help",
    "circle-x",
    "columns-3",
    "copy",
    "copy-check",
    "external-link",
    "eye",
    "eye-off",
    "file",
    "filter",
    "flame",
    "folder",
    "git-commit-horizontal",
    "grip-vertical",
    "info",
    "layout-grid",
    "loader-circle",
    "magnet",
    "maximize-2",
    "minimize-2",
    "minus",
    "pentagon",
    "picture-in-picture-2",
    "play",
    "plus",
    "refresh-cw",
    "scaling",
    "scan",
    "search",
    "settings",
    "sliders-horizontal",
    "terminal",
    "trash-2",
    "triangle-alert",
    "view-2d",
    "view-back",
    "view-front",
    "x",
    "zap",
]
"""The names in the dist's icons.json."""


class Icon(Element):
    """A lucide icon; an unknown name draws circle-help with a console warning."""

    def __init__(self, name: IconName | react.Bind, *, size: Prop[float] = None, color: Prop[str] = None, stroke_width: Prop[float] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the icon; a prop left None keeps the component's default.

        Args:
            name: The lucide icon's name, or a binding to one.
            size: The icon's width and height, in pixels.
            color: The stroke colour; the text colour by default.
            stroke_width: The stroke width, in pixels.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-icon", locals(), None, **kwargs)
