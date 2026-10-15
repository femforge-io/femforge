"""The table: a TanStack table over columns, rows and options, reporting its layout, row clicks and the selection."""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, Unpack

from femforge_workbench.widgets.element import Element, Event, Prop, Shared, next_ref


type Row = Mapping[str, object]
type Layout = Mapping[str, object]
type Facet = Literal["auto", "none", "range", "values"]
type Selectable = Literal["multiple", "none", "single"]
"""Whether the user selects no rows, one or several."""

KIND = "table"


class ColumnSpec(TypedDict, total=False):
    """One column: its row field, header title, whether it sorts and filters as a number, how its header facets, and its width."""

    field: str
    title: str
    numeric: bool
    facet: Facet
    width: int


class LayoutDefaults(TypedDict, total=False):
    """The authored layout a missing document and Reset fall back to; the keys are the document's."""

    columns: Sequence[Mapping[str, object]]
    groupBy: Sequence[str]
    sort: Sequence[Mapping[str, object]]
    filter: Sequence[Mapping[str, object]]


class TableOptions(TypedDict, total=False):
    """The table options; the keys are the React prop's fields."""

    layout: Layout | None
    defaults: LayoutDefaults
    filterable: bool
    groupable: bool
    lead: int
    virtual: bool
    rowKey: str
    selectable: Selectable
    facetLimit: int
    dense: bool
    debug: bool


class Table(Element):
    """Columns over rows; `options` carries the persisted layout, the authored defaults and the switches."""

    def __init__(
        self,
        *,
        columns: Prop[Sequence[ColumnSpec]] = None,
        rows: Prop[Sequence[Row]] = None,
        options: Prop[TableOptions] = None,
        on_layout_change: Event = None,
        on_row_click: Event = None,
        on_selection_change: Event = None,
        **kwargs: Unpack[Shared],
    ) -> None:
        """Creates the table; a prop left None keeps the component's default.

        Args:
            columns: The column specs, in the order sent.
            rows: The data rows.
            options: The persisted layout, the authored defaults and the switches.
            on_layout_change: Receives the layout document once a burst of the user's changes settles.
            on_row_click: Receives a clicked data row.
            on_selection_change: Receives the selection, its rows under `rows`.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        kwargs.setdefault("ref", next_ref(KIND))
        super().__init__("ff-table", locals(), None, **kwargs)

    def restore(self, layout: Layout | None) -> None:
        """Applies a layout document, or the authored defaults for None."""
        self.call("restore", dict(layout) if layout is not None else None)

    def reset_layout(self) -> None:
        """Returns to the authored defaults."""
        self.call("resetLayout")

    def scroll_to_end(self) -> None:
        """Scrolls to the last row."""
        self.call("scrollToEnd")

    def scroll_to_row(self, index: int) -> None:
        """Scrolls a row of the shown row model into view."""
        self.call("scrollToRow", index)

    def open_config(self, open: bool = True) -> None:
        """Shows, or hides, the column configuration panel."""
        self.call("openConfig", open)
