"""Toggles: a pressed button and a group of them choosing one or many values."""

from collections.abc import Sequence
from typing import Literal, Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import Element, Event, Orientation, Prop, Shared, setter


type ToggleVariant = Literal["default", "outline"]
type ToggleSize = Literal["default", "lg", "sm"]
type ToggleType = Literal["multiple", "single"]


class Toggle(Element):
    """A button that stays pressed; `model` binds the pressed state to a state variable."""

    def __init__(
        self,
        children: object = None,
        *,
        model: str | None = None,
        pressed: Prop[bool] = None,
        default_pressed: Prop[bool] = None,
        on_pressed_change: Event = None,
        variant: Prop[ToggleVariant] = None,
        size: Prop[ToggleSize] = None,
        disabled: Prop[bool] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the toggle; a prop left None keeps the component's default.

        Args:
            children: The toggle's label or icon.
            model: A state variable to bind the pressed state to, unless `on_pressed_change` is given.
            pressed: Whether the toggle is pressed.
            default_pressed: Whether the toggle is pressed at mount while `pressed` is unset.
            on_pressed_change: Receives the new pressed state.
            variant: The toggle's style.
            size: The toggle's size.
            disabled: Disables the toggle.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None and on_pressed_change is None:
            pressed = react.Bind(model)
            on_pressed_change = setter(model)

        super().__init__("ff-toggle", locals(), children, **kwargs)


class ToggleGroup(Element):
    """Toggle items choosing one value (`type_` single) or a list of them; `model` binds the choice to a state variable."""

    def __init__(
        self,
        children: object = None,
        *,
        model: str | None = None,
        type_: Prop[ToggleType] = None,
        value: Prop[str | Sequence[str]] = None,
        default_value: Prop[str | Sequence[str]] = None,
        on_value_change: Event = None,
        variant: Prop[ToggleVariant] = None,
        size: Prop[ToggleSize] = None,
        orientation: Prop[Orientation] = None,
        disabled: Prop[bool] = None,
        spacing: Prop[float] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the group; a prop left None keeps the component's default.

        Args:
            children: The group's items.
            model: A state variable to bind the choice to, unless `on_value_change` is given.
            type_: Whether one item or several can be pressed.
            value: The pressed item's value, or the values of a multiple group.
            default_value: The choice at mount while `value` is unset.
            on_value_change: Receives the new choice.
            variant: The items' style.
            size: The items' size.
            orientation: The direction the items run.
            disabled: Disables every item.
            spacing: The gap between items in Tailwind spacing units; 0 joins them.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None and on_value_change is None:
            value = react.Bind(model)
            on_value_change = setter(model)

        super().__init__("ff-toggle-group", locals(), children, **kwargs)


class ToggleGroupItem(Element):
    """One item of a toggle group."""

    def __init__(
        self,
        children: object = None,
        *,
        value: Prop[str] = None,
        disabled: Prop[bool] = None,
        variant: Prop[ToggleVariant] = None,
        size: Prop[ToggleSize] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the item; a prop left None keeps the component's default.

        Args:
            children: The item's label or icon.
            value: The value the item stands for.
            disabled: Disables the item.
            variant: The item's style, over the group's.
            size: The item's size, over the group's.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-toggle-group-item", locals(), children, **kwargs)
