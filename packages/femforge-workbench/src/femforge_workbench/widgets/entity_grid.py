"""The entity grid: rows of bodies or boundaries with their condition lines and glyph toggles, one tab stop with roving focus."""

from collections.abc import Sequence
from typing import TypedDict, Unpack

from femforge_workbench.widgets.element import Element, Event, Prop, Shared
from femforge_workbench.widgets.icon import IconName


class LinePart(TypedDict, total=False):
    """One run of text in a condition line."""

    text: str
    """The text, separators included."""

    muted: bool
    """Whether the part reads dimmer than the line, such as an axis the frame drops."""

    tip: str
    """The part's tooltip; empty for none."""


class ConditionLine(TypedDict):
    """One condition a row received, as the parts it reads in."""

    parts: list[LinePart]
    """The parts, in reading order."""


class EntityRow(TypedDict, total=False):
    """One body or boundary group: its name, its material or measure, its condition lines and its glyph toggle."""

    id: str
    """The entity id, such as ``boundary:RIGHTFACE``."""

    name: str
    """The name as authored, such as ``RightFace``."""

    detail: str
    """The material of a body or the measure of a boundary."""

    detail_tip: str
    """The detail's tooltip, such as the face area a 2D length stands for."""

    tip: list[str]
    """The name's tooltip lines, the Elmer name first."""

    lines: list[ConditionLine]
    """One line per condition, under the name."""

    icon: IconName
    """The icon of the row's glyph kind; absent for a row without a drawable condition."""

    glyph_shown: bool
    """Whether the row's glyph is shown; absent while no scene draws it, which leaves the icon without a toggle."""

    glyph_note: str
    """Why the scene hides the row's glyph whatever its toggle says, such as a legend switch; absent while the toggle decides."""

    fit: bool
    """Whether the row offers to fit the view to its group, true while a scene draws the run and has the group's cells."""

    group: bool
    """Whether the row heads the bodies of one material."""

    member: bool
    """Whether the row is a body under a material group."""

    dimmed: bool
    """Whether the row has no cells to highlight."""

    dim_tip: str
    """Why a dimmed row cannot be highlighted."""

    hidden: bool
    """Whether the body is hidden in the Field Viewer, which the row offers to show."""

    label: str
    """The row's accessible name: its name, its condition lines with their tooltips, its glyph state and why it is dimmed or hidden."""


class EntityGrid(Element):
    """Rows of bodies or boundaries as one grid: the arrows move the focus, Enter selects, G toggles a glyph, F frames, S shows a hidden body and Escape clears."""

    def __init__(
        self,
        *,
        rows: Prop[Sequence[EntityRow]] = None,
        selected: Prop[str] = None,
        label: Prop[str] = None,
        columns: Prop[Sequence[str]] = None,
        on_select: Event = None,
        on_toggle_glyph: Event = None,
        on_frame: Event = None,
        on_show: Event = None,
        on_hover: Event = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the grid; a prop left None keeps the component's default.

        Args:
            rows: The rows, in order.
            selected: The id of the selected row.
            label: The grid's accessible name.
            columns: The header of each column: the name, the detail and the glyph.
            on_select: Receives the id of a row clicked or chosen with Enter, or an empty id for Escape.
            on_toggle_glyph: Receives the id of a row whose glyph toggle was pressed.
            on_frame: Receives the id of a row double-clicked or framed with F.
            on_show: Receives the id of a hidden body whose Show was clicked or pressed with S.
            on_hover: Receives the id of the row the pointer or the keyboard focus moved onto, and an empty id once both left the grid.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-entity-grid", locals(), None, **kwargs)
