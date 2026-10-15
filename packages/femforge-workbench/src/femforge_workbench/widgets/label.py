"""The label."""

from typing import Unpack

from femforge_workbench.widgets.element import Element, Prop, Shared


class Label(Element):
    """A label for the control `html_for` names."""

    def __init__(self, children: object = None, *, html_for: Prop[str] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the label; a prop left None keeps the component's default.

        Args:
            children: The label's text.
            html_for: The id of the control the label names.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-label", locals(), children, **kwargs)
