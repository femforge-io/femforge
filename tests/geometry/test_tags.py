"""HasTags metadata mixin, the Tagged* build123d subclasses, and the TagGrouper hierarchy."""

import inspect
from pathlib import Path
from typing import cast

import build123d as b3d
import pytest
from loguru import logger
from rich.tree import Tree

from femforge.common.tags import HasTags, TagGroup, TagGrouper, TagKey, clean_tag, parse_tags
from femforge.geometry.tags import TaggedCompound, TaggedEdge, TaggedFace, TaggedPart, TaggedSketch


def test_set_get_and_default() -> None:
    t = HasTags()
    assert t.get_tag("missing") is None
    t.set_tag("Phase", "1")
    assert t.get_tag("Phase") == "1"


def test_add_tag_aliases_set_tag() -> None:
    t = HasTags()
    t.add_tag("Phase", "2")
    assert t.get_tag("Phase") == "2"


def test_get_tags_all_vs_keys() -> None:
    t = HasTags()
    t.set_tag("Phase", "1")
    t.set_tag("Coil", "2")
    assert t.get_tags() == {"Phase": "1", "Coil": "2"}
    # Requested keys are always present; unknown keys default to "".
    assert t.get_tags(["Phase", "Missing"]) == {"Phase": "1", "Missing": ""}


def test_get_key_string() -> None:
    t = HasTags()
    t.set_tag("Phase", "1")
    t.set_tag("Coil", "2")
    assert t.get_key_string(["Phase", "Coil"]) == "Phase 1_Coil 2"
    # No keys -> the default fallback.
    assert t.get_key_string([]) == "default"
    assert t.get_key_string([], default="none") == "none"


def test_current_tags_is_a_copy() -> None:
    t = HasTags()
    t.set_tag("Phase", "1")
    snapshot = t.current_tags
    snapshot["Phase"] = "mutated"
    assert t.get_tag("Phase") == "1"


def test_remove_tag_present_and_absent() -> None:
    t = HasTags()
    t.set_tag("Phase", "1")
    t.remove_tag("Phase")
    assert t.get_tag("Phase") is None
    # Removing an absent key is a no-op.
    t.remove_tag("Phase")


def test_clear_and_set_tags() -> None:
    t = HasTags()
    t.set_tag("Phase", "1")
    t.clear_tags()
    assert t.get_tags() == {}
    source = {"Coil": "3"}
    t.set_tags(source)
    # set_tags stores a copy, not the caller's dict.
    source["Coil"] = "mutated"
    assert t.get_tag("Coil") == "3"


def _items() -> list[HasTags]:
    """2 phases x 2 layers, one item each."""
    return [HasTags({"Phase": phase, "Layer": layer}) for phase in ("0", "1") for layer in ("1", "2")]


def test_grouper_then_by_builds_nested_groups_and_flatten_merges_tags() -> None:
    root = TagGrouper("Phase").then_by("Layer").apply(_items())
    # Root (empty tags) -> 2 phase groups -> 2 layer groups each -> 1 leaf each.
    assert isinstance(root, TagGroup)
    assert root.tags == {}
    assert len(root) == 2
    leaves = root.flatten()
    assert len(leaves) == 4
    assert {(leaf.get_tag("Phase"), leaf.get_tag("Layer")) for leaf in leaves} == {("0", "1"), ("0", "2"), ("1", "1"), ("1", "2")}


def test_grouper_map_transforms_leaves_keeping_hierarchy() -> None:
    items = [HasTags({"Phase": phase}) for phase in ("0", "0", "1")]
    root = TagGrouper("Phase").map(items, lambda item: f"leaf-{item.get_tag('Phase')}")
    groups = [child for child in root if isinstance(child, TagGroup)]
    assert len(groups) == 2
    assert list(groups[0]) == ["leaf-0", "leaf-0"]
    assert list(groups[1]) == ["leaf-1"]
    # The group tags survive the mapping.
    assert groups[0].tags == {"Phase": "0"}


