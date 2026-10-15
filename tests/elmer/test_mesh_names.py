"""Reconcile gmsh physical-group ids with the ids ElmerGrid assigns. ElmerGrid renumbers 2D groups, so the sif must target the renumbered id, not the gmsh tag."""

from dataclasses import dataclass

from femforge.elmer.mesh_names import MeshNames, parse_mesh_names, reconcile_ids

# ElmerGrid mesh.names: bodies and boundaries each numbered from 1, so boundary ids (1,2,3) differ from the gmsh tags (3,4,5).
SAMPLE = """\
! ----- names for bodies -----
$ FR4 = 1
$ AIR = 2
! ----- names for boundaries -----
$ AIR__EXT = 1
$ AIR__FR4 = 2
$ FR4__EXT = 3
"""


@dataclass
class _Group:
    """Minimal stand-in for PhysicalGroup / BoundaryGroup (name + writable id)."""

    name: str
    id: int


def test_parse_splits_bodies_and_boundaries() -> None:
    names = parse_mesh_names(SAMPLE)
    assert names.bodies == {"FR4": 1, "AIR": 2}
    assert names.boundaries == {"AIR__EXT": 1, "AIR__FR4": 2, "FR4__EXT": 3}


def test_parse_ignores_preamble_and_malformed_lines() -> None:
    text = "garbage before any header\n$ Stray = 9\n! ----- names for boundaries -----\n$ A__EXT = 4\n$ bad = notanint\n"
    names = parse_mesh_names(text)
    assert names.bodies == {}  # the stray `$` line precedes any section header, so it's dropped
    assert names.boundaries == {"A__EXT": 4}  # the non-integer rhs is skipped


def test_reconcile_overwrites_gmsh_tags_with_elmer_ids() -> None:
    # Boundary groups still carry their gmsh tags (3,4,5); reconcile must shift them to the Elmer ids (1,2,3).
    bodies = [_Group("FR4", 1), _Group("AIR", 2)]
    boundaries = [_Group("AIR__EXT", 3), _Group("AIR__FR4", 4), _Group("FR4__EXT", 5)]
    missing = reconcile_ids(parse_mesh_names(SAMPLE), bodies, boundaries)

    assert missing == []
    assert [(b.name, b.id) for b in boundaries] == [("AIR__EXT", 1), ("AIR__FR4", 2), ("FR4__EXT", 3)]
    assert [(b.name, b.id) for b in bodies] == [("FR4", 1), ("AIR", 2)]


def test_reconcile_reports_names_absent_from_mesh_names() -> None:
    # A group whose name ElmerGrid didn't record (e.g. truncated) is left unchanged and reported.
    groups = [_Group("AIR__EXT", 3), _Group("MYSTERY", 99)]
    missing = reconcile_ids(MeshNames(bodies={}, boundaries={"AIR__EXT": 1}), [], groups)

    assert missing == ["MYSTERY"]
    assert groups[0].id == 1  # the known one is fixed
    assert groups[1].id == 99  # the unknown one is untouched


def test_reconcile_reports_missing_body() -> None:
    # A *body* group whose name ElmerGrid didn't record is left unchanged and reported (the body-loop's missing branch).
    bodies = [_Group("FR4", 5), _Group("GHOST_BODY", 8)]
    missing = reconcile_ids(MeshNames(bodies={"FR4": 1}, boundaries={}), bodies, [])

    assert missing == ["GHOST_BODY"]
    assert bodies[0].id == 1  # the known body is reconciled
    assert bodies[1].id == 8  # the unknown body is untouched
