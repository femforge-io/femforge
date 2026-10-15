"""2D (surface) physical groups from body adjacency: pure `partition_boundary_faces` logic plus a gmsh-gated integration case on two fragmented boxes."""

import pytest

from femforge.meshing.boundary import BoundaryGroup, BoundaryPartition, partition_boundary_faces


# -- pure partition logic (no gmsh) -----------------------------------------


def test_two_box_partition_yields_interface_and_externals():
    # A and B share face 100; A also owns faces 1-5, B owns 6-10.
    face_to_bodies: dict[int, set[str]] = {
        1: {"A"}, 2: {"A"}, 3: {"A"}, 4: {"A"}, 5: {"A"},
        100: {"A", "B"},
        6: {"B"}, 7: {"B"}, 8: {"B"}, 9: {"B"}, 10: {"B"},
    }
    parts = partition_boundary_faces(face_to_bodies)
    by_name = {p.name: p for p in parts}

    assert set(by_name) == {"A__B", "A__EXT", "B__EXT"}
    assert by_name["A__B"].bodies == ("A", "B")
    assert by_name["A__B"].face_tags == (100,)
    assert by_name["A__EXT"].face_tags == (1, 2, 3, 4, 5)
    assert by_name["B__EXT"].face_tags == (6, 7, 8, 9, 10)


def test_partition_is_total_every_face_covered_exactly_once():
    # gmsh's all-or-nothing rule needs every boundary face in exactly one group.
    face_to_bodies: dict[int, set[str]] = {1: {"A"}, 2: {"A", "B"}, 3: {"B"}, 4: {"B", "C"}, 5: {"C"}}
    parts = partition_boundary_faces(face_to_bodies)

    covered = [face for p in parts for face in p.face_tags]
    assert sorted(covered) == [1, 2, 3, 4, 5]
    assert len(covered) == len(set(covered))  # no face in two groups


def test_interface_name_is_sorted_for_determinism():
    # Body order in the face→bodies set must not leak into the name: B,A -> A__B.
    parts = partition_boundary_faces({7: {"B", "A"}})
    assert [p.name for p in parts] == ["A__B"]
    assert parts[0].bodies == ("A", "B")


def test_groups_emitted_in_sorted_order():
    # Emission sorted by body tuple -> deterministic gmsh id assignment: ("A",) < ("A","M") < ("Z",).
    parts = partition_boundary_faces({3: {"Z"}, 1: {"A"}, 2: {"A", "M"}})
    assert [p.name for p in parts] == ["A__EXT", "A__M", "Z__EXT"]


def test_three_body_face_joins_all_names():
    # A non-manifold face touched by three bodies still names deterministically (degenerate but defined).
    parts = partition_boundary_faces({1: {"C", "A", "B"}})
    assert parts[0].name == "A__B__C"
    assert parts[0].bodies == ("A", "B", "C")


def test_empty_adjacency_yields_no_groups():
    assert partition_boundary_faces({}) == []


def test_face_bounding_no_body_is_skipped():
    # A face mapped to an empty body set can't be named, so it produces no partition (the `continue` branch).
    assert partition_boundary_faces({1: set()}) == []
    # And it doesn't suppress a real face alongside it.
    parts = partition_boundary_faces({1: set(), 2: {"A"}})
    assert [p.name for p in parts] == ["A__EXT"]
    assert parts[0].face_tags == (2,)


def test_boundary_partition_is_a_frozen_record():
    p = BoundaryPartition(name="A__EXT", bodies=("A",), face_tags=(1, 2))
    with pytest.raises((AttributeError, TypeError)):
        p.name = "other"  # type: ignore[misc]  # frozen
    assert p.on_axis is False  # axis membership is opt-in via axis_faces


# -- symmetry-axis splitting (axis_faces) ------------------------------------


def test_axis_faces_split_out_of_the_external_group():
    # A single body with both true far-field and axis edges splits A__EXT + A__AXIS: one merged BC block per group
    # is pinned downstream, so a mixed group could not carry the far field and the axis condition separately.
    parts = partition_boundary_faces({1: {"A"}, 2: {"A"}, 3: {"A"}}, axis_faces=frozenset({3}))
    by_name = {p.name: p for p in parts}

    assert set(by_name) == {"A__EXT", "A__AXIS"}
    assert by_name["A__EXT"].face_tags == (1, 2)
    assert by_name["A__AXIS"].face_tags == (3,)
    assert by_name["A__AXIS"].on_axis and not by_name["A__EXT"].on_axis


def test_interface_face_in_axis_set_stays_an_interface():
    # Two bodies meeting on the axis is still an interface; axis_faces only splits single-body groups.
    parts = partition_boundary_faces({1: {"A", "B"}, 2: {"A"}}, axis_faces=frozenset({1, 2}))
    by_name = {p.name: p for p in parts}

    assert set(by_name) == {"A__B", "A__AXIS"}
    assert not by_name["A__B"].on_axis


