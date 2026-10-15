"""Sintered NdFeB magnets with temperature-dependent remanence and coercivity."""

from femforge.materials.properties import MaterialProperties
from femforge.physical.expressions import Param
from femforge.physical.property_functions import Expression, Static
from femforge.physical.units import DIM_TEMPERATURE, U


_REMANENCE_TEMPERATURE_COEFFICIENT: float = -0.0012
"""`α_Br` `[1/K]`: Reversible temperature coefficient of remanence for a standard "N" grade sintered NdFeB."""

_COERCIVITY_TEMPERATURE_COEFFICIENT: float = -0.0060
"""`β_Hcb` `[1/K]`: Reversible temperature coefficient of coercivity for a standard "N" grade sintered NdFeB."""

_REFERENCE_TEMPERATURE: float = 293.15
"""`T_ref` `[K]`: The temperature the grade's remanence and coercivity are quoted at (20 °C)."""


class Neodymium(MaterialProperties):
    """Sintered NdFeB magnet; grades supply B_r and H_cb at the reference temperature, authored as Expressions so a hot magnet's weakened drive falls out of the operating point."""

    def __init__(self, *, name: str, tag: str, remanence_at_reference: float, coercivity_at_reference: float):
        """Fill in the NdFeB properties around a grade's remanence and coercivity.

        Args:
            name: The display name.
            tag: The registry key that a body's ``material`` tag names.
            remanence_at_reference: The remanence B_r at the reference temperature, in T.
            coercivity_at_reference: The coercivity H_cb at the reference temperature, in A/m.
        """
        super().__init__()

        self.name: str = name
        self.tag: str = tag

        self.mechanical.density = Static(value=7500 * U.kg / U.m**3)
        self.mechanical.elastic_modulus = Static(value=160e9 * U.Pa)
        # Sintered NdFeB is brittle: it fractures at ~80 MPa in tension with no yield plateau, so yield_strength is left unset.
        self.mechanical.ultimate_tensile_strength = Static(value=80e6 * U.Pa)

        self.thermal.conductivity = Static(value=8.7 * U.W / (U.m * U.K))
        self.thermal.specific_heat_capacity = Static(value=440 * U.J / (U.kg * U.K))
        self.thermal.convective_heat_transfer_coefficient = Static(value=10 * U.W / (U.m**2 * U.K))

        self.magnetic.rel_permeability = Static(value=1.05 * U.dimensionless)
        self.magnetic.remanence = Expression(
            param_dims={"temperature": DIM_TEMPERATURE},
            result_units="T",
            body=remanence_at_reference * (1.0 + _REMANENCE_TEMPERATURE_COEFFICIENT * (Param(name="temperature") - _REFERENCE_TEMPERATURE)),
        )
        self.magnetic.coercivity = Expression(
            param_dims={"temperature": DIM_TEMPERATURE},
            result_units="A/m",
            body=coercivity_at_reference * (1.0 + _COERCIVITY_TEMPERATURE_COEFFICIENT * (Param(name="temperature") - _REFERENCE_TEMPERATURE)),
        )

        self.electrical.conductivity = Static(value=7.14e5 * U.S / U.m)
        self.electrical.resistivity = Static(value=1.4e-6 * U.ohm * U.m)
        self.electrical.permittivity = Static(value=8.854e-12 * U.F / U.m)
        self.electrical.permeability = Static(value=1.3196e-6 * U.H / U.m)


class N52(Neodymium):
    """N52 neodymium magnet."""

    def __init__(self):
        """Fill in the N52 grade."""
        super().__init__(name="Neodymium Magnet (N52)", tag="N52", remanence_at_reference=1.48, coercivity_at_reference=955e3)


class N42(Neodymium):
    """N42 neodymium magnet."""

    def __init__(self):
        """Fill in the N42 grade."""
        super().__init__(name="Neodymium Magnet (N42)", tag="N42", remanence_at_reference=1.30, coercivity_at_reference=915e3)
