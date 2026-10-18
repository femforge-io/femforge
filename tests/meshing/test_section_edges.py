"""The edge file of a 2D section: where it lives, how it reads back, and how imported curves match its edges."""

from pathlib import Path

import pytest

from femforge.common.files import BundleFile
from femforge.common.vector import Vec3
from femforge.meshing.backend import MeshError
from femforge.meshing.section_edges import MATCH_TOLERANCE, CurveSpan, SectionEdge, SectionEdges, edges_path, millimetres_per_unit


LEFT = CurveSpan(start=Vec3(0, 0, 0), end=Vec3(0, 20, 0), length=20.0)
RIGHT = CurveSpan(start=Vec3(100, 0, 0), end=Vec3(100, 20, 0), length=20.0)


def _edges() -> SectionEdges:
    return SectionEdges(edges=[SectionEdge(name="Name=Left", span=LEFT), SectionEdge(name="Name=Right", span=RIGHT)])


def test_the_edge_file_sits_beside_its_step_under_the_bundle_name() -> None:
    assert edges_path(Path("run/geometry.step")) == Path("run") / BundleFile.GEOMETRY_BOUNDARIES
    assert edges_path(Path("solve.step")).name == "solve.boundaries.json"


def test_a_model_unit_converts_to_millimetres() -> None:
    assert millimetres_per_unit(None) == 1.0
    assert millimetres_per_unit("M") == pytest.approx(1000.0)
    assert millimetres_per_unit("CM") == pytest.approx(10.0)


def test_a_span_matches_the_same_curve_either_way_round_within_the_tolerance() -> None:
    reversed_left = CurveSpan(start=Vec3(0, 20, 0), end=Vec3(0, 0, MATCH_TOLERANCE / 2), length=20.0)

    assert LEFT.matches(LEFT)
    assert LEFT.matches(reversed_left)
    assert not LEFT.matches(RIGHT)
    assert not LEFT.matches(CurveSpan(start=Vec3(0, 0, 0), end=Vec3(0, 20, 0), length=20.5))
    assert not LEFT.matches(CurveSpan(start=Vec3(0, 0, 0), end=Vec3(0, 21, 0), length=20.0))


def test_the_edges_write_and_load_back(tmp_path: Path) -> None:
    step = tmp_path / "geometry.step"

    assert SectionEdges.load(step) is None

    _edges().write(step)

    assert SectionEdges.load(step) == _edges()
    assert b"\r" not in edges_path(step).read_bytes()


def test_writing_no_edges_removes_a_stale_file(tmp_path: Path) -> None:
    step = tmp_path / "geometry.step"
    _edges().write(step)

    SectionEdges(edges=[]).write(step)

    assert not edges_path(step).exists()


def test_each_edge_claims_one_matching_curve() -> None:
    twin = SectionEdges(edges=[SectionEdge(name="Name=Left", span=LEFT), SectionEdge(name="Name=Twin", span=LEFT)])
    curves = {3: RIGHT, 5: LEFT, 6: LEFT}

    assert _edges().match(curves) == {5: "Name=Left", 3: "Name=Right"}
    assert twin.match(curves) == {5: "Name=Left", 6: "Name=Twin"}


def test_an_edge_no_curve_matches_raises() -> None:
    with pytest.raises(MeshError, match="'Name=Right' is missing from the imported STEP"):
        _ = _edges().match({5: LEFT})
