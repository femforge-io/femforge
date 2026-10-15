"""Rect / NestedRect layout math and the air-domain boundary_box helper."""

import build123d as b3d
import pytest
from anytree import PreOrderIter

from femforge.common.vector import Vec3
from femforge.geometry.tags import TaggedPart
from femforge.geometry.utils import NestedRect, Rect, all_colliinear, boundary_box, collinear_ends, compound_topology, drop_collinear_points, remove_where


V = b3d.Vector


def _rect() -> Rect:
    r = Rect(label="r")
    r.left = 1.0
    r.right = 5.0
    r.bottom = 2.0
    r.top = 8.0
    return r


def test_rect_edge_accessors_roundtrip() -> None:
    r = _rect()
    assert (r.left, r.right, r.bottom, r.top) == (1.0, 5.0, 2.0, 8.0)
    assert r.width == pytest.approx(4.0)
    assert r.height == pytest.approx(6.0)
    assert r.size == V(4.0, 6.0)
    assert r.center == V(3.0, 5.0)


def test_rect_corners_and_points_wind_counterclockwise() -> None:
    r = _rect()
    assert r.corners == [V(1, 2), V(5, 2), V(5, 8), V(1, 8)]
    assert r.points == r.corners


def test_rect_add_sub_offset() -> None:
    r = _rect()
    moved = r + V(1, 1)
    assert (moved.left, moved.bottom) == (2.0, 3.0)
    back = moved - V(1, 1)
    assert (back.left, back.bottom) == (1.0, 2.0)
    # In-place variants mutate.
    r += V(2, 0)
    assert r.left == 3.0
    r -= V(2, 0)
    assert r.left == 1.0


def test_rect_repr_and_format() -> None:
    r = _rect()
    assert repr(r) == "Rect((1.000, 2.000), (5.000, 8.000), r)"
    assert f"{r:.1f}" == "Rect((1.0, 2.0), (5.0, 8.0), r)"


def _nested() -> NestedRect:
    n = NestedRect(label="n")
    n.outside.left, n.outside.right = 0.0, 10.0
    n.outside.bottom, n.outside.top = 0.0, 10.0
    n.inside.left, n.inside.right = 2.0, 8.0
    n.inside.bottom, n.inside.top = 2.0, 8.0
    return n


def test_nested_rect_add_sub_offset_both_rects() -> None:
    n = _nested()
    moved = n + V(1, 0)
    assert moved.inside.left == 3.0 and moved.outside.left == 1.0
    back = moved - V(1, 0)
    assert back.inside.left == 2.0 and back.outside.left == 0.0
    n += V(0, 1)
    assert n.inside.bottom == 3.0 and n.outside.bottom == 1.0
    n -= V(0, 1)
    assert n.inside.bottom == 2.0 and n.outside.bottom == 0.0


def test_nested_rect_repr_mentions_both_rects() -> None:
    text = repr(_nested())
    assert text.count("Rect(") >= 2
    assert "n" in text


def test_nested_rect_face_and_extrude_are_annular() -> None:
    n = _nested()
    face = n.face()
    # Outside 10x10 minus inside 6x6.
    assert face.area == pytest.approx(100.0 - 36.0)
    part = n.extrude(thickness=2.0)
    assert part.volume == pytest.approx((100.0 - 36.0) * 2.0)
    assert part.label == f"{n.label}_Part"


def test_boundary_box_pads_the_shape_bounds() -> None:
    box = b3d.Compound(children=[b3d.Box(2, 4, 6)])
    padded = boundary_box(box, padding=Vec3(2, 2, 2))
    # Each axis grows by the padding (half per side).
    assert padded.bounding_box().size.X == pytest.approx(4.0)
    assert padded.bounding_box().size.Y == pytest.approx(6.0)
    assert padded.bounding_box().size.Z == pytest.approx(8.0)
    assert padded.label == "Boundary Box"


def _tagged_part(label: str, tags: dict[str, str]) -> TaggedPart:
    part = TaggedPart(b3d.Box(1, 1, 1))
    part.label = label
    part.set_tags(tags)
    return part


def test_remove_where_filters_nodes_and_vacuums_empty_compounds() -> None:
    drop = _tagged_part("drop", {})
    keep = _tagged_part("keep", {})
    nested = b3d.Compound(children=[_tagged_part("drop", {})], label="nested")
    root = b3d.Compound(children=[keep, drop, nested], label="root")

    result = remove_where(root, lambda node: str(node.label) == "drop")

    labels = [str(node.label) for node in PreOrderIter(result)]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
    assert "drop" not in labels
    assert "keep" in labels
    assert "nested" not in labels, "a compound left empty by the filter is vacuumed too"


def test_collinearity_helpers_short_lists() -> None:
    assert all_colliinear([V(0, 0), V(1, 1)]), "fewer than three points are trivially collinear"
    single = [V(2, 3)]
    assert collinear_ends(single) == single


def test_all_collinear_checks_every_point_against_the_first_segment() -> None:
    assert all_colliinear([V(0, 0), V(1, 0), V(2, 0), V(5, 0)])
    assert not all_colliinear([V(0, 0), V(1, 0), V(2, 0), V(2, 1)])


def test_collinear_ends_returns_the_extreme_points_along_the_line() -> None:
    # The first two points orient the line, so the ends come back in that direction.
    assert collinear_ends([V(1, 0), V(2, 0), V(0, 0), V(3, 0)]) == [V(0, 0), V(3, 0)]


def test_drop_collinear_points_keeps_corners_and_endpoints() -> None:
    points = [V(0, 0), V(1, 0), V(2, 0), V(2, 1)]
    assert drop_collinear_points(points) == [V(0, 0), V(2, 0), V(2, 1)]


def test_compound_topology_renders_the_tree() -> None:
    nested = b3d.Compound(children=[_tagged_part("leaf", {})], label="inner")
    root = b3d.Compound(children=[nested], label="outer")
    tree = compound_topology(root)
    assert "outer" in str(tree.label)
    assert len(tree.children) == 1
    assert "inner" in str(tree.children[0].label)
    assert "leaf" in str(tree.children[0].children[0].label)
