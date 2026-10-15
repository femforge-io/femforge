"""The separator."""

from typing import Unpack

from femforge_workbench.widgets.element import Element, Orientation, Prop, Shared


class Separator(Element):
    """A rule between content."""

    def __init__(self, *, orientation: Prop[Orientation] = None, decorative: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the rule; a prop left None keeps the component's default.

        Args:
            orientation: The direction the rule runs.
            decorative: Hides the rule from assistive technology.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-separator", locals(), None, **kwargs)
