"""The widget wrappers: registration rules, refs and calls, and their parameters against the dist's props.json and icons.json."""

import importlib
import inspect
import json
import pkgutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal, TypeAliasType, cast, get_args, get_origin, is_typeddict

import pytest
from trame.app import get_server
from trame.ui.html import DivLayout
from trame.widgets import react
from trame_server import Server

from femforge_workbench import widgets
from femforge_workbench.widgets import module
from femforge_workbench.widgets.accordion import Accordion
from femforge_workbench.widgets.button import Button
from femforge_workbench.widgets.checkbox import Checkbox
from femforge_workbench.widgets.dist_hash import source_hash
from femforge_workbench.widgets.dock import Dock
from femforge_workbench.widgets.element import MODEL, RESERVED, SHARED, Element, Shared, is_event, next_ref, react_name
from femforge_workbench.widgets.icon import IconName
from femforge_workbench.widgets.input import ColorInput, Input, InputGroupInput, Textarea
from femforge_workbench.widgets.select import Select
from femforge_workbench.widgets.slider import Slider
from femforge_workbench.widgets.table import Table
from femforge_workbench.widgets.tabs import Tabs
from femforge_workbench.widgets.term import Term
from femforge_workbench.widgets.toggle import Toggle, ToggleGroup
from femforge_workbench.widgets.tree import Tree
from tests.workbench.tree import find, prop, props


type TagProps = dict[str, dict[str, list[str] | dict[str, list[str]]]]

PROPS = cast("TagProps", json.loads((module.SERVE_DIR / "props.json").read_text(encoding="utf-8")))
ICONS = cast("list[str]", json.loads((module.SERVE_DIR / "icons.json").read_text(encoding="utf-8")))
FUNCTION_PROPS = frozenset({"render_panel"})
"""Wrapper parameters typed as functions in TS, which the props emitter leaves out."""

type Wrapper = type[Element]


def _server(name: str) -> Server:
    return get_server(f"widgets-{name}", client_type="react")


def _wrappers() -> Iterator[tuple[str, Wrapper]]:
    """Every Element subclass defined in the widgets package, with its module name."""
    for info in pkgutil.iter_modules(widgets.__path__):
        loaded = importlib.import_module(f"{widgets.__name__}.{info.name}")
        for name, value in cast("dict[str, object]", vars(loaded)).items():
            if inspect.isclass(value) and issubclass(value, Element) and value is not Element and value.__module__ == loaded.__name__:
                yield name, value


def _parameters(wrapper: Wrapper) -> dict[str, inspect.Parameter]:
    """The declared parameters of a wrapper: neither reserved, nor the model sugar."""
    signature = inspect.signature(wrapper.__init__)

    return {name: parameter for name, parameter in signature.parameters.items() if name not in RESERVED and name != MODEL}


def _literal_aliases(annotation: object) -> list[TypeAliasType]:
    """The Literal type aliases inside an annotation, however nested in Prop[...] or unions."""
    if isinstance(annotation, TypeAliasType):
        value = cast("object", annotation.__value__)
        if get_origin(value) is Literal:
            return [annotation]

        return _literal_aliases(value)

    arguments = cast("tuple[object, ...]", get_args(annotation))

    return [found for argument in arguments for found in _literal_aliases(argument)]


def _literal_values(alias: TypeAliasType) -> list[str]:
    """The strings of a Literal alias."""
    return [str(value) for value in cast("tuple[object, ...]", get_args(cast("object", alias.__value__)))]


def _typed_dicts(annotation: object) -> list[type]:
    """The TypedDict classes inside an annotation, however nested in Prop[...], sequences or unions."""
    if is_typeddict(annotation):
        return [cast("type", annotation)]

    if isinstance(annotation, TypeAliasType):
        return _typed_dicts(cast("object", annotation.__value__))

    arguments = cast("tuple[object, ...]", get_args(annotation))

    return [found for argument in arguments for found in _typed_dicts(argument)]


