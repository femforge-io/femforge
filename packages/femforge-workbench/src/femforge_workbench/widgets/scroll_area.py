"""Scroll areas: a viewport with styled scrollbars."""

from typing import Literal, Unpack

from femforge_workbench.widgets.element import Element, Orientation, Prop, Shared


type ScrollAreaType = Literal["always", "auto", "hover", "scroll"]


class ScrollArea(Element):
    """A scrolling viewport; `type_` says when its bars show."""

    def __init__(self, children: object = None, *, type_: Prop[ScrollAreaType] = None, scroll_hide_delay: Prop[float] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the scroll area; a prop left None keeps the component's default.

        Args:
            children: The scrolled content.
            type_: When the scrollbars show.
            scroll_hide_delay: How long the scrollbars stay after scrolling stops, in milliseconds.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-scroll-area", locals(), children, **kwargs)


class ScrollBar(Element):
    """A scrollbar of the area along `orientation`."""

    def __init__(self, *, orientation: Prop[Orientation] = None, force_mount: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the scrollbar; a prop left None keeps the component's default.

        Args:
            orientation: The direction the bar scrolls.
            force_mount: Keeps the bar mounted while it is hidden.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-scroll-bar", locals(), None, **kwargs)