def test_body_fully_on_axis_has_no_external_group():
    # The rod-on-the-axis shape: every single-body edge is on the axis, so no ROD__EXT is invented.
    parts = partition_boundary_faces({1: {"ROD"}, 2: {"AIR", "ROD"}}, axis_faces=frozenset({1}))
    assert [p.name for p in parts] == ["AIR__ROD", "ROD__AXIS"]


def test_empty_axis_set_is_identical_to_the_default():
    # The 3D path passes axis_faces=frozenset(): its output must be indistinguishable from the pre-axis behaviour.
    face_to_bodies: dict[int, set[str]] = {3: {"Z"}, 1: {"A"}, 2: {"A", "M"}}
    assert partition_boundary_faces(face_to_bodies, axis_faces=frozenset()) == partition_boundary_faces(face_to_bodies)


def test_axis_group_emitted_after_its_external_sibling():
    # Deterministic gmsh id assignment: sorted by (bodies, on_axis), so A__EXT precedes A__AXIS.
    parts = partition_boundary_faces({2: {"A"}, 1: {"A"}, 3: {"B"}}, axis_faces=frozenset({1}))
    assert [p.name for p in parts] == ["A__EXT", "A__AXIS", "B__EXT"]


def test_boundary_group_axis_flag_semantics():
    axis = BoundaryGroup(gmsh_id=1, name="AIR__AXIS", bodies=("AIR",), face_tags=[1], dim=1, on_axis=True)
    ext = BoundaryGroup(gmsh_id=2, name="AIR__EXT", bodies=("AIR",), face_tags=[2], dim=1)

    # An axis group is single-body but NOT external: external_only conditions and the far field must skip it.
    assert axis.is_axis and not axis.is_external
    assert ext.is_external and not ext.is_axis


def test_boundary_group_external_flag_and_protocol():
    ext = BoundaryGroup(gmsh_id=9, name="AIR__EXT", bodies=("AIR",), face_tags=[1, 2, 3])
    iface = BoundaryGroup(gmsh_id=10, name="AIR__N52_MAG_N", bodies=("AIR", "N52_MAG_N"), face_tags=[4])

    assert ext.is_external and not iface.is_external
    assert ext.dim == 2
    # Satisfies the same MeshGroupSource name contract as PhysicalGroup.
    assert ext.physical_group_name() == "AIR__EXT"


def test_boundary_group_dim_follows_the_model():
    # In a 2D planar model the boundaries are curves, so the group carries dim 1 instead of the 3D default.
    curve = BoundaryGroup(gmsh_id=1, name="AIR__EXT", bodies=("AIR",), face_tags=[1], dim=1)
    assert curve.dim == 1


# -- gmsh integration: real adjacency read off a built model ----------------


def test_gmsh_adjacency_on_two_fragmented_boxes():
    """Two fragmented boxes sharing one face: the real gmsh read sees the shared face as an interface and the
    rest as external shells, and gmsh accepts the resulting 2D physical groups while every volume survives."""
    pytest.importorskip("gmsh")
    import gmsh

    from femforge.meshing.boundary import read_face_adjacency

    if not gmsh.isInitialized():
        gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()

        left = gmsh.model.occ.addBox(0, 0, 0, 1, 1, 1)
        right = gmsh.model.occ.addBox(1, 0, 0, 1, 1, 1)  # touches `left` at the x=1 plane
        # Fragment so the coincident faces become one shared surface entity (conformal).
        gmsh.model.occ.fragment([(3, left)], [(3, right)])
        gmsh.model.occ.synchronize()

        volumes = gmsh.model.getEntities(dim=3)
        assert len(volumes) == 2, "fragment should leave exactly the two volumes"

        # Name each volume A/B by which side of x=1 its center of mass sits.
        body_groups: list[tuple[str, list[int]]] = []
        for _, tag in volumes:
            com = gmsh.model.occ.getCenterOfMass(3, tag)
            body_groups.append(("A" if com[0] < 1.0 else "B", [tag]))

        adjacency = read_face_adjacency(body_groups)
        parts = partition_boundary_faces(adjacency)
        by_name = {p.name: p for p in parts}

        assert set(by_name) == {"A__B", "A__EXT", "B__EXT"}
        assert len(by_name["A__B"].face_tags) == 1  # exactly the shared face
        assert len(by_name["A__EXT"].face_tags) == 5  # a cube minus its shared face
        assert len(by_name["B__EXT"].face_tags) == 5

        # gmsh must accept every group as a real 2D physical group with a positive id.
        for part in parts:
            gid = gmsh.model.addPhysicalGroup(2, list(part.face_tags), name=part.name)
            assert gid > 0

        # The 2D groups must not have dropped any volume (all-or-nothing is per-dimension).
        assert len(gmsh.model.getEntities(dim=3)) == 2
    finally:
        gmsh.clear()
