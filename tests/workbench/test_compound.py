"""CompoundRenderable: the instanced draw, the empty-compound placeholder and the opt-in OCP viewer push."""

import sys
import types

import pytest

pytest.importorskip("pyvista")
pytest.importorskip("build123d")

import build123d as b3d  # imported after the gate so the file still collects where the CAD stack is absent
import pyvista as pv

from femforge_workbench.compound import EMPTY_LABEL, CompoundRenderable
from femforge_workbench.settings import Setting


def _box() -> b3d.Compound:
    return b3d.Compound(children=[b3d.Box(2.0, 2.0, 2.0)], label="box")


def test_renders_the_compound_into_an_off_screen_plotter_keeping_its_background() -> None:
    renderable = CompoundRenderable(_box())
    plotter = pv.Plotter(off_screen=True)
    plotter.background_color = pv.Color("#123456")

    renderable.render(plotter)

    # One instanced actor for the single leaf, plus the silhouette actor drawn beside it.
    assert len(renderable.inst.actors) == 1
    assert len(plotter.renderer.actors) == 2
    assert plotter.parallel_projection
    assert plotter.background_color == pv.Color("#123456")

    plotter.close()


def test_an_empty_compound_draws_the_placeholder() -> None:
    renderable = CompoundRenderable(b3d.Compound(label="nothing"))

    assert renderable.compound.label == EMPTY_LABEL
    assert renderable.inst.actors


def test_the_ocp_viewer_push_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[object] = []

    def show(compound: object, **_: object) -> None:
        shown.append(compound)

    viewer = types.ModuleType("ocp_vscode")
    monkeypatch.setattr(viewer, "show", show, raising=False)
    monkeypatch.setitem(sys.modules, "ocp_vscode", viewer)
    monkeypatch.delenv(Setting.OCP_VSCODE, raising=False)

    _ = CompoundRenderable(_box())
    assert shown == []

    monkeypatch.setenv(Setting.OCP_VSCODE, "1")
    _ = CompoundRenderable(_box())
    assert len(shown) == 1

    _ = CompoundRenderable(_box(), ocp_vscode=False)
    assert len(shown) == 1


def test_the_ocp_viewer_push_without_ocp_vscode_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "ocp_vscode", None)

    with pytest.raises(ModuleNotFoundError) as caught:
        _ = CompoundRenderable(_box(), ocp_vscode=True)

    assert caught.value.name == "ocp_vscode"
    assert 'pip install "femforge-workbench[ocp]"' in str(caught.value)
