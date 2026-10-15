"""
Scene composition (viz.scene): clears once, draws each renderable in order, repaints once.

Pure orchestration — driven through a fake plotter / fake renderables, so it needs no pyvista render context.
"""

from __future__ import annotations

from typing import cast

from femforge.viz.scene import Renderable, Scene


class FakePlotter:
    """Records the clear / render / draw calls in order (the duck-typed surface Scene drives)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def clear(self) -> None:
        self.calls.append("clear")

    def render(self) -> None:
        self.calls.append("render")


class FakeRenderable:
    """A Renderable that logs its draw against the plotter and remembers what it was handed."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.got: object = None

    def render(self, plotter: object) -> None:
        self.got = plotter
        cast("FakePlotter", plotter).calls.append(f"draw:{self.name}")


def test_scene_renders_clear_then_items_in_order_then_repaint() -> None:
    plotter = FakePlotter()
    a, b = FakeRenderable("a"), FakeRenderable("b")
    Scene(a, b).render(cast("object", plotter))  # pyright: ignore[reportArgumentType]  # structural fake stands in for pv.Plotter
    assert plotter.calls == ["clear", "draw:a", "draw:b", "render"]
    # Each item is handed the same plotter to draw into.
    assert a.got is plotter and b.got is plotter


def test_scene_add_appends_and_chains() -> None:
    scene = Scene()
    returned = scene.add(FakeRenderable("x")).add(FakeRenderable("y"))
    assert returned is scene  # add() returns self so calls chain
    assert [cast("FakeRenderable", i).name for i in scene.items] == ["x", "y"]


def test_fakes_satisfy_the_renderable_protocol() -> None:
    # Scene and a single item are both Renderable (structural), so scenes can nest.
    assert isinstance(FakeRenderable("z"), Renderable)
    assert isinstance(Scene(), Renderable)
