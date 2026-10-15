"""The progress bar."""

from typing import Unpack

from femforge_workbench.widgets.element import Element, Prop, Shared


class Progress(Element):
    """A determinate bar filled to `value` out of `max_`."""

    def __init__(self, *, value: Prop[float] = None, max_: Prop[float] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the bar; a prop left None keeps the component's default.

        Args:
            value: How far the bar is filled.
            max_: The value of a full bar.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-progress", locals(), None, **kwargs)
