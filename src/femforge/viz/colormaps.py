"""ParaView's colormap presets, registered with matplotlib so every colormap name resolves through pyvista."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import TypedDict, cast

import numpy as np


PARAVIEW_PRESETS = "paraview_colormaps.json"
"""ParaView's exported presets less the ones matching a catalogue or matplotlib map."""

SAMPLES = 256


class Preset(TypedDict):
    """One ParaView preset as its JSON export writes it."""

    Name: str
    ColorSpace: str
    RGBPoints: list[float]


@cache
def paraview_presets() -> tuple[Preset, ...]:
    """The shipped ParaView presets in file order."""
    text = resources.files(__package__).joinpath(PARAVIEW_PRESETS).read_text(encoding="utf-8")

    return tuple(cast("list[Preset]", json.loads(text)))


def preset_colors(preset: Preset, count: int = SAMPLES) -> np.typing.NDArray[np.float64]:  # pragma: no cover - needs VTK (viz extra); proven by the gated integration test
    """``count`` RGB rows through a preset, interpolated in its own color space as ParaView does."""
    from vtkmodules.vtkRenderingCore import vtkColorTransferFunction

    transfer = vtkColorTransferFunction()
    spaces = {
        "RGB": transfer.SetColorSpaceToRGB,
        "HSV": transfer.SetColorSpaceToHSV,
        "Lab": transfer.SetColorSpaceToLab,
        "CIELAB": transfer.SetColorSpaceToLabCIEDE2000,
        "Diverging": transfer.SetColorSpaceToDiverging,
        "Step": transfer.SetColorSpaceToStep,
    }
    spaces[preset["ColorSpace"]]()

    flat = preset["RGBPoints"]
    points = [flat[index : index + 4] for index in range(0, len(flat), 4)]
    low, high = points[0][0], points[-1][0]
    for x, red, green, blue in points:
        _ = transfer.AddRGBPoint((x - low) / (high - low), red, green, blue)

    # HSV presets such as blot overshoot 1.0 by rounding.
    return np.clip(np.asarray([transfer.GetColor(float(t)) for t in np.linspace(0.0, 1.0, count)], dtype=np.float64), 0.0, 1.0)


@cache
def register_paraview() -> None:  # pragma: no cover - needs VTK (viz extra); proven by the gated integration test
    """Registers every shipped preset with matplotlib under its ParaView name, once per process."""
    from matplotlib import colormaps
    from matplotlib.colors import ListedColormap

    for preset in paraview_presets():
        if preset["Name"] not in colormaps:
            colormaps.register(ListedColormap(preset_colors(preset), name=preset["Name"]))
