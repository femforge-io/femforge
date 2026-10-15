"""Dialogs: a modal and its trigger, content, header, footer and close."""

from typing import Unpack

from femforge_workbench.widgets.element import Element, Event, Prop, Shared


class Dialog(Element):
    """A dialog opened by its trigger or the `open_` prop."""

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
        """Creates the dialog; a prop left None keeps the component's default.

        Args:
            children: The dialog's trigger and content.
            open_: Whether the dialog is open.
            default_open: Whether the dialog opens at mount while `open_` is unset.
            on_open_change: Receives the new open state, as when Escape or the backdrop closes the dialog.
            modal: Blocks the page behind the dialog while it is open.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dialog", locals(), children, **kwargs)


class DialogTrigger(Element):
    """The control opening the dialog; `as_child` renders the child element itself."""

    def __init__(self, children: object = None, *, as_child: Prop[bool] = None, disabled: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the trigger; a prop left None keeps the component's default.

        Args:
            children: The trigger's content.
            as_child: Renders the child element itself as the trigger.
            disabled: Disables the trigger.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dialog-trigger", locals(), children, **kwargs)


class DialogContent(Element):
    """The dialog's box."""

    def __init__(self, children: object = None, *, show_close_button: Prop[bool] = None, force_mount: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the box; a prop left None keeps the component's default.

        Args:
            children: The dialog's header, body and footer.
            show_close_button: Shows the close button in the corner.
            force_mount: Keeps the content mounted while the dialog is closed.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dialog-content", locals(), children, **kwargs)


class DialogHeader(Element):
    """The title and description block."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the header around `children`."""
        super().__init__("ff-dialog-header", locals(), children, **kwargs)


class DialogFooter(Element):
    """The action row."""

    def __init__(self, children: object = None, *, show_close_button: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the action row; a prop left None keeps the component's default.

        Args:
            children: The dialog's actions.
            show_close_button: Adds a Close button to the row.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dialog-footer", locals(), children, **kwargs)


class DialogTitle(Element):
    """The dialog's title."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the title from `children`."""
        super().__init__("ff-dialog-title", locals(), children, **kwargs)


class DialogDescription(Element):
    """The dialog's description."""

    def __init__(self, children: object = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the description from `children`."""
        super().__init__("ff-dialog-description", locals(), children, **kwargs)


class DialogClose(Element):
    """A control closing the dialog."""

    def __init__(self, children: object = None, *, as_child: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the close control; a prop left None keeps the component's default.

        Args:
            children: The control's content.
            as_child: Renders the child element itself as the close control.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-dialog-close", locals(), children, **kwargs)
