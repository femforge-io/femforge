"""Tagged STEP import: assembly names and placements, named faces, and the rejected entries."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import build123d as b3d
import pytest

from femforge.geometry.step import import_tagged_step
from femforge.geometry.tags import TaggedCompound, TaggedEdge, TaggedFace, TaggedPart


def test_import_inherits_assembly_names_and_placements(tmp_path: Path) -> None:
    magnet = TaggedPart(b3d.Box(1, 1, 1), tags={"Material": "N42"})
    stack = TaggedCompound(children=[magnet], tags={"Type": "Stack"})
    stack.location = b3d.Location((0, 0, 5))
    TaggedCompound(children=[stack]).export_step(str(tmp_path / "nested.step"), flatten=False)

    parts = [child for child in cast("tuple[object, ...]", import_tagged_step(tmp_path / "nested.step").children) if isinstance(child, TaggedPart)]
    assert [part.current_tags for part in parts] == [{"Type": "Stack", "Material": "N42"}]
    assert parts[0].bounding_box().center().Z == pytest.approx(5.0)
    assert parts[0].volume == pytest.approx(1.0)


def test_an_unnamed_body_raises(tmp_path: Path) -> None:
    TaggedCompound(children=[TaggedPart(b3d.Box(1, 1, 1))]).export_step(str(tmp_path / "bare.step"))

    with pytest.raises(ValueError, match="no key=value name"):
        _ = import_tagged_step(tmp_path / "bare.step")


def _beam_with(leaf: TaggedFace | TaggedEdge) -> TaggedCompound:
    box = b3d.Box(10, 2, 2)
    beam = TaggedPart([box], tags={"Material": "Steel316"}, name="Beam")
    beam.color = b3d.Color("gray")
    leaf.color = b3d.Color("red")
    return TaggedCompound(children=[beam, leaf])


def test_a_named_sheet_face_imports_as_a_tagged_face(tmp_path: Path) -> None:
    root = TaggedFace(b3d.Box(10, 2, 2).faces().sort_by(b3d.Axis.X)[0], name="Root")
    _beam_with(root).export_step(str(tmp_path / "faces.step"))

    children = cast("tuple[object, ...]", import_tagged_step(tmp_path / "faces.step").children)

    assert [type(child).__name__ for child in children] == ["TaggedPart", "TaggedFace"]
    face = cast("TaggedFace", children[1])
    assert face.current_tags == {"Name": "Root"}
    assert face.area == pytest.approx(4.0)
    assert face.center().X == pytest.approx(-5.0)


def test_an_entry_with_neither_solid_nor_face_raises(tmp_path: Path) -> None:
    wire = TaggedEdge(b3d.Box(10, 2, 2).edges()[0], tags={"Name": "Wire"})
    _beam_with(wire).export_step(str(tmp_path / "edge.step"), flatten=False)

    with pytest.raises(ValueError, match="neither a solid nor a face"):
        _ = import_tagged_step(tmp_path / "edge.step")
