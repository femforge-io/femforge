"""The Elmer ``Constants`` block."""

from __future__ import annotations

from typing import Annotated

from femforge.common.pydantic import Map
from femforge.elmer.config.common.keywords import SifKeywordModel


STEFAN_BOLTZMANN: float = 5.6704e-8
"""`σ_SB` `[W/(m²·K⁴)]`: The Stefan-Boltzmann constant thermal radiation needs."""


class Constants(SifKeywordModel):
    """The Elmer ``Constants`` block: each constant is an optional field carrying its sif keyword; a physics sets only the constants it needs, and an unset one is omitted."""

    permeability_of_vacuum: Annotated[float | None, Map("Permeability of Vacuum")] = None
    """`μ₀` `[H/m]`: Needed by the magnetostatic solver."""

    permittivity_of_vacuum: Annotated[float | None, Map("Permittivity of Vacuum")] = None
    """`ε₀` `[F/m]`: Needed by the electrostatic solver."""

    stefan_boltzmann: Annotated[float | None, Map("Stefan Boltzmann")] = None
    """`σ_SB` `[W/(m²·K⁴)]`: Needed for thermal radiation."""
