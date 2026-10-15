"""The switch."""

from typing import Literal, Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import Element, Event, Prop, Shared, setter


type SwitchSize = Literal["default", "sm"]


class Switch(Element):
    """An on/off switch; `model` binds its checked state to a state variable."""

    def __init__(
        self,
        *,
        model: str | None = None,
        checked: Prop[bool] = None,
        default_checked: Prop[bool] = None,
        on_checked_change: Event = None,
        size: Prop[SwitchSize] = None,
        disabled: Prop[bool] = None,
        name: Prop[str] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the switch; a prop left None keeps the component's default.

        Args:
            model: A state variable to bind the checked state to, unless `on_checked_change` is given.
            checked: Whether the switch is on.
            default_checked: Whether the switch is on at mount while `checked` is unset.
            on_checked_change: Receives the new checked state.
            size: The switch's size.
            disabled: Disables the switch.
            name: The form field name.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None and on_checked_change is None:
            checked = react.Bind(model)
            on_checked_change = setter(model)

        super().__init__("ff-switch", locals(), None, **kwargs)
