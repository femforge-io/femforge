"""The per-physics solver-stack presets: ``physics_config(physics)``."""

from __future__ import annotations

from femforge.common.frame import Axisymmetric, Frame
from femforge.elmer.config.common.constants import STEFAN_BOLTZMANN, Constants
from femforge.elmer.config.common.operating_point import OperatingPoint
from femforge.elmer.config.common.physics_config import PhysicsConfig
from femforge.elmer.config.common.simulation import CoordinateSystem, SimulationSettings
from femforge.elmer.config.electromagnetism.coil_current import CoilCurrentSolver
from femforge.elmer.config.electromagnetism.electrostatics import ElectrostaticsSolver
from femforge.elmer.config.electromagnetism.magnetodynamics import MagnetoDynamicsCalcFieldsSolver, MagnetoDynamicsSolver
from femforge.elmer.config.electromagnetism.magnetodynamics_2d import MagnetoDynamics2DSolver
from femforge.elmer.config.electromagnetism.stat_current import StatCurrentSolver
from femforge.elmer.config.mechanics.stress import StressSolver
from femforge.elmer.config.output.result_output import ResultOutputSolver
from femforge.elmer.config.output.save_scalars import SaveScalarsSolver, bare_save_scalars
from femforge.elmer.config.thermal.heat import HeatSolver
from femforge.elmer.physics import Physics
from femforge.elmer.variables import ElmerVariable, elemental
from femforge.materials.properties import PERMEABILITY_OF_VACUUM, PERMITTIVITY_OF_VACUUM


def solved_stack(physics: Physics, frame: Frame) -> PhysicsConfig:
    """The preset stack ``physics`` solves as under ``frame``; an axisymmetric form runs in the Axi Symmetric coordinate system."""
    config: PhysicsConfig = physics_config(physics.in_frame(frame))

    if isinstance(frame, Axisymmetric):
        config.simulation.coordinate_system = CoordinateSystem.AXI_SYMMETRIC

    return config


