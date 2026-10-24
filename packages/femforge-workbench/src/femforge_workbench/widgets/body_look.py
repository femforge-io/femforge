"""The body look: a swatch that opens a body's visibility, opacity and color editor."""

from collections.abc import Sequence
from typing import Literal, Unpack

from femforge_workbench.widgets.combobox import ComboboxItem
from femforge_workbench.widgets.element import Element, Event, Prop, Shared


type Paint = Literal["field", "colormap", "solid", "mixed"]


class BodyLook(Element):
    """One body's look as an 18 px swatch; its popover holds the eye and opacity on top, then the Field, Colormap and Fixed modes."""

    def __init__(
        self,
        *,
        paint: Prop[Paint] = None,
        colors: Prop[Sequence[str | None]] = None,
        shown: Prop[bool | Literal["mixed"]] = None,
        opacity: Prop[float] = None,
        field_gradient: Prop[str] = None,
        colormap: Prop[str] = None,
        colormaps: Prop[Sequence[ComboboxItem]] = None,
        filters: Prop[Sequence[str]] = None,
        field_disabled: Prop[bool] = None,
        palette: Prop[Sequence[str]] = None,
        label: Prop[str] = None,
        disabled: Prop[bool] = None,
        on_shown_change: Event = None,
        on_opacity_change: Event = None,
        on_opacity_commit: Event = None,
        on_paint_change: Event = None,
        on_color_change: Event = None,
        on_colormap_change: Event = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the body look; a prop left None keeps the component's default.

        Args:
            paint: Where the color comes from: the field colormap, the body colormap, a fixed color, or a group's mix.
            colors: The resolved color of each body the swatch stands for, None for a body painted by the field.
            shown: Whether the bodies show, or ``mixed`` for a group that is partly hidden.
            opacity: The bodies' own opacity, 0 to 1.
            field_gradient: The field colormap as a CSS gradient.
            colormap: The shared body colormap.
            colormaps: The colormaps the Colormap mode lists.
            filters: The group toggles that narrow the colormap list.
            field_disabled: Disables the Field mode, for a run without fields.
            palette: The fixed colors offered one click away.
            label: The name the controls are announced with.
            disabled: Disables the swatch.
            on_shown_change: Receives the new visibility.
            on_opacity_change: Receives the opacity as the thumb moves.
            on_opacity_commit: Receives the opacity when the thumb is released.
            on_paint_change: Receives the chosen mode.
            on_color_change: Receives a picked fixed color.
            on_colormap_change: Receives a picked body colormap.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-body-look", locals(), None, **kwargs)