def test_tree_renders_groups_and_leaves() -> None:
    root = TagGrouper("Phase").then_by("Layer").apply(_items())
    tree = root.tree()
    assert isinstance(tree, Tree)
    # Root node fans out to the two phase groups.
    assert len(tree.children) == 2
    # Depth-first: phase group -> layer group -> leaf label.
    phase_node = tree.children[0]
    layer_node = phase_node.children[0]
    assert "Layer" in str(layer_node.label)
    assert len(layer_node.children) == 1


def test_flatten_and_tag_drops_edges_under_solids() -> None:
    # Leaves more than one dimension below the highest are construction geometry: a solid keeps its marker faces, not its edges.
    flattened = TaggedCompound(
        children=[
            TaggedPart([b3d.Box(1, 1, 1)], tags={"Material": "Copper"}),
            TaggedFace(b3d.Rectangle(1, 1).face(), tags={"Material": "Air"}),
            TaggedEdge(b3d.Line((0, 0), (1, 0)).edge(), tags={"Material": "Air"}),
        ]
    ).flatten_and_tag()

    labels = [str(child.label) for child in flattened.children]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
    assert labels == ["Material=Copper", "Material=Air"]


def test_flatten_and_tag_places_leaves_and_inherits_tags_without_touching_the_scene() -> None:
    part = TaggedPart(b3d.Box(1, 1, 1), tags={"Material": "Copper", "Type": "Leaf"})
    part.color = b3d.Color(1, 0, 0)
    face = TaggedFace(b3d.Rectangle(1, 1).face(), tags={"Material": "Air"})
    inner = TaggedCompound(children=[part, face], tags={"Group": "A", "Type": "Stack"})
    inner.location = b3d.Location((0, 0, 20))
    scene = TaggedCompound(children=[inner])
    scene.location = b3d.Location((5, 0, 0))

    flattened = scene.flatten_and_tag()

    leaves = cast("tuple[TaggedPart | TaggedFace, ...]", flattened.children)
    assert [leaf.current_tags for leaf in leaves] == [{"Group": "A", "Type": "Leaf", "Material": "Copper"}, {"Group": "A", "Type": "Stack", "Material": "Air"}]
    assert [tuple(leaf.center()) for leaf in leaves] == [pytest.approx((5.0, 0.0, 20.0)), pytest.approx((5.0, 0.0, 20.0))]
    assert leaves[0].color is not None
    assert cast("tuple[object, ...]", inner.children) == (part, face)
    assert cast("object", part.parent) is inner


def test_rebuild_labels_restamps_part_labels_from_their_tags() -> None:
    part = TaggedPart([b3d.Box(1, 1, 1)], tags={"Material": "Copper"})
    scene = TaggedCompound(children=[part])
    part.label = "stale"
    scene.rebuild_labels()
    assert part.label == "Material=Copper"


def test_export_step_can_skip_the_flatten(tmp_path: Path) -> None:
    scene = TaggedCompound(children=[TaggedPart([b3d.Box(1, 1, 1)], tags={"Material": "Copper"})])
    out: Path = tmp_path / "scene.step"
    scene.export_step(str(out), flatten=False)
    assert out.is_file()


def test_children_at_global_location_applies_the_parent_placement() -> None:
    scene = TaggedCompound(children=[TaggedPart([b3d.Box(1, 1, 1)], tags={"Material": "Copper"})])
    scene.location = b3d.Location((10, 0, 0))
    placed = scene.children_at_global_location
    assert len(placed) == 1
    assert placed[0].center().X == pytest.approx(10.0)


def test_tagged_subclasses_carry_tags() -> None:
    box = b3d.Box(1, 1, 1)
    face = b3d.Circle(1).face()
    tagged: list[HasTags] = [
        TaggedCompound(children=[box]),
        TaggedSketch(),
        TaggedFace(face),
        TaggedPart(box),
    ]
    for shape in tagged:
        shape.set_tag("Phase", "1")
        assert shape.get_tag("Phase") == "1"


