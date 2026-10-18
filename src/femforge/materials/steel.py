"""Carbon and stainless steels."""

from femforge.materials.properties import MaterialProperties
from femforge.physical.property_functions import Calibration, CalibrationPoint, Static
from femforge.physical.units import DIM_FLUX_DENSITY, U


# Normal (anhysteretic) magnetization curve of annealed AISI 1080, as (H in A/m, B in T) pairs: mu_r peaks at ~584
# around B = 0.88 T, knees at ~1.5 T and approaches Js ~ 1.99 T, after which dB/dH decays to mu0.
_BH_CURVE: list[tuple[float, float]] = [
    (0.0, 0.0),
    (100.0, 0.025),
    (200.0, 0.055),
    (400.0, 0.150),
    (600.0, 0.300),
    (800.0, 0.500),
    (1_000.0, 0.700),
    (1_200.0, 0.880),
    (1_500.0, 1.080),
    (2_000.0, 1.280),
    (3_000.0, 1.480),
    (4_000.0, 1.580),
    (6_000.0, 1.680),
    (10_000.0, 1.760),
    (20_000.0, 1.850),
    (40_000.0, 1.920),
    (80_000.0, 1.990),
    (200_000.0, 2.199),
    (500_000.0, 2.601),
    (1_000_000.0, 3.242),
]


class Steel_1080(MaterialProperties):
    """1080 High-carbon steel."""

    def __init__(self, rel_permeability: float | None = None):
        """``rel_permeability`` None emits the nonlinear H-B curve; a value freezes a linear Static μr."""
        super().__init__()

        self.name: str = "High-Carbon Steel (AISI 1080, annealed)"
        self.tag: str = "Steel1080"

        self.mechanical.density = Static(value=7850 * U.kg / U.m**3)
        self.mechanical.elastic_modulus = Static(value=205e9 * U.Pa)
        self.mechanical.poisson_ratio = Static(value=0.29 * U.dimensionless)
        self.mechanical.yield_strength = Static(value=375e6 * U.Pa)
        self.mechanical.ultimate_tensile_strength = Static(value=615e6 * U.Pa)

        self.thermal.conductivity = Static(value=47.7 * U.W / (U.m * U.K))
        self.thermal.specific_heat_capacity = Static(value=470 * U.J / (U.kg * U.K))
        self.thermal.convective_heat_transfer_coefficient = Static(value=10 * U.W / (U.m**2 * U.K))

        if rel_permeability is None:
            self.magnetic.bh_curve = Calibration(
                param_dims={"flux_density": DIM_FLUX_DENSITY},
                points=[CalibrationPoint(inputs={"flux_density": b * U.T}, output=h * U.A / U.m) for h, b in _BH_CURVE],
                method="cubic",
            )
        else:
            self.magnetic.rel_permeability = Static(value=rel_permeability * U.dimensionless)
        # Soft-magnetic in this condition, so no remanence is authored: a remanence is what marks a material as a
        # permanent magnet (MaterialProperties.is_magnet) and would give the flux path a spurious magnetization.
        self.magnetic.coercivity = Static(value=400 * U.A / U.m)

        self.electrical.conductivity = Static(value=5.56e6 * U.S / U.m)
        self.electrical.resistivity = Static(value=1.8e-7 * U.ohm * U.m)
        self.electrical.permittivity = Static(value=8.854e-12 * U.F / U.m)


class Steel_316(MaterialProperties):
    """316 austenitic stainless steel, annealed."""

    def __init__(self):
        """Fill in the properties of annealed 316 stainless steel."""
        super().__init__()

        self.name: str = "Stainless Steel (AISI 316, annealed)"
        self.tag: str = "Steel316"

        self.mechanical.density = Static(value=8000 * U.kg / U.m**3)
        self.mechanical.elastic_modulus = Static(value=193e9 * U.Pa)
        self.mechanical.poisson_ratio = Static(value=0.27 * U.dimensionless)
        self.mechanical.yield_strength = Static(value=290e6 * U.Pa)
        self.mechanical.ultimate_tensile_strength = Static(value=580e6 * U.Pa)

        self.thermal.conductivity = Static(value=16.3 * U.W / (U.m * U.K))
        self.thermal.specific_heat_capacity = Static(value=500 * U.J / (U.kg * U.K))
        self.thermal.convective_heat_transfer_coefficient = Static(value=10 * U.W / (U.m**2 * U.K))

        # Annealed austenite is effectively non-magnetic (mu_r ~ 1.008); cold work raises it via strain-induced
        # martensite, which this model does not capture.
        self.magnetic.rel_permeability = Static(value=1.008 * U.dimensionless)

        self.electrical.conductivity = Static(value=1.35e6 * U.S / U.m)
        self.electrical.resistivity = Static(value=7.4e-7 * U.ohm * U.m)
        self.electrical.permittivity = Static(value=8.854e-12 * U.F / U.m)
