"""The table panel: the rows and columns it pushes, the element's bindings, the calls through its ref, the selection, and a layout persisted per panel."""

from pathlib import Path
from typing import cast

import polars as pl
import pytest

from femforge.common.utils import title

from femforge_workbench.grid import GROUP_COLUMN, Row, group_rows
from femforge_workbench.persist import LocalJsonStore
from tests.workbench.tree import Node, find, prop, props


pytest.importorskip("trame")
pytest.importorskip("pyvista")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Workbench  # noqa: E402
from femforge_workbench.panels.table import StateVar, TablePanel, to_rows  # noqa: E402


SERIES = "series"
VALUE = "MAG.OD_mm"
COUNT = "count"
TAG = "tag"


def _frame() -> pl.DataFrame:
    frame = pl.DataFrame(
        {
            SERIES: ["alpha", "beta", "alpha", "beta"],
            TAG: ["a1", "b1", "a2", "b2"],
            VALUE: [1.23456, 2.5, float("nan"), None],
            COUNT: [1, 2, 3, 4],
        }
    )

    return group_rows(frame, SERIES, max_=[VALUE])


def _shell(name: str, root: Path, *, server: str) -> Workbench:
    return Workbench(name, title="Table", server=get_server(server, client_type="react"), persist=LocalJsonStore(root), user="tester")


def _table(app: Workbench, id: str = "rows", **kwargs: object) -> TablePanel:
    return TablePanel(app, id, "Rows", _frame(), grouped=True, filterable=True, lead=(SERIES,), **kwargs)  # pyright: ignore[reportArgumentType]  # the keyword-only knobs are typed on the panel


def _document() -> dict[str, object]:
    return {
        "v": 2,
        "columns": [{"field": COUNT, "visible": True}, {"field": TAG, "visible": False}, {"field": SERIES, "visible": True}],
        "groupBy": [GROUP_COLUMN],
        "sort": [{"field": COUNT, "dir": "desc"}],
        "filter": [{"field": SERIES, "value": "al"}, {"field": COUNT, "min": 2}, {"field": TAG, "in": ["a1", None]}],
    }


def _callback(node: Node, name: str) -> dict[str, object]:
    return cast("dict[str, object]", cast("dict[str, object]", prop(node, name))["callback"])


def test_the_rows_keep_numbers_as_numbers_and_blank_the_non_finite() -> None:
    rows = to_rows(_frame())

    assert rows[0][VALUE] == pytest.approx(1.23456)
    assert rows[0][COUNT] == 1
    assert rows[2][VALUE] is None and rows[3][VALUE] is None
    assert rows[0][SERIES] == "alpha"
    assert str(rows[0][GROUP_COLUMN]).startswith("alpha")


def test_the_columns_lead_first_then_the_group_label_without_the_hidden_and_the_options_carry_the_authored_grouping(tmp_path: Path) -> None:
    app = _shell("table-columns", tmp_path, server="wb-table-columns")
    table = _table(app, hidden=(TAG,))

    defined = table.columns()

    assert defined == [
        {"field": SERIES, "title": title(SERIES), "numeric": False},
        {"field": GROUP_COLUMN, "title": title(GROUP_COLUMN), "numeric": False, "facet": "none"},
        {"field": VALUE, "title": title(VALUE), "numeric": True},
        {"field": COUNT, "title": title(COUNT), "numeric": True},
    ]
    assert table.options() == {"layout": None, "defaults": {"groupBy": [GROUP_COLUMN]}, "filterable": True, "selectable": "none", "virtual": False}

    plain = TablePanel(app, "flat", "Flat", _frame(), virtual=True)

    assert [column.get("field") for column in plain.columns()] == [SERIES, TAG, VALUE, COUNT]
    assert plain.options() == {"layout": None, "defaults": {}, "filterable": False, "selectable": "none", "virtual": True}


def test_the_element_binds_the_state_and_calls_reach_its_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("table-element", tmp_path, server="wb-table-element")
    picked: list[Row] = []
    table = _table(app, pick=picked.append)
    calls: list[tuple[str | None, str | None, tuple[object, ...]]] = []

    def record(ref: str | None = None, method: str | None = None, *args: object) -> None:
        calls.append((ref, method, args))

    monkeypatch.setattr(app.server, "js_call", record)
    table.restore(_document())
    table.reset_layout()
    table.scroll_to_end()
    table.scroll_to_row(9000)
    table.open_config()
    table.open_config(False)

    assert table.table is not None
    node = find(app.state[f"trame__template_{table.template_name}"], "ff-table")[0]
    assert prop(node, "columns") == {"js": table.key(StateVar.COLUMNS)}
    assert prop(node, "rows") == {"js": table.key(StateVar.ROWS)}
    assert prop(node, "options") == {"js": table.key(StateVar.OPTIONS)}
    layout_change = _callback(node, "onLayoutChange")
    assert layout_change["args"] == {"js": "[$event]"} and app.ctrl.trigger_fn(str(layout_change["trigger"])) == table.on_layout
    assert _callback(node, "onRowClick")["args"] == {"js": "[$event]"}
    assert "onSelectionChange" not in props(node), "a table that is not selectable reports no selection"
    assert prop(node, "ref") == table.table.reference
    reference = table.table.reference
    assert calls == [
        (reference, "restore", (_document(),)),
        (reference, "resetLayout", ()),
        (reference, "scrollToEnd", ()),
        (reference, "scrollToRow", (9000,)),
        (reference, "openConfig", (True,)),
        (reference, "openConfig", (False,)),
    ]

    table.on_click({SERIES: "alpha"})
    table.on_click("not a row")

    assert picked == [{SERIES: "alpha"}]
    assert "onRowClick" not in props(find(app.state[f"trame__template_{TablePanel(app, 'plain', 'Plain', _frame()).template_name}"], "ff-table")[0])


