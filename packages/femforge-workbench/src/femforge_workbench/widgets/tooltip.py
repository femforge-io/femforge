"""Tooltips: a provider, a tooltip and its trigger and content."""

from typing import Unpack

from femforge_workbench.widgets.element import Align, Element, Event, Prop, Shared, Side


class TooltipProvider(Element):
    """Shares the open delay across the tooltips inside it."""

    def __init__(self, children: object = None, *, delay_duration: Prop[float] = None, skip_delay_duration: Prop[float] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the provider; a prop left None keeps the component's default.

        Args:
            children: The tooltips that share the delay.
            delay_duration: How long the pointer rests on a trigger before its tooltip opens, in milliseconds.
            skip_delay_duration: How soon after a tooltip closes another opens without the delay, in milliseconds.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-tooltip-provider", locals(), children, **kwargs)


class Tooltip(Element):
    """A tooltip around its trigger."""

    def __init__(
        self,
        children: object = None,
        *,
        open_: Prop[bool] = None,
        default_open: Prop[bool] = None,
        on_open_change: Event = None,
        delay_duration: Prop[float] = None,
        disable_hoverable_content: Prop[bool] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the tooltip; a prop left None keeps the component's default.

        Args:
            children: The trigger and the content.
            open_: Whether the tooltip is open.
            default_open: Whether the tooltip opens at mount while `open_` is unset.
            on_open_change: Receives the new open state.
            delay_duration: The open delay in milliseconds, over the provider's.
            disable_hoverable_content: Closes the tooltip once the pointer leaves the trigger, even for the content.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-tooltip", locals(), children, **kwargs)


class TooltipTrigger(Element):
    """The element the tooltip describes; `as_child` renders the child element itself."""

    def __init__(self, children: object = None, *, as_child: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the trigger; a prop left None keeps the component's default.

        Args:
            children: The element the tooltip describes.
            as_child: Renders the child element itself as the trigger.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-tooltip-trigger", locals(), children, **kwargs)


class TooltipContent(Element):
    """The tooltip's bubble."""

    def __init__(self, children: object = None, *, side: Prop[Side] = None, align: Prop[Align] = None, side_offset: Prop[float] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the bubble; a prop left None keeps the component's default.

        Args:
            children: The tooltip's text.
            side: The side of the trigger the bubble opens on.
            align: How the bubble aligns against the trigger.
            side_offset: The gap between the trigger and the bubble, in pixels.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-tooltip-content", locals(), children, **kwargs)
