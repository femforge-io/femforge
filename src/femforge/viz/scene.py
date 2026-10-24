"""Composable render targets: a [`Scene`][femforge.viz.scene.Scene] draws several [`Renderable`][femforge.viz.scene.Renderable]s in one plotter refresh, on the plotter thread."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable


if TYPE_CHECKING:
    import pyvista as pv


@runtime_checkable
class Renderable(Protocol):
    """Something a workbench run returns for display: it draws itself into ``plotter`` (on the plotter thread)."""

    def render(self, plotter: pv.Plotter) -> None:
        """Draw into ``plotter``, on the plotter thread."""
        ...


class Scene:
    """
    An ordered group of [`Renderable`][]s drawn into one plotter refresh: clear once, draw in order (later items on top), repaint once.

    Scene is itself a [`Renderable`][], so scenes can nest.
    """

    def __init__(self, *items: Renderable) -> None:
        """Hold ``items`` in drawing order."""
        self.items: list[Renderable] = list(items)

    def add(self, item: Renderable) -> Scene:
        """Append a renderable to draw; returns ``self`` so calls chain."""
        self.items.append(item)
        return self

    def render(self, plotter: pv.Plotter) -> None:
        """Clear the plotter but keep its lights, draw every item in order, and repaint — once. Call on the plotter thread."""
        lights: list[pv.Light] = list(cast("list[pv.Light]", plotter.renderer.lights))
        plotter.clear()
        for light in lights:
            plotter.add_light(light)

        for item in self.items:
            item.render(plotter)
        plotter.render()
