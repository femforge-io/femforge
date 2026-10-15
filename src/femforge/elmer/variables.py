"""Elmer solver variable names: the single source for each string an emit site declares and a parser reads back."""

from enum import StrEnum


class ElmerVariable(StrEnum):
    """The Elmer variable names this project emits and reads back, one member per name.

    VTU and SaveScalars names come back lowercased, so match them with ``member.lower()``.
    """

    POTENTIAL = "Potential"
    """The conduction/electrostatic scalar potential (also the Dirichlet keyword of [`FixedPotential`][femforge.physical.conditions.FixedPotential]).
    NOT renameable: coupled HeatSolve resolves the electric solution by this exact name for ``Joule Heat = True`` — any other name silently zeroes the heat source."""

    TEMPERATURE = "Temperature"
    """The heat-solve variable. Keep Elmer's spelling: runtime material properties emit ``Variable Temperature``."""

    DISPLACEMENT = "Displacement"
    """The elasticity primary variable."""

    JOULE_HEATING = "Joule Heating"
    """Fixed by Elmer: created internally by StatCurrentSolve's ``Calculate Joule Heating``."""

    MAGNETIC_FLUX_DENSITY = "Magnetic Flux Density"
    """Fixed by Elmer: created internally by MagnetoDynamicsCalcFields."""

    MAGNETIC_FIELD_ENERGY = "Magnetic Field Energy"
    """Fixed by Elmer: the CalcFields ``Separate Magnetic Energy`` observable's log/scalar name."""

    EDDY_CURRENT_POWER = "Eddy Current Power"
    """Fixed by Elmer: the CalcFields eddy-loss ``res:`` scalar (harmonic/transient solves; absent in statics)."""


def elemental(variable: ElmerVariable | str) -> str:
    """The variable's DG counterpart: Elmer registers a solver's elemental field as ``"<name> e"``."""
    return f"{variable} e"
