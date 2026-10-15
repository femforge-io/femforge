"""Dry air as a material."""

from femforge.materials.properties import MaterialProperties
from femforge.physical.property_functions import Static
from femforge.physical.units import U


class Air(MaterialProperties):
    """Dry air at room temperature."""

    def __init__(self):
        """Fill in the properties of dry air at room temperature."""
        super().__init__()
        self.name: str = "Air"
        self.tag: str = "Air"
        self.mechanical.density = Static(value=1.225 * U.kg / U.m**3)

        self.thermal.conductivity = Static(value=0.0257 * U.W / (U.m * U.K))
        self.thermal.specific_heat_capacity = Static(value=1005 * U.J / (U.kg * U.K))
        self.thermal.convective_heat_transfer_coefficient = Static(value=10 * U.W / (U.m**2 * U.K))

        self.magnetic.rel_permeability = Static(value=1.0 * U.dimensionless)

        self.electrical.conductivity = Static(value=1e-14 * U.S / U.m)
        self.electrical.resistivity = Static(value=1e14 * U.ohm * U.m)
        self.electrical.permittivity = Static(value=8.854e-12 * U.F / U.m)
        self.electrical.permeability = Static(value=1.2566370614e-6 * U.H / U.m)
