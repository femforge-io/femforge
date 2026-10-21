"""The tooltip a single child carries."""

from typing import Unpack

from femforge_workbench.widgets.element import Element, Prop, Shared, Side


class Tip(Element):
    """Gives its one child a tooltip that opens on hover and on keyboard focus; empty content renders the child alone."""

    def __init__(self, children: object = None, *, content: Prop[str] = None, side: Prop[Side] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the tooltip; a prop left None keeps the component's default.

        Args:
            children: The one element the tooltip describes.
            content: The tooltip's text: what the element does or what the screen does not show.
            side: The side of the element the tooltip opens on.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-tip", locals(), children, **kwargs)
