"""The accordion: collapsible sections, one or several open at a time."""

from collections.abc import Sequence
from typing import Literal, Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import Element, Event, Prop, Shared, setter


type AccordionType = Literal["multiple", "single"]


class Accordion(Element):
    """Sections opened one at a time (`type_` single) or several; `model` binds the open ids to a state variable."""

    # Radix declares `collapsible` only in the single half of its union and the emitted contract drops it, so a lone section that must close again belongs in a `multiple` accordion.

    def __init__(
        self,
        children: object = None,
        *,
        model: str | None = None,
        type_: Prop[AccordionType] = None,
        value: Prop[str | Sequence[str]] = None,
        default_value: Prop[str | Sequence[str]] = None,
        on_value_change: Event = None,
        disabled: Prop[bool] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the accordion; a prop left None keeps the component's default.

        Args:
            children: The accordion's sections.
            model: A state variable to bind the open section ids to, unless `on_value_change` is given.
            type_: Whether one section opens at a time or several.
            value: The open section's id, or the open ids of a multiple accordion.
            default_value: The sections open at mount while `value` is unset.
            on_value_change: Receives the open ids after a section opens or closes.
            disabled: Disables every section.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None and on_value_change is None:
            value = react.Bind(model)
            on_value_change = setter(model)

        super().__init__("ff-accordion", locals(), children, **kwargs)


class AccordionItem(Element):
    """One section, named by `value`."""

    def __init__(self, children: object = None, *, value: Prop[str] = None, disabled: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the section; a prop left None keeps the component's default.

        Args:
            children: The section's trigger and content.
            value: The section's id.
            disabled: Disables the section.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-accordion-item", locals(), children, **kwargs)


class AccordionTrigger(Element):
    """The header that opens and closes a section."""

    def __init__(self, children: object = None, *, disabled: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the trigger; a prop left None keeps the component's default.

        Args:
            children: The header's text.
            disabled: Disables the trigger.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-accordion-trigger", locals(), children, **kwargs)


class AccordionContent(Element):
    """The body of a section."""

    def __init__(self, children: object = None, *, force_mount: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the section body; a prop left None keeps the component's default.

        Args:
            children: The section's content.
            force_mount: Keeps the content mounted while the section is closed.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-accordion-content", locals(), children, **kwargs)
