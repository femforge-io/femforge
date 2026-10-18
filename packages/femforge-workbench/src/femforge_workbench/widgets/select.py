"""Selects: a Radix select and its trigger, value, content and items."""

from typing import Literal, Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import Align, Element, Event, Prop, Shared, Side, setter


type SelectTriggerSize = Literal["default", "sm"]
type SelectPosition = Literal["item-aligned", "popper"]


class Select(Element):
    """A single-choice select; `model` binds its value to a state variable."""

    def __init__(
        self,
        children: object = None,
        *,
        model: str | None = None,
        value: Prop[str] = None,
        default_value: Prop[str] = None,
        on_value_change: Event = None,
        open_: Prop[bool] = None,
        default_open: Prop[bool] = None,
        on_open_change: Event = None,
        disabled: Prop[bool] = None,
        required: Prop[bool] = None,
        name: Prop[str] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the select; a prop left None keeps the component's default.

        Args:
            children: The select's trigger and content.
            model: A state variable to bind the value to, unless `on_value_change` is given.
            value: The chosen item's value.
            default_value: The value at mount while `value` is unset.
            on_value_change: Receives the chosen value.
            open_: Whether the dropdown is open.
            default_open: Whether the dropdown opens at mount while `open_` is unset.
            on_open_change: Receives the new open state.
            disabled: Disables the select.
            required: Marks the select as required in a form.
            name: The form field name.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None and on_value_change is None:
            value = react.Bind(model)
            on_value_change = setter(model)

        super().__init__("ff-select", locals(), children, **kwargs)


class SelectTrigger(Element):
    """The button showing the chosen value."""

    def __init__(
        self,
        children: object = None,
        *,
        size: Prop[SelectTriggerSize] = None,
        disabled: Prop[bool] = None,
        aria_label: Prop[str] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the trigger; a prop left None keeps the component's default.

        Args:
            children: The trigger's content, usually a SelectValue.
            size: The trigger's height.
            disabled: Disables the trigger.
            aria_label: The trigger's accessible name, for a select whose caption is not its label element.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-select-trigger", locals(), children, **kwargs)


class SelectValue(Element):
    """The chosen value inside the trigger, or `placeholder` while none is."""

    def __init__(self, *, placeholder: Prop[str] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the value, showing `placeholder` while nothing is chosen."""
        super().__init__("ff-select-value", locals(), None, **kwargs)


class SelectContent(Element):
    """The dropdown holding the items."""

    def __init__(
        self,
        children: object = None,
        *,
        position: Prop[SelectPosition] = None,
        side: Prop[Side] = None,
        align: Prop[Align] = None,
        side_offset: Prop[float] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the dropdown; a prop left None keeps the component's default.

        Args:
            children: The select's items.
            position: Whether the list sits over the chosen item or floats beside the trigger.
            side: The side of the trigger the list opens on, in popper position.
            align: How the list aligns against the trigger, in popper position.
            side_offset: The gap between the trigger and the list in pixels, in popper position.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-select-content", locals(), children, **kwargs)


class SelectItem(Element):
    """One option; `children` is its label."""

    def __init__(
        self,
        children: object = None,
        *,
        value: Prop[str] = None,
        disabled: Prop[bool] = None,
        text_value: Prop[str] = None,
        detail: Prop[list[str]] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the option; a prop left None keeps the component's default.

        Args:
            children: The option's label.
            value: The value the option chooses.
            disabled: Disables the option.
            text_value: The text typeahead matches, for a label that is not plain text.
            detail: Muted parts after the label, never copied into the trigger nor matched on typeahead.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-select-item", locals(), children, **kwargs)


class SelectGroup(Element):
    """Items under one label."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the group around `children`."""
        super().__init__("ff-select-group", locals(), children, **kwargs)


class SelectLabel(Element):
    """The label of a group."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the label from `children`."""
        super().__init__("ff-select-label", locals(), children, **kwargs)


class SelectSeparator(Element):
    """A rule between items."""

    def __init__(self, **kwargs: Unpack[Shared]) -> None:
        """Creates the rule."""
        super().__init__("ff-select-separator", locals(), None, **kwargs)
