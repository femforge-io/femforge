"""FR4, the glass-epoxy laminate of printed circuit boards."""

from femforge.materials.properties import MaterialProperties
from femforge.physical.property_functions import Static
from femforge.physical.units import U


class FR4(MaterialProperties):
    """FR4 glass-epoxy laminate, the substrate of printed circuit boards."""

    def __init__(self):
        """Fill in the properties of FR4."""
        super().__init__()
        self.name: str = "FR4"
        self.tag: str = "FR4"

        self.mechanical.density = Static(value=1900 * U.kg / U.m**3)
        self.mechanical.elastic_modulus = Static(value=20e9 * U.Pa)
        self.mechanical.poisson_ratio = Static(value=0.13 * U.dimensionless)
        self.mechanical.yield_strength = Static(value=60e6 * U.Pa)
        self.mechanical.ultimate_tensile_strength = Static(value=100e6 * U.Pa)

        self.thermal.conductivity = Static(value=0.3 * U.W / (U.m * U.K))
        self.thermal.specific_heat_capacity = Static(value=1200 * U.J / (U.kg * U.K))
        self.thermal.convective_heat_transfer_coefficient = Static(value=10 * U.W / (U.m**2 * U.K))
        self.thermal.glass_transition_temperature = Static(value=(130 * U.degC).to(U.K))

        self.magnetic.rel_permeability = Static(value=1.0 * U.dimensionless)

        self.electrical.conductivity = Static(value=1e-10 * U.S / U.m)
        self.electrical.resistivity = Static(value=1e10 * U.ohm * U.m)
        self.electrical.permittivity = Static(value=4.5 * 8.854e-12 * U.F / U.m)
        self.electrical.permeability = Static(value=1e-6 * U.H / U.m)
