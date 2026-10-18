"""TaggedCompound.fingerprint: leaf order, the STEP round trip, edge leaves, integration noise, scale and symmetric changes."""

from pathlib import Path

import build123d as b3d

from femforge.geometry.step import import_tagged_step
from femforge.geometry.tags import TaggedCompound, TaggedEdge, TaggedFace, TaggedPart


def _leaves() -> list[TaggedPart | TaggedFace]:
    box = b3d.Box(10, 4, 6)
    bar = TaggedPart([box], tags={"Material": "Steel316"}, name="Bar")
    bar.color = b3d.Color("gray")
    left = TaggedFace(box.faces().sort_by(b3d.Axis.X)[0], name="Left")
    left.color = b3d.Color(1.0, 0.0, 0.0, 0.25)
    rod = TaggedPart([b3d.Cylinder(2, 5)], tags={"Material": "Air"})
    rod.location = b3d.Location((0, 0, 20))
    return [bar, left, rod]


def _model() -> TaggedCompound:
    bar, left, rod = _leaves()
    stack = TaggedCompound(children=[bar, left], tags={"Layer": "Top"})
    stack.location = b3d.Location((3, 1, 2))
    return TaggedCompound(children=[stack, rod])


def test_the_fingerprint_ignores_the_order_of_the_leaves() -> None:
    leaves = _leaves()

    assert TaggedCompound(children=leaves).fingerprint() == TaggedCompound(children=leaves[::-1]).fingerprint()


def test_the_fingerprint_survives_the_step_round_trip(tmp_path: Path) -> None:
    model = _model()
    step = tmp_path / "model.step"
    model.export_step(str(step))

    assert import_tagged_step(step).fingerprint() == model.fingerprint()


def test_the_fingerprint_moves_with_an_edge_leaf_of_a_section() -> None:
    face = TaggedFace(b3d.Rectangle(10, 4).face(), tags={"Material": "Steel316"})

    def section(top: float) -> TaggedCompound:
        return TaggedCompound(children=[face, TaggedEdge(b3d.Line((-5, -2), (-5, top)).edge(), name="Left")])

    assert section(2.0).fingerprint() == section(2.0).fingerprint()
    assert section(2.0).fingerprint() != section(1.0).fingerprint()


def test_the_fingerprint_rounds_integration_noise_away() -> None:
    def bar(shift: float) -> TaggedCompound:
        part = TaggedPart([b3d.Box(10, 4, 6)], tags={"Material": "Steel316"})
        part.location = b3d.Location((shift, 0, 0))
        return TaggedCompound(children=[part])

    assert bar(0.0).fingerprint() == bar(1e-13).fingerprint()
    assert bar(0.0).fingerprint() != bar(1e-3).fingerprint()


def test_the_fingerprint_moves_with_changes_that_keep_the_mass_properties() -> None:
    def plate(cuts: list[b3d.Part]) -> str:
        return TaggedCompound(children=[TaggedPart([b3d.Cylinder(20, 5) - cuts], tags={"Material": "Steel316"})]).fingerprint()

    def holes(x: float, y: float) -> list[b3d.Part]:
        return [b3d.Pos(x, y, 0) * b3d.Cylinder(2, 5), b3d.Pos(-x, -y, 0) * b3d.Cylinder(2, 5)]

    assert plate([b3d.Box(8, 8, 5)]) != plate([b3d.Box(8, 8, 5).rotate(b3d.Axis.Z, 30)])
    assert plate(holes(8, 0)) != plate(holes(0, 8))


def test_the_fingerprint_moves_with_a_leaf_scaled_about_the_origin() -> None:
    def scaled(factor: float) -> str:
        bar = b3d.Pos(5, 1, 2) * b3d.Box(20, 4, 6)
        return TaggedCompound(children=[TaggedPart([bar.scale(factor)], tags={"Material": "Steel316"}, name="Bar")]).fingerprint()

    assert scaled(1.0) != scaled(2.0)
    assert scaled(1.0) != scaled(1.1)
