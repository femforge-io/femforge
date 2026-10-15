"""Tabs: a tab list and the content panes it switches."""

from typing import Literal, Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import Element, Event, Orientation, Prop, Shared, setter


type ActivationMode = Literal["automatic", "manual"]
type TabsListVariant = Literal["default", "line"]


class Tabs(Element):
    """Tabbed panes; `model` binds the active tab to a state variable."""

    def __init__(
        self,
        children: object = None,
        *,
        model: str | None = None,
        value: Prop[str] = None,
        default_value: Prop[str] = None,
        on_value_change: Event = None,
        orientation: Prop[Orientation] = None,
        activation_mode: Prop[ActivationMode] = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the tabs; a prop left None keeps the component's default.

        Args:
            children: The tab list and the panes.
            model: A state variable to bind the active tab to, unless `on_value_change` is given.
            value: The active tab's value.
            default_value: The active tab at mount while `value` is unset.
            on_value_change: Receives the newly active tab's value.
            orientation: The direction the tab list runs.
            activation_mode: Whether an arrow key activates a tab or only focuses it.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        if model is not None and on_value_change is None:
            value = react.Bind(model)
            on_value_change = setter(model)

        super().__init__("ff-tabs", locals(), children, **kwargs)


class TabsList(Element):
    """The row of tab triggers."""

    def __init__(self, children: object = None, *, variant: Prop[TabsListVariant] = None, loop: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the tab list; a prop left None keeps the component's default.

        Args:
            children: The tab triggers.
            variant: The list's style.
            loop: Whether keyboard navigation wraps from the last tab to the first.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-tabs-list", locals(), children, **kwargs)


class TabsTrigger(Element):
    """The tab selecting `value`."""

    def __init__(self, children: object = None, *, value: Prop[str] = None, disabled: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the tab; a prop left None keeps the component's default.

        Args:
            children: The tab's label.
            value: The value the tab activates.
            disabled: Disables the tab.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-tabs-trigger", locals(), children, **kwargs)


class TabsContent(Element):
    """The pane shown while `value` is the active tab."""

    def __init__(self, children: object = None, *, value: Prop[str] = None, force_mount: Prop[bool] = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the pane; a prop left None keeps the component's default.

        Args:
            children: The pane's content.
            value: The tab value the pane shows for.
            force_mount: Keeps the pane mounted while another tab is active.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-tabs-content", locals(), children, **kwargs)
