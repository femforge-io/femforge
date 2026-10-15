"""TagGrouper builds a tag hierarchy over tagged items, flattens it back with merged tags, and maps leaves and leaf buckets."""

from femforge.common.tags import HasTags, TagGroup, TagGrouper


class _Item(HasTags):
    def __init__(self, name: str, **tags: str) -> None:
        super().__init__()
        self.label: str = name
        self.set_tags(tags)


def _items() -> list[_Item]:
    return [_Item("a", Phase="0", Layer="1"), _Item("b", Phase="0", Layer="2"), _Item("c", Phase="1", Layer="1")]


def test_apply_nests_one_group_level_per_key_set() -> None:
    root = TagGrouper("Phase").then_by("Layer").apply(_items())
    assert root.tags == {}
    assert [group.tags for group in root] == [{"Phase": "0"}, {"Phase": "1"}]
    phase0 = root[0]
    assert isinstance(phase0, TagGroup)
    assert [group.tags for group in phase0] == [{"Layer": "1"}, {"Layer": "2"}]


def test_a_group_of_items_without_a_key_omits_that_key() -> None:
    items = [_Item("pad", Phase="0", CurrentDir="Entry"), _Item("trace", Phase="0"), _Item("board")]

    root = TagGrouper("Phase").then_by("CurrentDir").apply(items)
    leaves = root.flatten()

    assert [group.tags for group in root] == [{"Phase": "0"}, {}]
    phase0 = root[0]
    assert isinstance(phase0, TagGroup)
    assert [group.tags for group in phase0] == [{"CurrentDir": "Entry"}, {}]
    assert [leaf.tags for leaf in leaves] == [{"Phase": "0", "CurrentDir": "Entry"}, {"Phase": "0"}, {}]


def test_flatten_merges_the_hierarchy_tags_and_relabels_leaves() -> None:
    items = _items()
    for item in items:
        item.tags["Own"] = "kept"
    leaves = TagGrouper("Phase").then_by("Layer").apply(items).flatten()
    assert [leaf.label for leaf in leaves] == [leaf.tags_str() for leaf in leaves]
    assert all(leaf.tags["Own"] == "kept" for leaf in leaves)
    assert {leaf.tags["Phase"] for leaf in leaves} == {"0", "1"}


def test_grouper_without_keys_puts_everything_under_one_bucket() -> None:
    root = TagGrouper().apply(_items())
    assert len(root) == 1
    only = root[0]
    assert isinstance(only, TagGroup)
    assert only.tags == {} and len(only) == 3


def test_map_and_map_groups_keep_the_hierarchy() -> None:
    grouper = TagGrouper("Phase")
    mapped = grouper.map(_items(), lambda item: item.label.upper())
    first = mapped[0]
    assert isinstance(first, TagGroup) and list(first) == ["A", "B"]

    counted = grouper.map_groups(_items(), len)
    assert [list(group) for group in counted if isinstance(group, TagGroup)] == [[2], [1]]


def test_tree_renders_groups_and_leaves() -> None:
    tree = TagGrouper("Phase").apply(_items()).tree()
    assert len(tree.children) == 2
    assert len(tree.children[0].children) == 2
