"""The dock: a dockview host rendering one server template per panel."""

from collections.abc import Mapping
from typing import Literal, Unpack

from trame.widgets import react

from femforge_workbench.widgets.element import Element, Event, Prop, Shared, next_ref


type FloatingGroupBounds = Literal["boundedWithinViewport"] | Mapping[str, float]
type Layout = Mapping[str, object]

KIND = "dock"


class Dock(Element):
    """Hosts panels whose bodies are the server templates `render_panel` names; the panel methods reach the mounted dock through its ref."""

    def __init__(
        self,
        *,
        render_panel: react.Slot | None = None,
        dark: Prop[bool] = None,
        popout_url: Prop[str] = None,
        floating_group_bounds: Prop[FloatingGroupBounds] = None,
        on_ready: Event = None,
        on_layout_change: Event = None,
        on_visibility: Event = None,
        on_active_panel: Event = None,
        on_panel_removed: Event = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the dock; a prop left None keeps the component's default.

        Args:
            render_panel: The slot rendering a panel's body from its params.
            dark: Whether the dock paints its dark theme.
            popout_url: The page a popped-out group opens in.
            floating_group_bounds: Where a floating group may move: wholly within the viewport, or keeping the given minimum width and height in view.
            on_ready: Fires once the dock is mounted and can take panels.
            on_layout_change: Receives the serialized layout once a burst of changes settles.
            on_visibility: Receives a panel's id and whether it became visible.
            on_active_panel: Receives the id of the panel that became active.
            on_panel_removed: Receives the id of the panel the user closed.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        kwargs.setdefault("ref", next_ref(KIND))
        super().__init__("ff-dock", locals(), None, **kwargs)

    def add_panel(self, panel_id: str, title: str, template_name: str, **add_on: object) -> None:
        """Adds a panel showing a template; an id already present is left alone.

        Args:
            panel_id: The new panel's id.
            title: The tab title.
            template_name: The server template the panel's body renders.
            add_on: dockview's placement options, such as `position`, `inactive` and the initial size.
        """
        self.call("addPanel", panel_id, title, template_name, add_on)

    def remove_panel(self, panel_id: str) -> None:
        """Removes the panel from the dock."""
        self.call("removePanel", panel_id)

    def active_panel(self, panel_id: str) -> None:
        """Activates the panel's tab."""
        self.call("activePanel", panel_id)

    def restore_layout(self, layout: Layout) -> None:
        """Replaces the arrangement with a serialized dockview layout."""
        self.call("restoreLayout", dict(layout))

    def move_to(self, panel_id: str, target_id: str) -> None:
        """Moves a panel into the group of another."""
        self.call("moveTo", panel_id, target_id)

    def set_panel_title(self, panel_id: str, title: str) -> None:
        """Renames the panel's tab."""
        self.call("setTitle", panel_id, title)

    def maximize(self, panel_id: str) -> None:
        """Maximizes the panel's group."""
        self.call("maximize", panel_id)

    def restore_maximized(self) -> None:
        """Returns the maximized group to its place."""
        self.call("restoreMaximized")
