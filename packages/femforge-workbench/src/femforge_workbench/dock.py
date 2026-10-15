"""The dock tree: panels as tabs in splits and tab groups, its dockview layout JSON, and the rule for restoring a saved arrangement."""

import copy
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from femforge.common.log import log


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench
    from femforge_workbench.panels import PanelComponent


type Builder = Callable[[Workbench, str, str], PanelComponent]
"""Makes a panel's component from the shell, the panel id and the title."""

type Layout = dict[str, object]
"""A dockview layout document as JSON."""

type Node = Panel | Tabs | Split
"""A node of the dock tree: a panel, a tab group or a split."""

AXIS = 1000
"""The nominal extent of each axis the weights are shared over; dockview rescales proportionally."""

CONTENT_COMPONENT = "DockPanel"
"""The Dock component that hosts a server template inside a panel, as Dock.tsx names it."""

TEMPLATE_PREFIX = "panel-"


class Orientation(StrEnum):
    """The axis a dockview branch lays its children along."""

    HORIZONTAL = "HORIZONTAL"
    VERTICAL = "VERTICAL"

    def flipped(self) -> "Orientation":
        """The other orientation."""
        return Orientation.VERTICAL if self is Orientation.HORIZONTAL else Orientation.HORIZONTAL


class PlacementDirection(StrEnum):
    """Where add_panel puts a panel relative to its reference."""

    WITHIN = "within"
    RIGHT = "right"
    BELOW = "below"


class Key(StrEnum):
    """The keys of a dockview layout JSON."""

    GRID = "grid"
    PANELS = "panels"
    ACTIVE_GROUP = "activeGroup"
    POPOUT_GROUPS = "popoutGroups"
    FLOATING_GROUPS = "floatingGroups"
    ROOT = "root"
    WIDTH = "width"
    HEIGHT = "height"
    ORIENTATION = "orientation"
    TYPE = "type"
    SIZE = "size"
    DATA = "data"
    ID = "id"
    VIEWS = "views"
    ACTIVE_VIEW = "activeView"
    TITLE = "title"
    CONTENT_COMPONENT = "contentComponent"
    PARAMS = "params"
    TEMPLATE_NAME = "templateName"
    POSITION = "position"
    REFERENCE_PANEL = "referencePanel"
    DIRECTION = "direction"


class NodeType(StrEnum):
    """The two node kinds of a dockview grid."""

    BRANCH = "branch"
    LEAF = "leaf"


def template_name(panel_id: str) -> str:
    """The trame template a panel renders through."""
    return f"{TEMPLATE_PREFIX}{panel_id}"


@dataclass(frozen=True)
class Panel:
    """One tab: its title, the builder that makes its component once the shell exists, and its share of the axis."""

    name: str
    """The tab title, from which the panel id is derived when `id` is None."""

    build: Builder
    """Makes the panel's component from the shell, the panel id and the title, once the shell exists."""

    weight: float = 1
    """`[-]`: The panel's share of its parent's axis, relative to its siblings' weights."""

    id: str | None = None
    """The panel id; None derives it from `name`."""

    @property
    def panel_id(self) -> str:
        """The id the dock knows the panel by: `id` when given, else `name` lowercased with dashes."""
        if self.id is not None:
            return str(self.id)

        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")


@dataclass(frozen=True)
class Tabs:
    """Panels sharing one tab group, the first shown."""

    panels: Sequence[Panel]
    """The panels sharing the tab group, the first one shown."""

    weight: float = 1
    """`[-]`: The group's share of its parent's axis, relative to its siblings' weights."""


@dataclass(frozen=True)
class Split:
    """Panels, tab groups or splits sharing one axis by weight; the axis alternates with nesting, horizontal at the root."""

    children: Sequence[Node]
    """The panels, tab groups and splits along the axis, in order."""

    weight: float = 1
    """`[-]`: The split's share of its parent's axis, relative to its siblings' weights; ignored at the root."""


@dataclass(frozen=True)
class Placement:
    """Where add_panel puts one panel: beside, below or within the group of a reference panel."""

    panel: Panel
    reference: str | None
    direction: PlacementDirection

    def add_on(self) -> Layout:
        """The position add-on for add_panel, empty for the first panel."""
        if self.reference is None:
            return {}

        return {Key.POSITION: {Key.REFERENCE_PANEL: self.reference, Key.DIRECTION: str(self.direction)}}


def panels(node: Node) -> list[Panel]:
    """Every panel under a node, in authored order."""
    if isinstance(node, Panel):
        return [node]

    children = node.panels if isinstance(node, Tabs) else node.children

    return [panel for child in children for panel in panels(child)]


def panel_ids(node: Node) -> list[str]:
    """The ids of every panel under a node, in authored order."""
    return [panel.panel_id for panel in panels(node)]


