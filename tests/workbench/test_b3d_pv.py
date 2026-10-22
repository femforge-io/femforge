"""Build123Instancer: one mapper per unique shape, empty leaves skipped, and the silhouette, feature-edge and window helpers."""

import pytest


pytest.importorskip("pyvista")
pytest.importorskip("build123d")

import build123d as b3d
import pyvista as pv

from femforge_workbench.b3d_pv import Build123Instancer


def _assembly() -> b3d.Compound:
    box = b3d.Box(1.0, 1.0, 1.0)
    edge = b3d.Edge.make_line((0, 0, 0), (1, 0, 0))

    return b3d.Compound(children=[box, box.moved(b3d.Location((5, 0, 0))), edge, edge.moved(b3d.Location((0, 3, 0)))], label="assembly")


def test_repeated_shapes_share_one_mapper_and_shapes_without_faces_draw_nothing() -> None:
    instancer = Build123Instancer().parse(_assembly())

    assert len(instancer.actors) == 2
    assert instancer.actors[0].GetMapper() is instancer.actors[1].GetMapper()
    assert [actor.GetObjectName() for actor in instancer.actors] == ["Unnamed", "Unnamed"]


def test_an_empty_compound_has_no_actors() -> None:
    assert Build123Instancer().parse(b3d.Compound(label="nothing")).actors == []


def test_silhouettes_and_feature_edges_follow_every_instance() -> None:
    instancer = Build123Instancer().parse(_assembly())
    plotter = pv.Plotter(off_screen=True)

    rims = instancer.add_silhouettes(plotter, feature_angle=None)
    edges = instancer.add_feature_edges(plotter, color=(1.0, 0.0, 0.0), boundary_edges=False)

    assert len(rims) == len(edges) == 2
    assert edges[0].GetMapper() is edges[1].GetMapper(), "the edges of one shape are computed once"
    assert len(plotter.renderer.actors) == 4

    plotter.close()


def test_show_opens_a_plotter_holding_every_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[pv.Plotter] = []
    plotter_class = pv.Plotter

    def off_screen() -> pv.Plotter:
        plotter = plotter_class(off_screen=True)
        opened.append(plotter)

        return plotter

    monkeypatch.setattr(pv, "Plotter", off_screen)
    instancer = Build123Instancer().parse(_assembly())

    shown = instancer.show(auto_close=False)  # pyright: ignore[reportUnknownMemberType]  # show forwards untyped kwargs

    assert opened == [shown] and len(shown.renderer.actors) == 2

    shown.close()
