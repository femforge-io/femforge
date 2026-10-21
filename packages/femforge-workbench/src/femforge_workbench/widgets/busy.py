"""The busy indicator of an area whose content the server is working on."""

from typing import Unpack

from femforge_workbench.widgets.element import Element, Prop, Shared


class Busy(Element):
    """A line along the top edge of its parent and a spinner on the control that started the work, shown only while `active` outlasts the busy delay."""

    def __init__(self, *, active: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the indicator; a prop left None keeps the component's default.

        Args:
            active: Whether the server is working on the parent's content.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-busy", locals(), None, **kwargs)
