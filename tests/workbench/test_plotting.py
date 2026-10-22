"""xyz_to_meshgrid spreads scattered samples onto a regular grid."""

import numpy as np

from femforge_workbench.plotting import xyz_to_meshgrid


def test_scattered_samples_of_a_plane_interpolate_onto_the_plane() -> None:
    x = [0.0, 1.0, 0.0, 1.0, 0.5]
    y = [0.0, 0.0, 1.0, 1.0, 0.5]
    z = [2.0 * xi + yi for xi, yi in zip(x, y, strict=True)]

    grid_x, grid_y, grid_z = xyz_to_meshgrid(x, y, z, grid_points=5)

    expected = 2.0 * np.asarray(grid_x)[None, :] + np.asarray(grid_y)[:, None]
    assert np.asarray(grid_z).shape == (5, 5)
    np.testing.assert_allclose(grid_z, expected, atol=1e-6)
    np.testing.assert_allclose(grid_x, np.linspace(0.0, 1.0, 5))