def _build(wrapper: Wrapper, server: Server) -> Element:
    """An instance with every optional parameter unset and a placeholder for each required one."""
    required = {name: "x" for name, parameter in _parameters(wrapper).items() if cast("object", parameter.default) is inspect.Parameter.empty and parameter.kind is not parameter.VAR_KEYWORD}
    factory = cast("Callable[..., Element]", wrapper)
    with DivLayout(server):
        return factory(**required)


WRAPPERS = list(_wrappers())


def test_the_rename_rule_strips_one_underscore_then_maps_or_camel_cases() -> None:
    assert react_name("type_") == "type"
    assert react_name("min_") == "min"
    assert react_name("on_value_change") == "onValueChange"
    assert react_name("min_steps_between_thumbs") == "minStepsBetweenThumbs"
    assert react_name("aria_label") == "aria-label"
    assert react_name("html_for") == "htmlFor"
    assert is_event("on_click") and not is_event("only")


def test_every_declared_name_is_registered_even_when_unset_so_it_serializes_later() -> None:
    server = _server("registration")
    with DivLayout(server):
        button = Button("Go", variant="ghost")

    registered = {name for name in button._attr_names if isinstance(name, tuple)}
    assert ("variant", "variant") in registered and ("size", "size") in registered and ("as_child", "asChild") in registered
    assert ("aria_label", "aria-label") in registered
    assert ("type_", "type") in registered
    assert [name for name in button._attr_names if name == ("classes", "className")] == [("classes", "className")], "shared names are never re-declared"

    node = cast("dict[str, object]", button.html)
    assert node["tag"] == "ff-button" and node["children"] == ["Go"]
    assert props(node) == {"variant": "ghost"}

    button.size = "sm"
    button.classes = "mx-2"
    assert props(cast("dict[str, object]", button.html)) == {"variant": "ghost", "size": "sm", "className": "mx-2"}


def test_events_register_as_camel_cased_handlers_and_shared_events_pass_through() -> None:
    server = _server("events")

    def pick(*_: object) -> None:
        pass

    with DivLayout(server):
        table = Table(on_row_click=react.Callback(pick, args="[$event]"), on_click=react.Callback(pick))

    assert ("on_row_click", "onRowClick") in table._event_names and ("on_layout_change", "onLayoutChange") in table._event_names
    node = cast("dict[str, object]", table.html)
    row_click = cast("dict[str, object]", prop(node, "onRowClick"))
    assert cast("dict[str, object]", row_click["callback"])["args"] == {"js": "[$event]"}
    assert "onClick" in props(node)


