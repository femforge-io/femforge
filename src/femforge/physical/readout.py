"""Short readouts of quantities and directions, at three significant figures on fixed unit ladders."""

from __future__ import annotations

import math
from dataclasses import dataclass

from femforge.common.vector import Vec3
from femforge.physical.units import Quantity, U, Unit, VectorQuantity, quantity


__all__ = [
    "Rung",
    "Ladder",
    "LENGTH",
    "AREA",
    "FORCE",
    "PRESSURE",
    "CURRENT_DENSITY",
    "VOLTAGE",
    "TEMPERATURE",
    "HEAT_FLUX",
    "HEAT_TRANSFER",
    "POWER_DENSITY",
    "CURRENT",
    "FLUX_DENSITY",
    "LADDERS",
    "readout",
    "along",
    "vector_readout",
]

SIGNIFICANT_DIGITS = 3
"""The significant figures every readout keeps."""

ZERO_COMPONENT = 1e-9
"""The largest direction component that still reads as zero."""


@dataclass(frozen=True)
class Rung:
    """One unit a ladder can read a quantity in: its pint expression and the label it prints."""

    unit: str
    """The pint unit expression, such as ``mm**2``."""

    label: str
    """The unit as the readout prints it, such as ``mm²``."""


@dataclass(frozen=True)
class Ladder:
    """The units one dimensionality reads in, smallest first."""

    rungs: tuple[Rung, ...]
    """The rungs, smallest unit first."""

    def matches(self, value: Quantity) -> bool:
        """Whether ``value`` has this ladder's dimensionality."""
        return quantity(1.0, self.rungs[0].unit).dimensionality == value.dimensionality


LENGTH = Ladder((Rung("mm", "mm"), Rung("m", "m")))
"""Lengths in mm or m."""

AREA = Ladder((Rung("mm**2", "mm²"), Rung("m**2", "m²")))
"""Areas in mm² or m²."""

FORCE = Ladder((Rung("N", "N"), Rung("kN", "kN"), Rung("MN", "MN")))
"""Forces in N, kN or MN."""

PRESSURE = Ladder((Rung("Pa", "Pa"), Rung("kPa", "kPa"), Rung("MPa", "MPa"), Rung("GPa", "GPa")))
"""Pressures and tractions in Pa up to GPa."""

CURRENT_DENSITY = Ladder((Rung("A/m**2", "A/m²"), Rung("A/mm**2", "A/mm²")))
"""Current densities in A/m² or A/mm²."""

VOLTAGE = Ladder((Rung("V", "V"), Rung("kV", "kV")))
"""Voltages in V or kV."""

TEMPERATURE = Ladder((Rung("K", "K"),))
"""Temperatures in K."""

HEAT_FLUX = Ladder((Rung("W/m**2", "W/m²"), Rung("kW/m**2", "kW/m²"), Rung("MW/m**2", "MW/m²")))
"""Heat fluxes in W/m² up to MW/m²."""

HEAT_TRANSFER = Ladder((Rung("W/(m**2*K)", "W/(m²·K)"),))
"""Heat-transfer coefficients in W/(m²·K)."""

POWER_DENSITY = Ladder((Rung("W/m**3", "W/m³"), Rung("kW/m**3", "kW/m³"), Rung("MW/m**3", "MW/m³"), Rung("GW/m**3", "GW/m³")))
"""Power densities in W/m³ up to GW/m³."""

CURRENT = Ladder((Rung("A", "A"), Rung("kA", "kA")))
"""Currents in A or kA."""

FLUX_DENSITY = Ladder((Rung("mT", "mT"), Rung("T", "T")))
"""Flux densities in mT or T."""

LADDERS: tuple[Ladder, ...] = (LENGTH, AREA, FORCE, PRESSURE, CURRENT_DENSITY, VOLTAGE, TEMPERATURE, HEAT_FLUX, HEAT_TRANSFER, POWER_DENSITY, CURRENT, FLUX_DENSITY)
"""Every ladder, scanned in order for the first that matches a quantity."""


def _rounded(value: float) -> float:
    """``value`` at three significant figures."""
    return float(f"{value:.{SIGNIFICANT_DIGITS}g}")


def _significant(value: float) -> str:
    """``value`` at three significant figures, without an exponent or trailing zeros."""
    rounded: float = _rounded(value)

    if rounded == 0.0:
        return "0"

    decimals: int = max(SIGNIFICANT_DIGITS - 1 - math.floor(math.log10(abs(rounded))), 0)
    text: str = f"{rounded:.{decimals}f}"

    if "." in text:
        text = text.rstrip("0").rstrip(".")

    return text


def readout(value: Quantity, *, unit: Unit | None = None) -> str:
    """``value`` at three significant figures in the largest rung of its ladder that reads at least 1, or in ``unit`` when given."""
    if unit is not None:
        rungs: list[Rung] = [rung for ladder in LADDERS for rung in ladder.rungs if U.Unit(rung.unit) == unit]
        label: str = rungs[0].label if rungs else f"{unit:~P}"

        return f"{_significant(value.m_as(unit))} {label}"

    ladder: Ladder | None = next((candidate for candidate in LADDERS if candidate.matches(value)), None)

    if ladder is None:
        compact: Quantity = value.to_compact()

        return f"{_significant(compact.magnitude)} {compact.units:~P}"

    for rung in reversed(ladder.rungs):
        magnitude: float = _rounded(value.m_as(rung.unit))

        if abs(magnitude) >= 1.0:
            return f"{_significant(magnitude)} {rung.label}"

    smallest: Rung = ladder.rungs[0]

    return f"{_significant(value.m_as(smallest.unit))} {smallest.label}"


def along(direction: Vec3) -> str:
    """The unit direction of ``direction`` as its three components at three significant figures, such as ``(0.6, 0.8, 0)``."""
    unit: Vec3 = direction.normalized()
    components: list[str] = ["0" if abs(component) <= ZERO_COMPONENT else _significant(component) for component in unit.to_tuple()]

    return f"({', '.join(components)})"


def vector_readout(value: VectorQuantity) -> str:
    """The magnitude of ``value`` and the direction it points along, such as ``100 N Along (1, 0, 0)``."""
    vector: Vec3 = value.magnitude
    size: Quantity = quantity(vector.magnitude(), str(value.units))

    if vector.magnitude() == 0.0:
        return readout(size)

    return f"{readout(size)} Along {along(vector)}"
