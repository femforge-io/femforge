"""The checkbox."""

from typing import Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import Element, Event, Prop, Shared, setter


class Checkbox(Element):
    """A checkbox; `model` binds its checked state to a state variable."""

    def __init__(
        self,
        *,
        model: str | None = None,
        checked: Prop[bool] = None,
        default_checked: Prop[bool] = None,
        on_checked_change: Event = None,
        disabled: Prop[bool] = None,
        required: Prop[bool] = None,
        name: Prop[str] = None,
        value: Prop[str] = None,
        aria_label: Prop[str] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the checkbox; a prop left None keeps the component's default.

        Args:
            model: A state variable to bind the checked state to, unless `on_checked_change` is given.
            checked: Whether the box is checked.
            default_checked: Whether the box is checked at mount while `checked` is unset.
            on_checked_change: Receives the new checked state.
            disabled: Disables the checkbox.
            required: Marks the checkbox as required in a form.
            name: The form field name.
            value: The value a form submits while the box is checked.
            aria_label: The accessible name.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None and on_checked_change is None:
            checked = react.Bind(model)
            on_checked_change = setter(model)

        super().__init__("ff-checkbox", locals(), None, **kwargs)
