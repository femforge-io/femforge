"""Annealed copper with a temperature-dependent conductivity."""

from femforge.materials.properties import MaterialProperties
from femforge.physical.expressions import Param
from femforge.physical.property_functions import Expression, Static
from femforge.physical.units import DIM_TEMPERATURE, U


CONDUCTIVITY_AT_REFERENCE: float = 5.96e7
"""`σ_ref` `[S/m]`: Electrical conductivity of annealed copper at the reference temperature."""

RESISTIVITY_TEMPERATURE_COEFFICIENT: float = 3.93e-3
"""
`α` `[1/K]`: Linear temperature coefficient of resistivity, σ(T) = σ_ref/(1 + α·(T − T_ref)).

The law the coupled Joule solve emits, so every analytic R(T) must use the same one.
"""

REFERENCE_TEMPERATURE: float = 293.15
"""`T_ref` `[K]`: The temperature the copper conductivity is quoted at (20 °C)."""


class Copper(MaterialProperties):
    """Annealed copper whose resistivity rises linearly with temperature."""

    def __init__(self):
        """Fill in the properties of annealed copper."""
        super().__init__()
        self.name: str = "Copper"
        self.tag: str = "Copper"
        self.mechanical.density = Static(value=8960 * U.kg / U.m**3)

        self.thermal.conductivity = Static(value=401 * U.W / (U.m * U.K))
        self.thermal.specific_heat_capacity = Static(value=385 * U.J / (U.kg * U.K))
        # Natural convection placeholder, matching the other materials; the surface film coefficient is a BC property, not a bulk one.
        self.thermal.convective_heat_transfer_coefficient = Static(value=10 * U.W / (U.m**2 * U.K))

        self.magnetic.rel_permeability = Static(value=1.0 * U.dimensionless)

        self.electrical.conductivity = Expression(
            param_dims={"temperature": DIM_TEMPERATURE},
            result_units="S/m",
            body=CONDUCTIVITY_AT_REFERENCE / (1.0 + RESISTIVITY_TEMPERATURE_COEFFICIENT * (Param(name="temperature") - REFERENCE_TEMPERATURE)),
        )
        self.electrical.resistivity = Static(value=1.68e-8 * U.ohm * U.m)
        self.electrical.permittivity = Static(value=8.854e-12 * U.F / U.m)
        self.electrical.permeability = Static(value=1.2566370614e-6 * U.H / U.m)
