"""The entity grid: rows of bodies or boundaries with their conditions as read-only editors, one tab stop with roving focus."""

from collections.abc import Sequence
from typing import TypedDict, Unpack

from femforge_workbench.widgets.element import Element, Event, Prop, Shared
from femforge_workbench.widgets.fields import ConditionCard
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


class BodyChip(TypedDict, total=False):
    """A body a boundary touches, by its name and its body color."""

    name: str
    """The body's name, or ``External`` for the outside."""

    color: str
    """The body's color as sRGB hex; absent for the outside."""


class EntityRow(TypedDict, total=False):
    """One body or boundary group: its kind icon, its name or the bodies it touches, its material and measure, and its conditions as cards."""

    id: str
    """The entity id, such as ``boundary:RIGHTFACE``."""

    name: str
    """The name as authored, such as ``RightFace``."""

    detail: str
    """The measure of a body or a boundary."""

    detail_tip: str
    """The detail's tooltip, such as the face area a 2D length stands for."""

    material: str
    """A body's material, shown as a read-only field."""

    material_tip: str
    """The material's full name."""

    tip: list[str]
    """The name's tooltip lines, the Elmer name first."""

    kind_icon: IconName
    """The icon of the group's kind: a body, a face or an edge."""

    chips: list[BodyChip]
    """The bodies an unnamed boundary touches, which name it."""

    cards: list[ConditionCard]
    """One read-only editor per condition the group received."""

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
    """The row's accessible name: its name, its conditions with their tooltips and why it is dimmed or hidden."""


class EntityGrid(Element):
    """Rows of bodies or boundaries as one grid: the arrows move the focus, Enter selects, F frames, S shows a hidden body and Escape clears."""

    def __init__(
        self,
        *,
        rows: Prop[Sequence[EntityRow]] = None,
        selected: Prop[str] = None,
        label: Prop[str] = None,
        columns: Prop[Sequence[str]] = None,
        on_select: Event = None,
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
            columns: The header of each column: the name and the detail.
            on_select: Receives the id of a row clicked or chosen with Enter, or an empty id for Escape.
            on_frame: Receives the id of a row double-clicked or framed with F.
            on_show: Receives the id of a hidden body whose Show was clicked or pressed with S.
            on_hover: Receives the id of the row the pointer or the keyboard focus moved onto, and an empty id once both left the grid.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-entity-grid", locals(), None, **kwargs)
