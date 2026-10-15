"""The table panel: a TanStack table over a polars frame that sorts, filters, facets, hides and groups in the client, with the layout persisted per panel."""

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, cast

import polars as pl
from trame.widgets import react

from femforge.common.log import log
from femforge.common.utils import title
from femforge_workbench.grid import GROUP_COLUMN, Row
from femforge_workbench.panels import PanelComponent
from femforge_workbench.widgets.table import ColumnSpec, LayoutDefaults, Selectable, Table, TableOptions


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


STORE_PREFIX = "table-"
NO_SELECTION: Selectable = "none"
"""The selection mode that leaves the rows unselectable."""

type Layout = dict[str, object]
"""The persisted layout document the client reports: column order and visibility, group fields, sort and filters."""

type Pick = Callable[[Row], None]
"""Receives a clicked row."""

type Select = Callable[[list[Row]], None]
"""Receives the selected rows."""


class StateVar(StrEnum):
    """The per-panel state variables the table binds."""

    COLUMNS = "columns"
    ROWS = "rows"
    OPTIONS = "options"


class SelectionKey(StrEnum):
    """The field of the selection the client reports that the panel reads."""

    ROWS = "rows"


def to_rows(frame: pl.DataFrame) -> list[Row]:
    """The table's data: numbers stay numbers so the client sorts and filters them, everything else is text."""

    def shown(name: str, dtype: pl.DataType) -> pl.Expr:
        column = pl.col(name)
        if dtype.is_float():
            # JSON carries no NaN or infinity, and the state encoder would write one the client cannot parse.
            return pl.when(column.is_finite()).then(column).otherwise(None).alias(name)

        if dtype.is_numeric():
            return column

        return column.cast(pl.String).alias(name)

    kept = [shown(name, dtype) for name, dtype in frame.schema.items()]

    return frame.select(kept).to_dicts()


