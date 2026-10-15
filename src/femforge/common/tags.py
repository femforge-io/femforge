# pyright: reportUnknownParameterType=false
# pyright: reportMissingParameterType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportExplicitAny=false

"""Tags: the shared key vocabulary, tagged objects and the grouping of tagged items into hierarchies."""

import re
from enum import StrEnum
from typing import Any, Callable, Iterable, cast

from rich.tree import Tree


type TagDict = dict[TagKey | str, str]
"""Tag key=value pairs; a key is a [`TagKey`][] or a user-defined name."""


class TagKey(StrEnum):
    """The shared tag-key vocabulary: geometry authors these on parts, meshing/FEA select by them."""

    MATERIAL = "Material"
    """The registry tag of the body's material."""

    PHASE = "Phase"
    """The winding phase a conductor belongs to."""

    CURRENT_DIR = "CurrentDir"
    """Whether an electrode is a current entry or exit, a [`CurrentDir`][femforge.common.tags.CurrentDir] value."""

    SEGMENT = "Segment"
    """The winding segment a conductor belongs to."""

    LAYER = "Layer"
    """The layer a conductor lies on, such as a PCB copper layer."""

    TYPE = "Type"
    """The kind of part, such as ``Trace`` or ``Via``."""

    MAGNETIZATION = "Magnetization"
    """A magnet's magnetization label, such as ``N`` or ``S``."""

    SLOT = "Slot"
    """The pole or slot position a part occupies."""

    NAME = "Name"
    """The authored part name; when set it is the body's name everywhere (mesh group, Elmer body, entities.json, viewer)."""


class CurrentDir(StrEnum):
    """Values of [`TagKey.CURRENT_DIR`][]: whether an electrode part is a current entry or exit."""

    ENTRY = "Entry"
    """The electrode where current enters."""

    EXIT = "Exit"
    """The electrode where current leaves."""


TAG_PAIR_PATTERN = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=([^/,\s_]+)")
TAG_KEY_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
ILLEGAL_TAG_VALUE_CHARS = re.compile(r"[/,\s_]")


def clean_tag(key: str, value: str) -> str:
    """The tag value with the characters [`TAG_PAIR_PATTERN`][] cannot carry removed, warning when any were; raises on an unparseable key or an empty value."""
    if not TAG_KEY_PATTERN.fullmatch(key):
        raise ValueError(f"Tag key {key!r} must match {TAG_KEY_PATTERN.pattern} to survive the STEP name.")

    cleaned = ILLEGAL_TAG_VALUE_CHARS.sub("", value)
    if cleaned != value:
        # Imported here so loguru stays off the import path of every config.
        from femforge.common.log import log

        log.warning(f"Tag {key}={value!r} contains characters a tag value cannot carry ('/', ',', '_' or whitespace); using {cleaned!r}.")
    if not cleaned:
        raise ValueError(f"Tag {key}={value!r} is empty once '/', ',', '_' and whitespace are removed.")
    return cleaned


def parse_tags(text: str) -> TagDict:
    """The ``key=value`` pairs found in a label / STEP-name path; a repeated key keeps its last (deepest) value."""
    return dict(TAG_PAIR_PATTERN.findall(text))


class HasTags:
    """A mixin that holds an object's tag key=value pairs."""

    def __init__(self, tags: TagDict | None = None):
        """Start with ``tags``, each value cleaned, or with none."""
        self.tags: TagDict = {key: clean_tag(key, value) for key, value in tags.items()} if tags is not None else {}

    def set_tag(self, key: str, value: str):
        """Set ``key`` to the cleaned ``value``."""
        self.tags[key] = clean_tag(key, value)

    def add_tag(self, key: str, value: str):
        """Set ``key`` to the cleaned ``value``, like ``set_tag``."""
        self.set_tag(key, value)

    def get_tag(self, key: str, default: str | None = None) -> str | None:
        """The value of ``key``, or ``default`` when it is unset."""
        return self.tags.get(key, default)

    def get_tags(self, keys: list[str] | None = None) -> TagDict:
        """A copy of the tags, or of only ``keys`` with an unset key as an empty string."""
        if keys is None:
            return self.tags.copy()
        return {key: self.tags.get(key, "") for key in keys}

    def get_key_string(self, keys: list[str], default: str = "default") -> str:
        """Return a string representation of the tags for the given keys."""
        return "_".join([f"{key} {value}" for key, value in self.get_tags(keys).items()]) or default

    @property
    def current_tags(self) -> TagDict:
        """A copy of the tags."""
        return self.tags.copy()

    def remove_tag(self, key: str):
        """Drop ``key`` when it is set."""
        if key in self.tags:
            del self.tags[key]

    def clear_tags(self):
        """Drop every tag."""
        self.tags.clear()

    def set_tags(self, tags: TagDict):
        """Replace every tag with ``tags``, each value cleaned."""
        self.tags = {key: clean_tag(key, value) for key, value in tags.items()}

    def tags_str(self) -> str:
        """The tags as comma-separated ``key=value`` pairs."""
        return ", ".join([f"{key}={value}" for key, value in self.tags.items()])

    def __repr__(self) -> str:
        return f"HasTags(tags={self.tags})"


