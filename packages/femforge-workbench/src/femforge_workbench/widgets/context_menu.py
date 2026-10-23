"""Context menus: a Radix menu a right click opens over its trigger, and its items."""

from typing import Unpack

from femforge_workbench.widgets.element import Element, Event, Prop, Shared


class ContextMenu(Element):
    """A menu a right click on its trigger opens at the pointer."""

    def __init__(self, children: object = None, *, on_open_change: Event = None, modal: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the menu; a prop left None keeps the component's default.

        Args:
            children: The menu's trigger and content.
            on_open_change: Receives the new open state.
            modal: Blocks the page outside the menu while it is open.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-context-menu", locals(), children, **kwargs)


class ContextMenuTrigger(Element):
    """The area a right click opens the menu over; `as_child` makes the child element itself that area."""

    def __init__(
        self,
        children: object = None,
        *,
        as_child: Prop[bool] = None,
        disabled: Prop[bool] = None,
        drag_tolerance: Prop[float] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the trigger; a prop left None keeps the component's default.

        Args:
            children: The area's content.
            as_child: Makes the child element itself the area.
            disabled: Leaves the browser's own menu to a right click.
            drag_tolerance: How far, in pixels, a right press may move and still open the menu; None opens it after any drag.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-context-menu-trigger", locals(), children, **kwargs)


class ContextMenuContent(Element):
    """The floating list of items."""

    def __init__(self, children: object = None, *, loop: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the list; a prop left None keeps the component's default.

        Args:
            children: The menu's items.
            loop: Whether keyboard navigation wraps from the last item to the first.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-context-menu-content", locals(), children, **kwargs)


class ContextMenuItem(Element):
    """One item; `on_select` fires when it is chosen."""

    def __init__(
        self,
        children: object = None,
        *,
        on_select: Event = None,
        disabled: Prop[bool] = None,
        inset: Prop[bool] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the item; a prop left None keeps the component's default.

        Args:
            children: The item's label.
            on_select: Fires when the item is chosen.
            disabled: Disables the item.
            inset: Indents the item to line up with a checkbox item's label.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-context-menu-item", locals(), children, **kwargs)


class ContextMenuCheckboxItem(Element):
    """An item with a check mark."""

    def __init__(
        self,
        children: object = None,
        *,
        checked: Prop[bool] = None,
        on_checked_change: Event = None,
        disabled: Prop[bool] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the item; a prop left None keeps the component's default.

        Args:
            children: The item's label.
            checked: Whether the item is checked.
            on_checked_change: Receives the new checked state.
            disabled: Disables the item.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-context-menu-checkbox-item", locals(), children, **kwargs)


class ContextMenuSeparator(Element):
    """A rule between items."""

    def __init__(self, **kwargs: Unpack[Shared]) -> None:
        """Creates the rule."""
        super().__init__("ff-context-menu-separator", locals(), None, **kwargs)
