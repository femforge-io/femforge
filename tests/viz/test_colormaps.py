"""ParaView colormap presets: shipped, distinct, and resolvable by name once registered."""

import numpy as np
import pytest

from femforge.viz.colormaps import paraview_presets


def test_the_shipped_presets_are_named_once_each() -> None:
    names = [preset["Name"] for preset in paraview_presets()]

    assert len(names) == len(set(names)) and "Fast" in names
    assert "Viridis" not in names, "a preset matching a matplotlib map is left out"


def test_a_registered_preset_resolves_through_pyvista_inside_the_unit_cube() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    from femforge.viz.colormaps import register_paraview

    register_paraview()
    colors = np.asarray(pv.get_cmap_safe("blot")(np.linspace(0.0, 1.0, 64)))  # pyright: ignore[reportUnknownMemberType]

    assert colors.min() >= 0.0 and colors.max() <= 1.0
    assert pv.get_cmap_safe("Fast").name == "Fast"  # pyright: ignore[reportUnknownMemberType]
