"""Controls that appear with the setting they depend on."""

from typing import Unpack

from femforge_workbench.widgets.element import Element, Prop, Shared


class Reveal(Element):
    """Expands its children into place while `open_` holds and collapses them away when it ends; `classes` lay out the children."""

    def __init__(self, children: object = None, *, open_: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the reveal; a prop left None keeps the component's default.

        Args:
            children: The controls that depend on the setting.
            open_: Whether the setting they depend on holds.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-reveal", locals(), children, **kwargs)
