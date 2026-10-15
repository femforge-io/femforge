"""The dock tree: authored layout JSON, add_panel placements and the saved-layout restore rule."""

from typing import cast

from femforge_workbench.dock import AXIS, Builder, Key, NodeType, Orientation, Panel, PlacementDirection, Split, Tabs, layout_json, panels, placements, restorable, template_name


def _build(app: object, id: str, title: str) -> object:
    return (app, id, title)


BUILD = cast("Builder", _build)


def _tree() -> Split:
    return Split(
        [
            Panel("Preview", BUILD, id="preview", weight=2),
            Split([Tabs([Panel("Figure", BUILD, id="figure"), Panel("Notes & Log", BUILD)]), Panel("Rows", BUILD, id="rows")]),
        ]
    )


def _ids(root: Split) -> list[str]:
    return [panel.panel_id for panel in panels(root)]


def _node(layout: dict[str, object], *path: str | int) -> dict[str, object]:
    current: object = layout
    for step in path:
        current = cast("dict[str, object]", current)[step] if isinstance(step, str) else cast("list[object]", current)[step]

    return cast("dict[str, object]", current)


def test_panels_come_in_authored_order_with_slugged_ids() -> None:
    assert _ids(_tree()) == ["preview", "figure", "notes-log", "rows"]
    assert template_name("rows") == "panel-rows"


def test_layout_json_alternates_orientation_and_turns_weights_into_sizes() -> None:
    layout = layout_json(_tree())

    grid = _node(layout, Key.GRID)
    root = _node(layout, Key.GRID, Key.ROOT)
    left = _node(layout, Key.GRID, Key.ROOT, Key.DATA, 0)
    right = _node(layout, Key.GRID, Key.ROOT, Key.DATA, 1)
    tabs = _node(layout, Key.GRID, Key.ROOT, Key.DATA, 1, Key.DATA, 0)
    rows = _node(layout, Key.GRID, Key.ROOT, Key.DATA, 1, Key.DATA, 1)

    assert grid[Key.ORIENTATION] == Orientation.HORIZONTAL
    assert grid[Key.WIDTH] == AXIS and grid[Key.HEIGHT] == AXIS
    assert root[Key.TYPE] == NodeType.BRANCH and root[Key.SIZE] == AXIS
    assert left[Key.TYPE] == NodeType.LEAF and left[Key.SIZE] == 667
    assert _node(left, Key.DATA)[Key.VIEWS] == ["preview"]
    assert right[Key.TYPE] == NodeType.BRANCH and right[Key.SIZE] == 333
    assert tabs[Key.SIZE] == 500 and rows[Key.SIZE] == 500
    assert _node(tabs, Key.DATA)[Key.VIEWS] == ["figure", "notes-log"]
    assert _node(tabs, Key.DATA)[Key.ACTIVE_VIEW] == "figure"
    assert _node(rows, Key.DATA)[Key.VIEWS] == ["rows"]


def test_layout_json_describes_every_panel_and_names_its_template() -> None:
    layout = layout_json(_tree())

    entries = _node(layout, Key.PANELS)
    figure = _node(layout, Key.PANELS, "figure")
    group_ids = [
        _node(layout, Key.GRID, Key.ROOT, Key.DATA, 0, Key.DATA)[Key.ID],
        _node(
            layout,
            Key.GRID,
            Key.ROOT,
            Key.DATA,
            1,
            Key.DATA,
            0,
            Key.DATA,
        )[Key.ID],
    ]

    assert sorted(entries) == ["figure", "notes-log", "preview", "rows"]
    assert figure[Key.TITLE] == "Figure"
    assert figure[Key.CONTENT_COMPONENT] == "DockPanel"
    assert _node(figure, Key.PARAMS)[Key.TEMPLATE_NAME] == "panel-figure"
    assert layout[Key.ACTIVE_GROUP] == group_ids[0]
    assert len(set(group_ids)) == 2


def test_placements_put_siblings_beside_or_below_and_tabs_within() -> None:
    steps = [(place.panel.panel_id, place.reference, place.direction) for place in placements(_tree())]

    assert steps == [
        ("preview", None, PlacementDirection.WITHIN),
        ("figure", "preview", PlacementDirection.RIGHT),
        ("notes-log", "figure", PlacementDirection.WITHIN),
        ("rows", "figure", PlacementDirection.BELOW),
    ]
    assert placements(_tree())[0].add_on() == {}
    assert placements(_tree())[1].add_on() == {Key.POSITION: {Key.REFERENCE_PANEL: "preview", Key.DIRECTION: "right"}}


def test_restorable_needs_exactly_the_authored_ids() -> None:
    tree = _tree()
    saved = layout_json(tree)

    restored = restorable(saved, _ids(tree))

    assert restored == saved
    assert restored is not saved
    assert restorable(saved, [*_ids(tree), "extra"]) is None
    assert restorable(saved, _ids(tree)[:-1]) is None
    assert restorable(None, _ids(tree)) is None
    gridless: dict[str, object] = {Key.PANELS: {name: {Key.ID: name} for name in _ids(tree)}}
    assert restorable(gridless, _ids(tree)) is None


def _detached_layout() -> dict[str, object]:
    def leaf(group_id: str, views: list[str]) -> dict[str, object]:
        return {Key.TYPE: str(NodeType.LEAF), Key.SIZE: 500, Key.DATA: {Key.ID: group_id, Key.VIEWS: views, Key.ACTIVE_VIEW: views[0]}}

    root: dict[str, object] = {Key.TYPE: str(NodeType.BRANCH), Key.SIZE: AXIS, Key.DATA: [leaf("g1", ["preview"]), leaf("g2", ["figure"])]}

    return {
        Key.GRID: {Key.ROOT: root, Key.WIDTH: AXIS, Key.HEIGHT: AXIS, Key.ORIENTATION: str(Orientation.HORIZONTAL)},
        Key.PANELS: {name: {Key.ID: name} for name in ("preview", "figure", "rows", "notes-log")},
        Key.POPOUT_GROUPS: [{Key.DATA: {Key.ID: "g9", Key.VIEWS: ["rows"], Key.ACTIVE_VIEW: "rows"}, "url": "/popout.html"}],
        Key.FLOATING_GROUPS: [{Key.DATA: {Key.ID: "g8", Key.VIEWS: ["notes-log", "preview"]}, "position": {}}],
        Key.ACTIVE_GROUP: "g9",
    }


def test_restorable_folds_popout_and_floating_groups_into_the_first_group() -> None:
    saved = _detached_layout()

    restored = restorable(saved, ["preview", "figure", "rows", "notes-log"])

    assert restored is not None
    home = _node(restored, Key.GRID, Key.ROOT, Key.DATA, 0, Key.DATA)
    assert home[Key.VIEWS] == ["preview", "rows", "notes-log"]
    assert home[Key.ACTIVE_VIEW] == "preview"
    assert restored[Key.ACTIVE_GROUP] == "g1"
    assert Key.POPOUT_GROUPS not in restored and Key.FLOATING_GROUPS not in restored
    assert Key.POPOUT_GROUPS in saved


def test_restorable_without_a_grid_group_is_none() -> None:
    saved = _detached_layout()
    _node(saved, Key.GRID, Key.ROOT)[Key.DATA] = []

    assert restorable(saved, ["preview", "figure", "rows", "notes-log"]) is None
