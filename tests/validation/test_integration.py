"""Gated end-to-end checks: `sources_from_assembly` over a real OCC assembly, and the analytical field near a magnet face is substantial."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from femforge.common.tags import TagKey
from femforge.common.vector import Direction
from femforge.physical.conditions import Magnetization
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.materials.air import Air
from femforge.materials.neodymium import N52

if TYPE_CHECKING:
    from build123d import Compound


# N52's Bᵣ(T) evaluated at the default 300 K operating point.
EXPECTED_REMANENCE_T = 1.48 * (1 - 0.0012 * (300 - 293.15))
CARDINAL_DIRECTIONS = {(0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0)}


def _domain() -> DomainConfig:
    """N52 magnets in air, one world-frame magnetization direction per cardinal slot."""
    directions: dict[str, Direction] = {"N": Direction(0, 1, 0), "E": Direction(1, 0, 0), "S": Direction(0, -1, 0), "W": Direction(-1, 0, 0)}
    return DomainConfig(
        materials=[N52(), Air()],
        tags=[EntityTag(selector={TagKey.MAGNETIZATION: name}, conditions=[Magnetization(direction=direction)]) for name, direction in directions.items()],
    )


def _build123d_assembly() -> Compound:
    """A build123d Compound: a 5 x 5 x 25.4 mm magnet for each cardinal slot plus an air box."""
    from build123d import Box, Compound, Pos

    slots = ("N", "E", "S", "W")
    magnets = [Pos(10.0 * i, 0, 0) * Box(5, 5, 25.4) for i in range(len(slots))]  # mm; spaced along x
    for magnet, slot in zip(magnets, slots):
        magnet.label = f"Magnetization={slot}, Material=N52, Slot=1"
    air = Box(200, 200, 200)
    air.label = "Material=Air"
    return Compound(children=[*magnets, air])


def test_sources_from_a_build123d_assembly() -> None:
    pytest.importorskip("build123d")
    from femforge.validation.sources import sources_from_assembly

    sources = sources_from_assembly(_build123d_assembly(), _domain())

    # One source per magnet leaf; the air box leaf is skipped (no remanence).
    assert len(sources) == 4

    for s in sources:
        # Polarization magnitude equals the remanence regardless of orientation.
        assert s.polarization.magnitude() == pytest.approx(EXPECTED_REMANENCE_T, rel=1e-6)
        # Sizes are in metres (mm -> m): a 5 x 5 x 25.4 mm block.
        extents = sorted([s.size.x, s.size.y, s.size.z])
        assert extents == pytest.approx([0.005, 0.005, 0.0254], abs=1e-6)

    # Each cardinal slot resolves to its authored world-frame direction.
    observed = {(round(d.x), round(d.y), round(d.z)) for d in (s.polarization.normalized() for s in sources)}
    assert observed == {(round(x), round(y), round(z)) for x, y, z in CARDINAL_DIRECTIONS}


def test_field_near_a_magnet_face_is_substantial() -> None:
    pytest.importorskip("build123d")
    pytest.importorskip("magpylib")
    import numpy as np
    from numpy.typing import NDArray

    from femforge.validation.field import sample_magpylib_b
    from femforge.validation.sources import sources_from_assembly

    sources = sources_from_assembly(_build123d_assembly(), _domain())
    # Mag_N at x=0, polarized +y, 5 mm cube -> face at y=2.5 mm; sample 2.5 mm above it, where |B| is large.
    b = sample_magpylib_b(sources, np.array([[0.0, 0.005, 0.0]], dtype=np.float64))
    norms: NDArray[np.float64] = cast("NDArray[np.float64]", np.linalg.norm(b, axis=1))
    magnitude: float = cast("list[float]", norms.tolist())[0]
    assert magnitude > 0.01  # tens of mT at least, a real field, not numerical noise
    assert magnitude < 10.0  # and not absurd
