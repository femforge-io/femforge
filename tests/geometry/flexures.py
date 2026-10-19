"""The notch and toroidal flexure shapes, rebuilt for the frame checks."""

from __future__ import annotations

from typing import cast

import build123d as b3d


LENGTH = 100.0
WIDTH = 20.0
THICKNESS = 10.0
NECK_WIDTH = 5.0
NECK_LENGTH = 50.0


def _profile() -> b3d.Sketch:
    """The 100 x 20 rectangle centred on the origin, necked by two circles that leave a 5 mm waist over a 50 mm chord."""
    half_chord = NECK_LENGTH / 2
    rim = (WIDTH - NECK_WIDTH) / 2
    radius = (half_chord**2 + rim**2) / (2 * rim) + rim
    sketch = b3d.Rectangle(LENGTH, WIDTH)

    for side in (-1, 1):
        sketch -= b3d.Circle(radius).locate(b3d.Location((0, side * (radius + NECK_WIDTH / 2), 0)))

    return cast("b3d.Sketch", sketch)


def notch_flexure() -> b3d.Part:
    """The necked profile extruded 10 mm along +Z."""
    return cast("b3d.Part", b3d.extrude(_profile(), THICKNESS))


def toroidal_flexure() -> b3d.Part:
    """The lower half of the necked profile revolved about X."""
    half = _profile() - b3d.Rectangle(LENGTH, WIDTH).locate(b3d.Location((0, WIDTH / 2, 0)))

    return cast("b3d.Part", b3d.revolve(cast("b3d.Sketch", half).face(), b3d.Axis.X))