def test_tagged_containers_declare_tags_as_a_keyword_parameter() -> None:
    shapes: list[HasTags] = [
        TaggedCompound(children=[], tags={"Type": "Stack"}),
        TaggedSketch(tags={"Type": "Stack"}),
        TaggedEdge(b3d.Line((0, 0), (1, 0)).edge(), tags={"Type": "Stack"}),
    ]

    kinds = [inspect.signature(type(shape)).parameters["tags"].kind for shape in shapes]

    assert [shape.tags for shape in shapes] == [{"Type": "Stack"}] * 3
    assert kinds == [inspect.Parameter.KEYWORD_ONLY] * 3


def test_clean_tag_strips_unparseable_value_characters_with_a_warning() -> None:
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        assert clean_tag("Name", "SEAT") == "SEAT"
        assert not messages

        assert clean_tag("Name", "Bearing_Seat, rev/2") == "BearingSeatrev2"
    finally:
        logger.remove(sink)
    assert len(messages) == 1 and "cannot carry" in messages[0]


def test_clean_tag_rejects_an_unparseable_key_or_an_emptied_value() -> None:
    with pytest.raises(ValueError, match="Tag key"):
        _ = clean_tag("Part Name", "SEAT")
    with pytest.raises(ValueError, match="empty"):
        _ = clean_tag("Name", "_ /")


def test_has_tags_cleans_values_on_every_entry_point() -> None:
    t = HasTags({"Name": "A_B"})
    assert t.tags == {"Name": "AB"}
    t.set_tag("Phase", "1 2")
    t.set_tags({"Name": "C D"})
    assert t.tags == {"Name": "CD"}


def test_tagged_part_and_face_carry_their_name_into_the_step_label() -> None:
    part = TaggedPart([b3d.Box(1, 1, 1)], tags={TagKey.MATERIAL: "N52"}, name="Seat")
    assert part.get_tag(TagKey.NAME) == "Seat"
    assert parse_tags(part.label) == {"Material": "N52", "Name": "Seat"}

    face = TaggedFace(b3d.Rectangle(1, 1).face(), name="Bearing")
    assert face.tags == {"Name": "Bearing"}

    assert TaggedPart([b3d.Box(1, 1, 1)], tags={TagKey.MATERIAL: "N52"}).get_tag(TagKey.NAME) is None


def test_a_tagged_edge_takes_a_name_like_a_face() -> None:
    edge = TaggedEdge(b3d.Line((0, 0), (0, 20)).edge(), tags={"Side": "Left"}, name="LeftFace")

    assert edge.current_tags == {"Side": "Left", "Name": "LeftFace"}
    assert edge.label == "Side=Left, Name=LeftFace"
    assert (TaggedPart.dim, TaggedFace.dim, TaggedEdge.dim) == (3, 2, 1)


def test_a_faces_and_edges_compound_keeps_its_edges_placed_and_tagged() -> None:
    face = TaggedFace(b3d.Rectangle(10, 4).face(), tags={"Material": "Steel316"})
    edge = TaggedEdge(b3d.Line((-5, -2), (-5, 2)).edge(), name="Left")
    edge.color = b3d.Color("red")
    section = TaggedCompound(children=[TaggedCompound(children=[face, edge], tags={"Group": "A"})])
    section.location = b3d.Location((0, 0, 7))

    leaves = section.placed_leaves(b3d.Location((1, 0, 0)))

    assert [type(leaf).__name__ for leaf in leaves] == ["TaggedFace", "TaggedEdge"]
    assert leaves[1].current_tags == {"Group": "A", "Name": "Left"}
    assert tuple(leaves[1].center()) == pytest.approx((-4.0, 0.0, 7.0))
    assert leaves[1].color is not None
