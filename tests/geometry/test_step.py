"""Tagged STEP import: assembly names and placements, and the rejected unnamed body."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import build123d as b3d
import pytest

from femforge.geometry.step import import_tagged_step
from femforge.geometry.tags import TaggedCompound, TaggedPart


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