def physics_config(physics: Physics) -> PhysicsConfig:
    """The fully-populated, typed solver stack for ``physics``, keyed by an exhaustive ``match`` on the [`Physics`][femforge.elmer.physics.Physics] enum (a new physics is a static obligation)."""
    match physics:
        case Physics.MAGNETOSTATICS:
            return PhysicsConfig(
                solvers=[MagnetoDynamicsSolver(), MagnetoDynamicsCalcFieldsSolver(), bare_save_scalars(), ResultOutputSolver()],
                simulation=SimulationSettings(),
                constants=Constants(permeability_of_vacuum=PERMEABILITY_OF_VACUUM),
                operating_point=OperatingPoint(),
            )
        case Physics.MAGNETOSTATICS_2D:
            # What MAGNETOSTATICS solves as under frame=Axisymmetric(), on the 2D cross-section the frame maps the model
            # onto (X = radial >= 0, Y = along the axis). CalcFields pairs with the 2D solver (Potential Variable pins
            # the scalar A_phi); nodal fields stay ON — turning them off also suppresses the nodal force variable the
            # force reduction reads.
            return PhysicsConfig(
                solvers=[
                    MagnetoDynamics2DSolver(),
                    MagnetoDynamicsCalcFieldsSolver(
                        potential_variable=f'String "{ElmerVariable.POTENTIAL}"',
                        calculate_nodal_fields=True,
                    ),
                    bare_save_scalars(),
                    ResultOutputSolver(),
                ],
                simulation=SimulationSettings(coordinate_system=CoordinateSystem.AXI_SYMMETRIC),
                constants=Constants(permeability_of_vacuum=PERMEABILITY_OF_VACUUM),
                operating_point=OperatingPoint(),
            )
        case Physics.THERMAL:
            # The one SaveScalars reduction is the peak temperature — Result.temperature_max, the thermal-rating observable.
            return PhysicsConfig(
                solvers=[
                    HeatSolver(),
                    SaveScalarsSolver(variable_1=ElmerVariable.TEMPERATURE, operator_1="max", coefficient_1=None, variable_2=None, operator_2=None),
                    ResultOutputSolver(),
                ],
                simulation=SimulationSettings(),
                constants=Constants(stefan_boltzmann=STEFAN_BOLTZMANN),
                operating_point=OperatingPoint(),
            )
        case Physics.CURRENT_CONDUCTION:
            return PhysicsConfig(
                solvers=[StatCurrentSolver(), SaveScalarsSolver(), ResultOutputSolver()],
                simulation=SimulationSettings(),
                constants=Constants(),
                operating_point=OperatingPoint(),
            )
        case Physics.JOULE_HEATING:
            # Coupled StatCurrentSolve -> HeatSolve; one-way while sigma is Static, the iteration head-room is for a future two-way sigma(T).
            return PhysicsConfig(
                solvers=[
                    StatCurrentSolver(),
                    HeatSolver(),
                    SaveScalarsSolver(variable_3=ElmerVariable.TEMPERATURE, operator_3="max"),
                    ResultOutputSolver(),
                ],
                simulation=SimulationSettings(steady_state_max_iterations=10),
                constants=Constants(stefan_boltzmann=STEFAN_BOLTZMANN),
                operating_point=OperatingPoint(),
            )
        case Physics.COIL_MAGNETOSTATICS:
            # Chained CoilSolver -> WhitneyAV (one-way: J is independent of A). The AV source is CoilSolver's ELEMENTAL
            # coil current — a nodal Volume Current bridge silently dilutes the drive ~2x on thin conductors.
            return PhysicsConfig(
                solvers=[
                    # Before Simulation: the source must exist before the AV assembly reads it.
                    CoilCurrentSolver(),
                    # Jfix stays ON even with the elemental source: the DG coil current is not exactly solenoidal
                    # edge-element-wise, and without the projection the singular curl-curl solve diverges.
                    MagnetoDynamicsSolver(fix_input_current_density=True, use_elemental_coilcurrent=True),
                    # Potential Variable is pinned (typed String) so CalcFields latches onto the edge-element av; nodal forces
                    # off (meaningless without magnets); Separate Magnetic Energy prints "Magnetic Field Energy" to the log.
                    # H recovery off: μr ≡ 1 in these solves (H = B/μ0), and skipping it drops a 3-component CG
                    # recovery plus a quarter of the VTU on the big L meshes.
                    MagnetoDynamicsCalcFieldsSolver(
                        potential_variable='String "av"',
                        calculate_magnetic_field_strength=False,
                        calculate_magnetic_vector_potential=False,
                        calculate_relative_permeability=True,
                        calculate_nodal_forces=False,
                        separate_magnetic_energy=True,
                    ),
                    bare_save_scalars(),
                    # The pipeline reads only B back (energy cross-check, field sampling), so the VTU saves just that.
                    ResultOutputSolver(saved_vector_fields=[f'String "{elemental(ElmerVariable.MAGNETIC_FLUX_DENSITY)}"']),
                ],
                simulation=SimulationSettings(),
                constants=Constants(permeability_of_vacuum=PERMEABILITY_OF_VACUUM),
                operating_point=OperatingPoint(),
            )
        case Physics.ELECTROSTATICS:
            return PhysicsConfig(
                solvers=[ElectrostaticsSolver(), ResultOutputSolver()],
                simulation=SimulationSettings(),
                constants=Constants(permittivity_of_vacuum=PERMITTIVITY_OF_VACUUM),
                operating_point=OperatingPoint(),
            )
        # SCAFFOLD physics: the solver stack is complete, but the per-body wiring in Sim is a marked stub.
        case Physics.LINEAR_ELASTICITY:
            return PhysicsConfig(
                solvers=[StressSolver(), ResultOutputSolver()],
                simulation=SimulationSettings(),
                constants=Constants(),
                operating_point=OperatingPoint(),
            )
