"""Buttons: the shadcn button and button group."""

from typing import Literal, Unpack

from femforge_workbench.widgets.element import Element, Orientation, Prop, Shared


type ButtonVariant = Literal["default", "destructive", "ghost", "link", "outline", "secondary"]
type ButtonSize = Literal["default", "icon", "icon-lg", "icon-sm", "icon-xs", "lg", "sm", "xs"]
type ButtonType = Literal["button", "reset", "submit"]


class Button(Element):
    """A button whose children are its label or icon."""

    def __init__(
        self,
        children: object = None,
        *,
        variant: Prop[ButtonVariant] = None,
        size: Prop[ButtonSize] = None,
        disabled: Prop[bool] = None,
        type_: Prop[ButtonType] = None,
        as_child: Prop[bool] = None,
        aria_label: Prop[str] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the button; a prop left None keeps the component's default.

        Args:
            children: The button's label or icon.
            variant: The button's style.
            size: The button's size; the icon sizes make it square.
            disabled: Disables the button.
            type_: The HTML button type.
            as_child: Renders the child element itself, styled as the button.
            aria_label: The accessible name, needed when the button shows only an icon.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-button", locals(), children, **kwargs)


class ButtonGroup(Element):
    """Buttons joined into one row or column."""

    def __init__(self, children: object = None, *, orientation: Prop[Orientation] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the group; a prop left None keeps the component's default.

        Args:
            children: The grouped buttons.
            orientation: Whether the buttons join in a row or a column.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-button-group", locals(), children, **kwargs)


class ButtonGroupSeparator(Element):
    """A separator between grouped buttons."""

    def __init__(self, *, orientation: Prop[Orientation] = None, decorative: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the separator; a prop left None keeps the component's default.

        Args:
            orientation: The direction the separator runs.
            decorative: Hides the separator from assistive technology.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-button-group-separator", locals(), None, **kwargs)


class ButtonGroupText(Element):
    """Text set beside grouped buttons."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the text from `children`."""
        super().__init__("ff-button-group-text", locals(), children, **kwargs)