class TagGroup[T](HasTags, list[T]):
    """A list of tagged items or nested groups that carries its own tags."""

    def __init__(self, tags: TagDict, items: Iterable[T] = ()) -> None:
        """Hold ``items`` under ``tags``."""
        list.__init__(self, items)
        HasTags.__init__(self, tags)

    def __repr__(self) -> str:
        return f"TagGroup({self.tags}, {list.__repr__(self)})"

    def flatten[X: HasTags](self: "TagGroup[X | TagGroup[Any]]") -> list[X]:
        """Roll the hierarchy up into a flat list of leaves, merging each path's tags onto the item (its own tags win only for keys the hierarchy doesn't set). Mutates `.tags` in place."""
        out: list[X] = []

        def visit(node: object, inherited: TagDict) -> None:
            if isinstance(node, TagGroup):
                merged = {**inherited, **node.tags}
                for child in node:
                    visit(child, merged)
            else:
                item = cast(X, node)

                item.tags = {**item.tags, **inherited}

                if isinstance(getattr(item, "label", None), str):
                    setattr(item, "label", item.tags_str())

                out.append(item)

        visit(self, {})
        return out

    def tree(self, parent: Tree | None = None) -> Tree:
        """Return a tree representation of the tag group."""
        # Tree.add(label) creates-and-appends a child node from a label; passing a Tree object would nest it as a label, not a child.
        label = self.tags_str()
        node = parent.add(label) if parent is not None else Tree(label=label)

        for item in self:
            if isinstance(item, TagGroup):
                item.tree(parent=node)
            else:
                node.add(str(item))

        return node


# A node is either a leaf X or a group whose children are nodes, to any depth.
type Nested[X] = X | TagGroup[Nested[X]]
"""A leaf ``X`` or a [`TagGroup`][femforge.common.tags.TagGroup] of nested nodes."""

type Levels = list[list[str]]
"""The tag keys to group by at each level, outermost first."""


def _normalize(keys: list[str] | str | None) -> list[str]:
    if keys is None:
        return []
    return [keys] if isinstance(keys, str) else list(keys)


def _group_levels[TItem: HasTags](items: list[TItem], levels: Levels) -> list[Any]:
    """The raw items once out of levels, else one TagGroup per bucket carrying only the keys its items have."""
    if not levels:
        return list(items)

    head, *tail = levels
    buckets: dict[tuple[str, ...], list[TItem]] = {}
    for it in items:
        key = tuple(it.tags.get(k, "") for k in head)
        buckets.setdefault(key, []).append(it)

    # A tag value is never empty, so "" in a bucket key marks an absent tag.
    return [TagGroup({k: v for k, v in zip(head, key) if v}, _group_levels(group, tail)) for key, group in buckets.items()]


def _map_node[Y](node: object, fn: Callable[[Any], Y]) -> object:
    if isinstance(node, TagGroup):
        return TagGroup(node.tags, [_map_node(child, fn) for child in node])
    return fn(node)


def map_leaves[X, Y](root: TagGroup[Nested[X]], fn: Callable[[X], Y]) -> TagGroup[Nested[Y]]:
    """Transform every leaf X -> Y, keeping the hierarchy identical."""
    return cast("TagGroup[Nested[Y]]", _map_node(root, fn))


def map_group_leaves[X, Y](root: TagGroup[Nested[X]], fn: Callable[[list[X]], Y]) -> TagGroup[Nested[Y]]:
    """Collapse each deepest bucket of sibling leaves (a list[X]) into one Y,
    keeping its enclosing group and tags."""

    def map_group(group: TagGroup[Nested[X]]) -> TagGroup[Nested[Y]]:
        # a leaf-group has only non-group children
        if len(group) and all(not isinstance(child, TagGroup) for child in group):
            return TagGroup(group.tags, [fn(cast("list[X]", group))])
        return TagGroup(
            group.tags,
            [map_group(cast("TagGroup[Nested[X]]", child)) for child in group],
        )

    return map_group(root)


class TagGrouper(HasTags):
    """Groups tagged items into a [`TagGroup`][femforge.common.tags.TagGroup] hierarchy, one level per list of tag keys."""

    def __init__(self, keys: list[str] | str | None = None, *, _levels: Levels | None = None) -> None:
        """Group by ``keys`` at the first level."""
        super().__init__()
        self._levels: Levels = _levels if _levels is not None else [_normalize(keys)]

    def then_by(self, keys: list[str] | str) -> "TagGrouper":
        """A grouper with ``keys`` added as the next level."""
        return TagGrouper(_levels=[*self._levels, _normalize(keys)])

    def apply[X: HasTags](self, items: list[X]) -> TagGroup[Nested[X]]:
        """Group items into a single root TagGroup (root has empty tags)."""
        return TagGroup({}, _group_levels(items, self._levels))

    def map[X: HasTags, Y](self, items: list[X], fn: Callable[[X], Y]) -> TagGroup[Nested[Y]]:
        """Group ``items`` and transform each leaf with ``fn``."""
        return map_leaves(self.apply(items), fn)

    def map_groups[X: HasTags, Y](self, items: list[X], fn: Callable[[list[X]], Y]) -> TagGroup[Nested[Y]]:
        """Group ``items`` and collapse each deepest bucket of leaves with ``fn``."""
        return map_group_leaves(self.apply(items), fn)