class TablePanel(PanelComponent):
    """A read-only table whose rows go over once, the client sorting, filtering, faceting, reordering, hiding and grouping them; `lead` orders the leading columns and pins the first."""

    def __init__(
        self,
        app: "Workbench",
        id: str,
        title: str,
        frame: pl.DataFrame,
        *,
        grouped: bool = False,
        filterable: bool = False,
        lead: Sequence[str] = (),
        hidden: Sequence[str] = (),
        pick: Pick | None = None,
        selectable: Selectable = NO_SELECTION,
        select: Select | None = None,
        virtual: bool = False,
    ) -> None:
        """Builds the panel over a frame, its layout restored from the store.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            frame: The rows to show.
            grouped: Groups the rows by the `group` label column that `group_rows` adds.
            filterable: Shows the column filters.
            lead: The columns sent first, in order; the first is pinned.
            hidden: The frame's columns sent with the rows but given no column, so a pick still sees them.
            pick: Receives a clicked row; None leaves the rows unclickable.
            selectable: Whether the user selects no rows, one or several.
            select: Receives the selected rows on every change; None ignores the selection.
            virtual: Renders only the rows in view, for frames too long to draw whole.
        """
        self.frame = frame
        self.grouped = grouped
        self.filterable = filterable
        self.lead = tuple(lead)
        self.hidden = tuple(hidden)
        self.pick = pick
        self.selectable: Selectable = selectable
        self.select = select
        self.virtual = virtual
        self.saved: Layout | None = None
        """The layout the last session left, handed to the client through the options."""
        self.table: Table | None = None

        super().__init__(app, id, title)

    def build(self) -> None:
        """Pushes the columns, rows and options into the state and builds the table bound to them."""
        self.saved = self.app.persist.load(self.app.scope, self.store_key)
        pick = self.callback(self.on_click, args="[$event]") if self.pick is not None else None
        select = self.callback(self.on_select, args="[$event]") if self.selectable != NO_SELECTION and self.select is not None else None

        # A rebuilt panel must push its rows: a bound default only fills a variable the last build left empty.
        self.state[self.key(StateVar.COLUMNS)] = self.columns()
        self.state[self.key(StateVar.ROWS)] = to_rows(self.frame)
        self.state[self.key(StateVar.OPTIONS)] = self.options()
        self.table = Table(
            columns=react.Bind(self.key(StateVar.COLUMNS)),
            rows=react.Bind(self.key(StateVar.ROWS)),
            options=react.Bind(self.key(StateVar.OPTIONS)),
            on_layout_change=self.callback(self.on_layout, args="[$event]"),
            on_row_click=pick,
            on_selection_change=select,
        )

    @property
    def store_key(self) -> str:
        """The persisted document of this panel's layout."""
        return f"{STORE_PREFIX}{self.id}"

    def names(self) -> list[str]:
        """The columns sent: `lead` first, the group label while grouped, then the rest without `hidden`; the client applies the persisted order."""
        trailing = [name for name in self.frame.columns if name not in self.lead and name not in self.hidden and name != GROUP_COLUMN]
        label = [GROUP_COLUMN] if self.grouped else []

        return [*self.lead, *label, *trailing]

    def columns(self) -> list[ColumnSpec]:
        """The column specs: numbers sort, filter and facet as numbers; the group label never facets."""

        def spec(name: str) -> ColumnSpec:
            column = ColumnSpec(field=name, title=title(name), numeric=self.frame.schema[name].is_numeric())
            if name == GROUP_COLUMN:
                column["facet"] = "none"

            return column

        return [spec(name) for name in self.names()]

    def defaults(self) -> LayoutDefaults:
        """The authored layout: grouped by the label column while grouped."""
        if not self.grouped:
            return LayoutDefaults()

        return LayoutDefaults(groupBy=[GROUP_COLUMN])

    def options(self) -> TableOptions:
        """The table options: the layout the last session left, the authored defaults, and the filter, selection and virtual switches."""
        return TableOptions(
            layout=self.saved,
            defaults=self.defaults(),
            filterable=self.filterable,
            selectable=self.selectable,
            virtual=self.virtual,
        )

    def set_rows(self, frame: pl.DataFrame) -> None:
        """Replaces the rows; the columns follow a changed schema, the client keeping its order, visibility, sort and filters."""
        changed = frame.columns != self.frame.columns
        self.frame = frame

        with self.state:
            if changed:
                self.state[self.key(StateVar.COLUMNS)] = self.columns()

            self.state[self.key(StateVar.ROWS)] = to_rows(frame)

    def restore(self, layout: Layout | None) -> None:
        """Applies a layout document on every client, the authored defaults for None."""
        if self.table is not None:
            self.table.restore(layout)

    def reset_layout(self) -> None:
        """Returns the table to the authored layout on every client."""
        if self.table is not None:
            self.table.reset_layout()

    def scroll_to_end(self) -> None:
        """Scrolls the table to its last row."""
        if self.table is not None:
            self.table.scroll_to_end()

    def scroll_to_row(self, index: int) -> None:
        """Scrolls the row at `index` of the shown rows into view."""
        if self.table is not None:
            self.table.scroll_to_row(index)

    def open_config(self, open: bool = True) -> None:
        """Shows, or hides, the column configuration panel."""
        if self.table is not None:
            self.table.open_config(open)

    def on_layout(self, document: object) -> None:
        """Persists the layout the client reports and pushes it back, so a page that reloads is not served the layout this one replaced."""
        if not isinstance(document, Mapping):
            return

        self.saved = dict(cast("Mapping[str, object]", document))
        self.app.persist.save(self.app.scope, self.store_key, self.saved)
        with self.state:
            self.state[self.key(StateVar.OPTIONS)] = self.options()

        log.bind(panel=self.id, keys=sorted(self.saved)).info("Table layout saved.")

    def on_click(self, row: object) -> None:
        """Hands a clicked data row to the pick handler."""
        if self.pick is None or not isinstance(row, Mapping):
            return

        self.pick(dict(cast("Mapping[str, object]", row)))

    def on_select(self, selection: object) -> None:
        """Hands the selected rows to the select handler."""
        if self.select is None or not isinstance(selection, Mapping):
            return

        rows = cast("Mapping[str, object]", selection).get(SelectionKey.ROWS)
        if not isinstance(rows, list):
            return

        chosen = [dict(cast("Mapping[str, object]", row)) for row in cast("list[object]", rows) if isinstance(row, Mapping)]
        self.select(chosen)
