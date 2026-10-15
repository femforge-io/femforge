"""The combobox: a searchable single choice narrowed by group toggles."""

from collections.abc import Sequence
from typing import TypedDict, Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import Element, Event, Prop, Shared, setter


class ComboboxItem(TypedDict, total=False):
    """One option: its value, label, the groups it belongs to and an optional CSS background swatch."""

    value: str
    label: str
    groups: list[str]
    swatch: str


class Combobox(Element):
    """A searchable select whose `filters` toggle its groups on and off and whose `compact` face shows only the swatch; `model` binds its value to a state variable."""

    def __init__(
        self,
        *,
        model: str | None = None,
        items: Prop[Sequence[ComboboxItem]] = None,
        value: Prop[str] = None,
        on_value_change: Event = None,
        lead: Prop[ComboboxItem] = None,
        filters: Prop[Sequence[str]] = None,
        compact: Prop[bool] = None,
        placeholder: Prop[str] = None,
        search_placeholder: Prop[str] = None,
        empty_text: Prop[str] = None,
        disabled: Prop[bool] = None,
        aria_label: Prop[str] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the combobox; a prop left None keeps the component's default.

        Args:
            model: A state variable to bind the value to, unless `on_value_change` is given.
            items: The options.
            value: The chosen option's value.
            on_value_change: Receives the chosen value.
            lead: An option listed first, in a group of its own above the items.
            filters: The group toggles that narrow the items.
            compact: Shows only the chosen option's swatch on the trigger.
            placeholder: The trigger's text while nothing is chosen.
            search_placeholder: The search field's placeholder.
            empty_text: The text shown when no option matches the search.
            disabled: Disables the combobox.
            aria_label: The accessible name.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None and on_value_change is None:
            value = react.Bind(model)
            on_value_change = setter(model)

        super().__init__("ff-combobox", locals(), None, **kwargs)
