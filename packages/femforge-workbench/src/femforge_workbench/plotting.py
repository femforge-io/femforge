"""Figure helpers: the regular grid a scatter of samples needs before it can draw as a surface."""

from typing import Sequence, cast

import numpy as np
import pandas as pd


type PlotData = Sequence[int] | Sequence[float] | np.ndarray[tuple[int, ...], np.dtype[np.float64]] | pd.Series[float]


def xyz_to_meshgrid(x: PlotData, y: PlotData, z: PlotData, grid_points: int = 100) -> tuple[PlotData, PlotData, PlotData]:
    """Cubic-interpolates scattered (x, y, z) samples onto a regular grid for surface plotting.

    Args:
        x: The samples' x coordinates.
        y: The samples' y coordinates.
        z: The sample values.
        grid_points: The grid's point count along each axis.
    """
    from scipy.interpolate import griddata  # pyright: ignore[reportUnknownVariableType]

    grid_x = np.linspace(min(x), max(x), grid_points)
    grid_y = np.linspace(min(y), max(y), grid_points)
    x2, y2 = np.meshgrid(grid_x, grid_y)
    grid_z = cast(
        np.ndarray,
        griddata(
            points=(x, y),
            values=z,
            xi=(x2, y2),
            method="cubic",
        ),
    )
    return grid_x, grid_y, grid_z
