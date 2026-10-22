"""A bar at the bottom of its parent with a body that slides up under it."""

from typing import Unpack

from femforge_workbench.widgets.element import Element, Prop, Shared


class Drawer(Element):
    """Pins its first child, the bar, to the bottom of its parent and slides the rest up under it while `open_` holds, so the bar heads the open drawer."""

    def __init__(self, children: object = None, *, open_: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the drawer; a prop left None keeps the component's default.

        Args:
            children: The bar, then the body.
            open_: Whether the body is open.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-drawer", locals(), children, **kwargs)
