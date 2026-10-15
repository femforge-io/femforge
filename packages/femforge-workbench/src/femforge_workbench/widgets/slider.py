"""The slider."""

from collections.abc import Sequence
from typing import Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import Element, Event, Orientation, Prop, Shared, setter


class Slider(Element):
    """A one- or two-thumb slider over a list of values, its thumbs named after `aria_label`; `model` binds the values to a state variable."""

    def __init__(
        self,
        *,
        model: str | None = None,
        value: Prop[Sequence[float]] = None,
        default_value: Prop[Sequence[float]] = None,
        on_value_change: Event = None,
        on_value_commit: Event = None,
        min_: Prop[float] = None,
        max_: Prop[float] = None,
        step: Prop[float] = None,
        min_steps_between_thumbs: Prop[int] = None,
        orientation: Prop[Orientation] = None,
        inverted: Prop[bool] = None,
        disabled: Prop[bool] = None,
        aria_label: Prop[str] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the slider; a prop left None keeps the component's default.

        Args:
            model: A state variable to bind the values to, unless `on_value_change` is given.
            value: The thumb values, one per thumb.
            default_value: The thumb values at mount while `value` is unset.
            on_value_change: Receives the thumb values while a thumb moves.
            on_value_commit: Receives the thumb values when a thumb is released.
            min_: The low end of the range.
            max_: The high end of the range.
            step: The step between values.
            min_steps_between_thumbs: The fewest steps two thumbs keep between them.
            orientation: The direction the slider runs.
            inverted: Runs the range from high to low.
            disabled: Disables the slider.
            aria_label: The accessible name the thumbs are named after.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None and on_value_change is None:
            value = react.Bind(model)
            on_value_change = setter(model)

        super().__init__("ff-slider", locals(), None, **kwargs)