def test_refs_are_unique_and_call_reaches_js_call_through_them(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server("refs")
    calls: list[tuple[str | None, str | None, tuple[object, ...]]] = []

    def record(ref: str | None = None, method: str | None = None, *args: object) -> None:
        calls.append((ref, method, args))

    monkeypatch.setattr(server, "js_call", record)
    with DivLayout(server):
        dock = Dock()
        table = Table()
        button = Button()

    assert dock.reference.startswith("ff_dock_") and table.reference.startswith("ff_table_") and dock.reference != table.reference
    assert next_ref("dock") != next_ref("dock")
    with pytest.raises(KeyError):
        _ = button.reference

    dock.add_panel("a", "A", "panel-a", position={"referencePanel": "b", "direction": "right"})
    table.scroll_to_end()

    assert calls == [
        (dock.reference, "addPanel", ("a", "A", "panel-a", {"position": {"referencePanel": "b", "direction": "right"}})),
        (table.reference, "scrollToEnd", ()),
    ]


def test_the_model_sugar_binds_the_value_and_writes_it_back_client_side() -> None:
    server = _server("model")
    with DivLayout(server):
        bound = Select(model="wb_series")
        explicit = Select(model="wb_series", on_value_change=react.Callback("noop()"))
        text = Input(model="wb_name")

    node = cast("dict[str, object]", bound.html)
    assert prop(node, "value") == {"js": "wb_series"}
    assert prop(node, "onValueChange") == {"callback": {"js": 'trame.state.set("wb_series", $event)'}}
    assert "model" not in props(node) and not any(name == "model" for name in bound._attr_names)

    assert prop(cast("dict[str, object]", explicit.html), "onValueChange") == {"callback": {"js": "noop()"}}

    text_node = cast("dict[str, object]", text.html)
    assert prop(text_node, "defaultValue") == {"js": "wb_name"}
    assert prop(text_node, "onBlur") == {"callback": {"js": 'trame.state.set("wb_name", $event.target.value)'}}
    assert prop(text_node, "onKeyDown") == {"callback": {"js": '$event.key === "Enter" && trame.state.set("wb_name", $event.target.value)'}}
    assert "value" not in props(text_node), "a controlled input would drop keystrokes on the round trip"
    assert prop(text_node, "name") == "wb_name", "Chrome reports an input with neither id nor name as unlabelled"


@pytest.mark.parametrize(
    ("wrapper", "bound", "event"),
    [
        (Accordion, "value", "onValueChange"),
        (Checkbox, "checked", "onCheckedChange"),
        (Slider, "value", "onValueChange"),
        (Tabs, "value", "onValueChange"),
        (Toggle, "pressed", "onPressedChange"),
        (ToggleGroup, "value", "onValueChange"),
        (Tree, "value", "onValueChange"),
    ],
    ids=["accordion", "checkbox", "slider", "tabs", "toggle", "toggle-group", "tree"],
)
def test_every_controlled_wrapper_binds_its_model_and_sets_it_on_change(wrapper: Wrapper, bound: str, event: str) -> None:
    factory = cast("Callable[..., Element]", wrapper)
    with DivLayout(_server(f"model-{wrapper.__name__}")):
        element = factory(model="wb_choice")

    node = cast("dict[str, object]", element.html)
    assert prop(node, bound) == {"js": "wb_choice"}
    assert prop(node, event) == {"callback": {"js": 'trame.state.set("wb_choice", $event)'}}


def test_the_text_wrappers_seed_from_the_model_and_commit_its_value() -> None:
    with DivLayout(_server("text-models")):
        area = Textarea(model="wb_notes")
        grouped = InputGroupInput(model="wb_query")
        colour = ColorInput(model="wb_colour")

    commit = {"callback": {"js": 'trame.state.set("wb_notes", $event.target.value)'}}
    area_node = cast("dict[str, object]", area.html)
    assert prop(area_node, "defaultValue") == {"js": "wb_notes"} and prop(area_node, "name") == "wb_notes"
    assert prop(area_node, "onBlur") == commit

    grouped_node = cast("dict[str, object]", grouped.html)
    assert prop(grouped_node, "defaultValue") == {"js": "wb_query"} and prop(grouped_node, "name") == "wb_query"
    assert prop(grouped_node, "onKeyDown") == {"callback": {"js": '$event.key === "Enter" && trame.state.set("wb_query", $event.target.value)'}}

    colour_node = cast("dict[str, object]", colour.html)
    assert prop(colour_node, "value") == {"js": "wb_colour"} and prop(colour_node, "type") == "color"
    assert prop(colour_node, "onChange") == {"callback": {"js": 'trame.state.set("wb_colour", $event.target.value)'}}


def test_the_dock_and_terminal_methods_call_their_js_counterparts(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server("methods")
    calls: list[tuple[str | None, str | None, tuple[object, ...]]] = []

    def record(ref: str | None = None, method: str | None = None, *args: object) -> None:
        calls.append((ref, method, args))

    monkeypatch.setattr(server, "js_call", record)
    with DivLayout(server):
        dock = Dock()
        term = Term()

    dock.remove_panel("a")
    dock.active_panel("b")
    dock.restore_layout({"grid": {}})
    dock.move_to("a", "b")
    dock.set_panel_title("a", "Alpha")
    dock.maximize("a")
    dock.restore_maximized()
    term.write("hi")
    term.clear()
    term.fit()
    term.scroll_to_bottom()

    assert calls == [
        (dock.reference, "removePanel", ("a",)),
        (dock.reference, "activePanel", ("b",)),
        (dock.reference, "restoreLayout", ({"grid": {}},)),
        (dock.reference, "moveTo", ("a", "b")),
        (dock.reference, "setTitle", ("a", "Alpha")),
        (dock.reference, "maximize", ("a",)),
        (dock.reference, "restoreMaximized", ()),
        (term.reference, "write", ("hi",)),
        (term.reference, "clear", ()),
        (term.reference, "fit", ()),
        (term.reference, "scrollToBottom", ()),
    ]


def test_the_shared_typed_dict_names_only_trame_shared_props_and_events() -> None:
    assert set(Shared.__annotations__) <= SHARED | {"connect_parent"}
    assert "className" not in Shared.__annotations__ and "classes" in Shared.__annotations__


@pytest.mark.parametrize(("name", "wrapper"), WRAPPERS, ids=[name for name, _ in WRAPPERS])
def test_every_wrapper_parameter_names_a_prop_of_its_tag_and_every_literal_matches_the_union(name: str, wrapper: Wrapper) -> None:
    element = _build(wrapper, _server("contract"))
    tag = element._elem_name
    assert tag in PROPS, f"{name} renders {tag}, which the dist does not register"

    listed = set(cast("list[str]", PROPS[tag]["props"]))
    literals = cast("dict[str, list[str]]", PROPS[tag]["literals"])
    for parameter, spec in _parameters(wrapper).items():
        if is_event(parameter) or parameter in FUNCTION_PROPS or spec.kind is spec.VAR_KEYWORD:
            continue

        react_prop = react_name(parameter)
        assert react_prop in listed, f"{name}.{parameter} -> {react_prop} is not a prop of {tag}"

        for alias in _literal_aliases(cast("object", spec.annotation)):
            assert react_prop in literals, f"{tag}.{react_prop} is not a literal union in the dist"
            assert sorted(_literal_values(alias)) == sorted(literals[react_prop]), f"{alias.__name__} differs from {tag}.{react_prop}"

        for typed in _typed_dicts(cast("object", spec.annotation)):
            for key, inner in cast("dict[str, object]", typed.__annotations__).items():
                for alias in _literal_aliases(inner):
                    nested = f"{react_prop}.{key}"
                    assert nested in literals, f"{tag}.{nested} is not a literal union in the dist"
                    assert sorted(_literal_values(alias)) == sorted(literals[nested]), f"{alias.__name__} differs from {tag}.{nested}"


def test_the_icon_names_equal_the_dist_list() -> None:
    assert sorted(_literal_values(IconName)) == sorted(ICONS)


def test_the_module_descriptor_serves_the_dist_with_the_hash_as_cache_buster() -> None:
    info = module.version()
    descriptor = module.descriptor()

    assert set(info) == {"hash", "bundle", "css"}
    assert descriptor["serve"] == {"__ff": str(module.SERVE_DIR)}
    assert descriptor["scripts"] == [f"__ff/{info['bundle']}?v={info['hash']}"]
    assert descriptor["styles"] == [f"__ff/{info['css']}?v={info['hash']}"]
    assert descriptor["react_use"] == ["Ff"]
    assert module.asset_url("popout.html") == "/__ff/popout.html"
    assert module.assets() == [f"/__ff/{info['bundle']}?v={info['hash']}", f"/__ff/{info['css']}?v={info['hash']}"]
    assert widgets.asset_url is module.asset_url and widgets.initialize is module.initialize


def test_a_missing_dist_names_the_build_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "SERVE_DIR", tmp_path)
    module.version.cache_clear()
    try:
        with pytest.raises(FileNotFoundError, match="npm run build"):
            _ = module.version()
    finally:
        module.version.cache_clear()


def test_a_dist_older_than_its_sources_reports_both_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "version", lambda: {"hash": "0000000000000000", "bundle": "ff.umd.js", "css": "ff.css"})

    assert module.stale() == ("0000000000000000", source_hash(module.PACKAGE_ROOT))


def test_an_installed_wheel_never_reports_a_stale_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "PACKAGE_ROOT", tmp_path)

    assert module.stale() is None


def test_initialize_enables_the_module_once_and_elements_render_into_the_tree() -> None:
    server = _server("initialize")
    module.initialize(server)
    scripts_before = list(cast("list[str]", server.state["trame__scripts"]))

    with DivLayout(server) as layout:
        Button("One", size="sm")

    assert cast("list[str]", server.state["trame__scripts"]) == scripts_before
    assert [prop(node, "size") for node in find(layout.html, "ff-button")] == ["sm"]
    assert "__ff" in server.serve