def test_a_selectable_table_reports_the_selected_rows(tmp_path: Path) -> None:
    app = _shell("table-select", tmp_path, server="wb-table-select")
    chosen: list[list[Row]] = []
    table = _table(app, selectable="multiple", select=chosen.append)

    node = find(app.state[f"trame__template_{table.template_name}"], "ff-table")[0]
    assert _callback(node, "onSelectionChange")["args"] == {"js": "[$event]"}
    assert cast("dict[str, object]", app.state[table.key(StateVar.OPTIONS)])["selectable"] == "multiple"

    selection: dict[str, list[object]] = {"keys": ["0", "2"], "rows": [{SERIES: "alpha", COUNT: 1}, {SERIES: "alpha", COUNT: 3}]}
    empty: dict[str, list[object]] = {"keys": [], "rows": []}

    table.on_select(selection)
    table.on_select(empty)
    table.on_select("not a selection")
    table.on_select({"keys": ["0"], "rows": "not rows"})

    assert chosen == [[{SERIES: "alpha", COUNT: 1}, {SERIES: "alpha", COUNT: 3}], []]

    unhandled = _table(app, id="unhandled", selectable="single")

    assert "onSelectionChange" not in props(find(app.state[f"trame__template_{unhandled.template_name}"], "ff-table")[0]), "no handler, no trigger"


def test_set_rows_pushes_the_data_and_follows_a_changed_schema(tmp_path: Path) -> None:
    app = _shell("table-rows", tmp_path, server="wb-table-rows")
    table = _table(app)
    columns = table.key(StateVar.COLUMNS)
    before = cast("list[Row]", app.state[columns])

    table.set_rows(_frame().filter(pl.col(SERIES) == "alpha"))

    assert app.state[columns] == before
    assert len(cast("list[Row]", app.state[table.key(StateVar.ROWS)])) == 2

    table.set_rows(_frame().drop(TAG))

    assert [column["field"] for column in cast("list[Row]", app.state[columns])] == [SERIES, GROUP_COLUMN, VALUE, COUNT]


def test_a_persisted_layout_survives_a_restart_and_is_pushed_back_into_the_options(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path)
    app = Workbench("table-persist", server=get_server("wb-table-persist", client_type="react"), persist=store, user="tester")
    table = _table(app)
    document = _document()

    table.on_layout(document)
    store.flush()

    assert store.load(app.scope, table.store_key) == document
    assert (tmp_path / "tester" / "table-persist" / f"{table.store_key}.json").exists()
    assert cast("dict[str, object]", app.state[table.key(StateVar.OPTIONS)])["layout"] == document

    restarted = Workbench(
        "table-persist",
        server=get_server("wb-table-restarted", client_type="react"),
        persist=LocalJsonStore(tmp_path),
        user="tester",
    )
    rebuilt = _table(restarted)

    assert rebuilt.saved == document
    assert rebuilt.options().get("layout") == document
    assert rebuilt.columns() == table.columns(), "the client applies the persisted order; Python sends the natural one"

    rebuilt.on_layout("not a layout")

    assert rebuilt.saved == document


def test_dispose_takes_the_tables_triggers_off_the_controller(tmp_path: Path) -> None:
    app = _shell("table-dispose", tmp_path, server="wb-table-dispose")
    picked: list[Row] = []
    table = _table(app, id="disposed", pick=picked.append)
    layout_trigger = app.ctrl.trigger_name(table.on_layout)
    pick_trigger = app.ctrl.trigger_name(table.on_click)

    assert app.ctrl.trigger_fn(layout_trigger) is not None and app.ctrl.trigger_fn(pick_trigger) is not None
    assert table.bound == [table.on_click, table.on_layout]

    table.dispose()

    assert app.ctrl.trigger_fn(layout_trigger) is None
    assert app.ctrl.trigger_fn(pick_trigger) is None
    assert table.listeners == [] and table.bound == []
