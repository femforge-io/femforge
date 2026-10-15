"""Dropdown menus: a Radix menu and its trigger, content, items and groups."""

from typing import Literal, Unpack

from femforge_workbench.widgets.element import Align, Element, Event, Prop, Shared, Side


type MenuItemVariant = Literal["default", "destructive"]


class DropdownMenu(Element):
    """A menu opened by its trigger."""

    def __init__(
        self,
        children: object = None,
        *,
        open_: Prop[bool] = None,
        default_open: Prop[bool] = None,
        on_open_change: Event = None,
        modal: Prop[bool] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the menu; a prop left None keeps the component's default.

        Args:
            children: The menu's trigger and content.
            open_: Whether the menu is open.
            default_open: Whether the menu opens at mount while `open_` is unset.
            on_open_change: Receives the new open state.
            modal: Blocks the page outside the menu while it is open.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dropdown-menu", locals(), children, **kwargs)


class DropdownMenuTrigger(Element):
    """The control opening the menu; `as_child` renders the child element itself."""

    def __init__(self, children: object = None, *, as_child: Prop[bool] = None, disabled: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the trigger; a prop left None keeps the component's default.

        Args:
            children: The trigger's content.
            as_child: Renders the child element itself as the trigger.
            disabled: Disables the trigger.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dropdown-menu-trigger", locals(), children, **kwargs)


class DropdownMenuContent(Element):
    """The floating list of items."""

    def __init__(
        self,
        children: object = None,
        *,
        side: Prop[Side] = None,
        align: Prop[Align] = None,
        side_offset: Prop[float] = None,
        align_offset: Prop[float] = None,
        loop: Prop[bool] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the list; a prop left None keeps the component's default.

        Args:
            children: The menu's items.
            side: The side of the trigger the list opens on.
            align: How the list aligns against the trigger.
            side_offset: The gap between the trigger and the list, in pixels.
            align_offset: The shift along the aligned edge, in pixels.
            loop: Whether keyboard navigation wraps from the last item to the first.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dropdown-menu-content", locals(), children, **kwargs)


class DropdownMenuItem(Element):
    """One item; `on_select` fires when it is chosen."""

    def __init__(
        self,
        children: object = None,
        *,
        on_select: Event = None,
        disabled: Prop[bool] = None,
        inset: Prop[bool] = None,
        variant: Prop[MenuItemVariant] = None,
        text_value: Prop[str] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the item; a prop left None keeps the component's default.

        Args:
            children: The item's label.
            on_select: Fires when the item is chosen.
            disabled: Disables the item.
            inset: Indents the item to line up with items that carry an icon.
            variant: The item's style.
            text_value: The text typeahead matches, for a label that is not plain text.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dropdown-menu-item", locals(), children, **kwargs)


class DropdownMenuCheckboxItem(Element):
    """An item with a check mark."""

    def __init__(
        self,
        children: object = None,
        *,
        checked: Prop[bool] = None,
        on_checked_change: Event = None,
        on_select: Event = None,
        disabled: Prop[bool] = None,
        text_value: Prop[str] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the item; a prop left None keeps the component's default.

        Args:
            children: The item's label.
            checked: Whether the item is checked.
            on_checked_change: Receives the new checked state.
            on_select: Fires when the item is chosen.
            disabled: Disables the item.
            text_value: The text typeahead matches, for a label that is not plain text.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dropdown-menu-checkbox-item", locals(), children, **kwargs)


class DropdownMenuRadioGroup(Element):
    """Radio items choosing one value."""

    def __init__(self, children: object = None, *, value: Prop[str] = None, on_value_change: Event = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the group; a prop left None keeps the component's default.

        Args:
            children: The radio items.
            value: The chosen item's value.
            on_value_change: Receives the chosen value.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dropdown-menu-radio-group", locals(), children, **kwargs)


class DropdownMenuRadioItem(Element):
    """One radio item."""

    def __init__(self, children: object = None, *, value: Prop[str] = None, disabled: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the item; a prop left None keeps the component's default.

        Args:
            children: The item's label.
            value: The value the item chooses.
            disabled: Disables the item.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dropdown-menu-radio-item", locals(), children, **kwargs)


class DropdownMenuLabel(Element):
    """A heading inside the menu."""

    def __init__(self, children: object = None, *, inset: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the heading; a prop left None keeps the component's default.

        Args:
            children: The heading's text.
            inset: Indents the heading to line up with items that carry an icon.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dropdown-menu-label", locals(), children, **kwargs)


class DropdownMenuSeparator(Element):
    """A rule between items."""

    def __init__(self, **kwargs: Unpack[Shared]) -> None:
        """Creates the rule."""
        super().__init__("ff-dropdown-menu-separator", locals(), None, **kwargs)


class DropdownMenuShortcut(Element):
    """The key hint at an item's end."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the key hint from `children`."""
        super().__init__("ff-dropdown-menu-shortcut", locals(), children, **kwargs)


class DropdownMenuGroup(Element):
    """Items under one label."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the group around `children`."""
        super().__init__("ff-dropdown-menu-group", locals(), children, **kwargs)


class DropdownMenuSub(Element):
    """A nested menu."""

    def __init__(self, children: object = None, *, open_: Prop[bool] = None, default_open: Prop[bool] = None, on_open_change: Event = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the nested menu; a prop left None keeps the component's default.

        Args:
            children: The nested menu's trigger and content.
            open_: Whether the nested menu is open.
            default_open: Whether the nested menu opens at mount while `open_` is unset.
            on_open_change: Receives the new open state.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dropdown-menu-sub", locals(), children, **kwargs)


class DropdownMenuSubTrigger(Element):
    """The item opening a nested menu."""

    def __init__(self, children: object = None, *, inset: Prop[bool] = None, disabled: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the item; a prop left None keeps the component's default.

        Args:
            children: The item's label.
            inset: Indents the item to line up with items that carry an icon.
            disabled: Disables the item.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dropdown-menu-sub-trigger", locals(), children, **kwargs)


class DropdownMenuSubContent(Element):
    """The nested menu's list."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the nested list around `children`."""
        super().__init__("ff-dropdown-menu-sub-content", locals(), children, **kwargs)