def placements(root: Split) -> list[Placement]:
    """The add_panel sequence that rebuilds the tree group by group: each sibling beside or below the previous one, tabs within the first."""
    out: list[Placement] = []

    def visit(node: Node, orientation: Orientation, reference: str | None, direction: PlacementDirection) -> str:
        if isinstance(node, Panel):
            out.append(Placement(node, reference, direction))

            return node.panel_id

        if isinstance(node, Tabs):
            first = visit(node.panels[0], orientation, reference, direction)
            for panel in node.panels[1:]:
                out.append(Placement(panel, first, PlacementDirection.WITHIN))

            return first

        beside = PlacementDirection.RIGHT if orientation is Orientation.HORIZONTAL else PlacementDirection.BELOW
        first = visit(node.children[0], orientation.flipped(), reference, direction)
        previous = first
        for child in node.children[1:]:
            previous = visit(child, orientation.flipped(), previous, beside)

        return first

    _ = visit(root, Orientation.HORIZONTAL, None, PlacementDirection.WITHIN)

    return out


def layout_json(root: Split) -> Layout:
    """The dockview serialization of the authored tree: splits alternate orientation from a horizontal root, weights become sizes, each leaf one tab group."""
    groups = 0

    def leaf(members: Sequence[Panel], size: int) -> Layout:
        nonlocal groups
        groups += 1
        ids = [panel.panel_id for panel in members]
        data: Layout = {Key.ID: f"group-{groups}", Key.VIEWS: ids, Key.ACTIVE_VIEW: ids[0]}

        return {Key.TYPE: str(NodeType.LEAF), Key.SIZE: size, Key.DATA: data}

    def branch(split: Split, size: int) -> Layout:
        total = sum(child.weight for child in split.children)
        data = [node(child, round(AXIS * child.weight / total)) for child in split.children]

        return {Key.TYPE: str(NodeType.BRANCH), Key.SIZE: size, Key.DATA: data}

    def node(child: Node, size: int) -> Layout:
        if isinstance(child, Panel):
            return leaf([child], size)

        if isinstance(child, Tabs):
            return leaf(child.panels, size)

        return branch(child, size)

    grid: Layout = {Key.ROOT: branch(root, AXIS), Key.WIDTH: AXIS, Key.HEIGHT: AXIS, Key.ORIENTATION: str(Orientation.HORIZONTAL)}
    entries: dict[str, Layout] = {
        panel.panel_id: {
            Key.ID: panel.panel_id,
            Key.TITLE: panel.name,
            Key.CONTENT_COMPONENT: CONTENT_COMPONENT,
            Key.PARAMS: {Key.TEMPLATE_NAME: template_name(panel.panel_id)},
        }
        for panel in panels(root)
    }

    return {Key.GRID: grid, Key.PANELS: entries, Key.ACTIVE_GROUP: "group-1"}


def restorable(saved: Mapping[str, object] | None, ids: Sequence[str]) -> Layout | None:
    """The saved layout when it names exactly the authored panels, with popout and floating groups folded into the first grid group; else None."""
    if saved is None:
        return None

    def mapping(value: object) -> Layout | None:
        if not isinstance(value, Mapping):
            return None

        return cast("Layout", value)

    def first_leaf(node: Layout) -> Layout | None:
        data = node.get(Key.DATA)
        if node.get(Key.TYPE) == NodeType.LEAF:
            return mapping(data)

        children = cast("list[object]", data) if isinstance(data, list) else []
        for child in children:
            branch = mapping(child)
            found = first_leaf(branch) if branch is not None else None
            if found is not None:
                return found

        return None

    layout = copy.deepcopy(dict(saved))
    entries = mapping(layout.get(Key.PANELS))
    if entries is None or sorted(entries) != sorted(ids):
        log.bind(saved=sorted(entries or {}), authored=sorted(ids)).warning("Saved layout ignored; panel ids differ.")
        return None

    grid = mapping(layout.get(Key.GRID))
    root = mapping(grid.get(Key.ROOT)) if grid is not None else None
    home = first_leaf(root) if root is not None else None
    if home is None:
        log.warning("Saved layout ignored; no group left in the grid.")
        return None

    views = cast("list[str]", home.setdefault(Key.VIEWS, []))
    for key in (Key.POPOUT_GROUPS, Key.FLOATING_GROUPS):
        detached = layout.pop(key, None)
        groups = cast("list[object]", detached) if isinstance(detached, list) else []
        for group in groups:
            entry = mapping(group)
            data = mapping(entry.get(Key.DATA)) if entry is not None else None
            folded = data.get(Key.VIEWS) if data is not None else None
            if isinstance(folded, list):
                views.extend(view for view in cast("list[str]", folded) if view not in views)

    home[Key.ACTIVE_VIEW] = home.get(Key.ACTIVE_VIEW) or views[0]
    layout[Key.ACTIVE_GROUP] = home.get(Key.ID)

    return layout
