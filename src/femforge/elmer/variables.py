"""Elmer solver variable names: the single source for each string an emit site declares and a parser reads back, with the unit each one is in."""

from enum import StrEnum

from femforge.elmer.physics import Physics


ELEMENTAL_SUFFIX = " e"
"""What Elmer appends to a solver's elemental (DG) field name."""


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

    MAGNETIC_FIELD_STRENGTH = "Magnetic Field Strength"
    """Fixed by Elmer: the CalcFields ``Calculate Magnetic Field Strength`` field."""

    NODAL_FORCE = "Nodal Force"
    """Fixed by Elmer: the CalcFields ``Calculate Nodal Forces`` field."""

    CURRENT_DENSITY = "Current Density"
    """Fixed by Elmer: the CalcFields ``Calculate Current Density`` field."""

    JFIX = "Jfix"
    """Fixed by Elmer: the fixed (divergence-free) source current density CalcFields writes."""

    VOLUME_CURRENT = "Volume Current"
    """Fixed by Elmer: the StatCurrentSolve ``Calculate Volume Current`` field."""

    ELECTRIC_FIELD = "Electric Field"
    """Fixed by Elmer: the ``Calculate Electric Field`` field of the electrostatic and CalcFields solvers."""

    STRESS = "Stress"
    """Fixed by Elmer: the StressSolve ``Calculate Stresses`` tensor, in Voigt order xx, yy, zz, xy, yz, xz."""

    STRAIN = "Strain"
    """Fixed by Elmer: the StressSolve ``Calculate Strains`` tensor, in the stress's Voigt order with tensorial shear."""

    VON_MISES = "VonMises"
    """Fixed by Elmer: the von Mises stress StressSolve writes beside the stress tensor."""

    PRINCIPAL_STRESS = "Principal Stress"
    """Fixed by Elmer: the StressSolve ``Calculate Principal`` principal stresses."""

    PRINCIPAL_STRAIN = "Principal Strain"
    """Fixed by Elmer: the StressSolve ``Calculate Principal`` principal strains."""

    STRAIN_ENERGY_DENSITY = "Strain Energy Density"
    """Derived by femforge from the stress and strain tensors; Elmer writes no such field."""

    @property
    def unit(self) -> str | None:
        """The SI unit the variable's values are in; None for the potential, whose unit the solve decides."""
        match self:
            case ElmerVariable.POTENTIAL:
                return None
            case ElmerVariable.TEMPERATURE:
                return "K"
            case ElmerVariable.DISPLACEMENT:
                return "m"
            case ElmerVariable.JOULE_HEATING:
                return "W/m³"
            case ElmerVariable.MAGNETIC_FLUX_DENSITY:
                return "T"
            case ElmerVariable.MAGNETIC_FIELD_ENERGY:
                return "J"
            case ElmerVariable.EDDY_CURRENT_POWER:
                return "W"
            case ElmerVariable.MAGNETIC_FIELD_STRENGTH:
                return "A/m"
            case ElmerVariable.NODAL_FORCE:
                return "N"
            case ElmerVariable.CURRENT_DENSITY | ElmerVariable.JFIX | ElmerVariable.VOLUME_CURRENT:
                return "A/m²"
            case ElmerVariable.ELECTRIC_FIELD:
                return "V/m"
            case ElmerVariable.STRESS | ElmerVariable.VON_MISES | ElmerVariable.PRINCIPAL_STRESS:
                return "Pa"
            case ElmerVariable.STRAIN | ElmerVariable.PRINCIPAL_STRAIN:
                return "-"
            case ElmerVariable.STRAIN_ENERGY_DENSITY:
                return "J/m³"


def elemental(variable: ElmerVariable | str) -> str:
    """The variable's DG counterpart: Elmer registers a solver's elemental field as ``"<name> e"``."""
    return f"{variable}{ELEMENTAL_SUFFIX}"


def variable_of(name: str) -> ElmerVariable | None:
    """The variable a VTU array name spells, nodal or elemental and in any case; None for a name no member spells."""
    bare: str = name.lower().removesuffix(ELEMENTAL_SUFFIX)

    return next((member for member in ElmerVariable if member.lower() == bare), None)


def variable_unit(name: str, physics: Physics | None = None) -> str | None:
    """The unit of a solver output array by its VTU name, nodal or elemental; the potential is a vector potential under the axisymmetric magnetostatic solve and a voltage under the rest.

    Args:
        name: The array name as the VTU spells it.
        physics: The physics the run solved, which decides what its potential is; None leaves the potential unitless.
    """
    variable: ElmerVariable | None = variable_of(name)
    if variable is None:
        return None

    if variable is not ElmerVariable.POTENTIAL:
        return variable.unit

    if physics is None:
        return None

    return "T·m" if physics is Physics.MAGNETOSTATICS_2D else "V"
