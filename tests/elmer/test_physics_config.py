"""Typed physics presets + OperatingPoint: the per-physics factory returns the expected solver ordering / settings / constants; OperatingPoint is the evaluation input. Pure-logic."""

from __future__ import annotations

import pytest

from femforge.elmer.physics import Physics
from femforge.elmer.config import (
    Constants,
    ElectrostaticsSolver,
    HeatSolver,
    MagnetoDynamicsCalcFieldsSolver,
    MagnetoDynamicsSolver,
    OperatingPoint,
    ResultOutputSolver,
    SaveScalarsSolver,
    SimulationSettings,
    StressSolver,
    physics_config,
)
from femforge.physical.units import U


def test_magnetostatics_stack_ordering_and_settings() -> None:
    config = physics_config(Physics.MAGNETOSTATICS)
    assert [type(s) for s in config.solvers] == [MagnetoDynamicsSolver, MagnetoDynamicsCalcFieldsSolver, SaveScalarsSolver, ResultOutputSolver]
    assert config.simulation.to_elmer() == SimulationSettings().to_elmer()
    assert config.constants.to_elmer() == {"Permeability of Vacuum": 1.25663706212e-6}


def test_thermal_stack_ordering_and_settings() -> None:
    config = physics_config(Physics.THERMAL)
    assert [type(s) for s in config.solvers] == [HeatSolver, SaveScalarsSolver, ResultOutputSolver]
    assert config.constants.to_elmer() == {"Stefan Boltzmann": 5.6704e-8}
    # The one reduction is the rating observable: max Temperature -> Result.temperature_max.
    scalars = config.solver(SaveScalarsSolver).to_elmer()
    assert scalars["Variable 1"] == "Temperature"
    assert scalars["Operator 1"] == "max"
    assert "Variable 2" not in scalars and "Coefficient 1" not in scalars


def test_electrostatics_and_elasticity_stacks() -> None:
    electro = physics_config(Physics.ELECTROSTATICS)
    assert [type(s) for s in electro.solvers] == [ElectrostaticsSolver, ResultOutputSolver]
    assert electro.constants.to_elmer() == {"Permittivity of Vacuum": 8.8541878128e-12}

    elasticity = physics_config(Physics.LINEAR_ELASTICITY)
    assert [type(s) for s in elasticity.solvers] == [StressSolver, ResultOutputSolver]
    assert elasticity.constants.to_elmer() == {}  # no constants activated


def test_each_config_carries_a_default_operating_point() -> None:
    for physics in Physics:
        op = physics_config(physics).operating_point
        assert isinstance(op, OperatingPoint)
        assert op.temperature == 300 * U.K


def test_operating_point_as_mapping_feeds_property_functions() -> None:
    # The ephemeral mapping is what material to_elmer(at=...) / property functions consume (prop(**at)).
    assert OperatingPoint(temperature=350 * U.K).as_mapping() == {"temperature": 350 * U.K}


def test_stack_is_mutable_before_generate() -> None:
    # A caller can tweak any solver field before generating; the returned instances are live and typed.
    config = physics_config(Physics.MAGNETOSTATICS)
    solver = config.solvers[0]
    assert isinstance(solver, MagnetoDynamicsSolver)
    solver.linear_system_max_iterations = 9000
    assert config.solvers[0].to_elmer()["Linear System Max Iterations"] == 9000


def test_solver_accessor_returns_typed_instance_or_raises() -> None:
    # config.solver(kind) returns a typed solver instance by class.
    config = physics_config(Physics.MAGNETOSTATICS)
    mag = config.solver(MagnetoDynamicsSolver)
    assert isinstance(mag, MagnetoDynamicsSolver)
    # The returned instance is live: editing it shows up via a second lookup.
    mag.linear_system_max_iterations = 7000
    assert config.solver(MagnetoDynamicsSolver).linear_system_max_iterations == 7000
    # A solver not in this physics' stack raises KeyError (HeatSolver is thermal-only).
    with pytest.raises(KeyError):
        _ = config.solver(HeatSolver)


def test_magnetic_presets_carry_a_bare_save_scalars() -> None:
    # Every magnetic preset exports the res: simulation/component scalars (Component force/torque, field energy)
    # without any configured reductions — the conduction defaults (diffusive flux etc.) must all be cleared.
    for physics in (Physics.MAGNETOSTATICS, Physics.MAGNETOSTATICS_2D, Physics.COIL_MAGNETOSTATICS):
        scalars = physics_config(physics).solver(SaveScalarsSolver)
        emitted = scalars.to_elmer()
        assert not any(key.startswith(("Variable", "Operator", "Coefficient")) for key in emitted), physics
        assert emitted["Filename"] == '"scalars.dat"'


def test_convergence_targets_keyed_by_normalized_equation() -> None:
    from femforge.elmer.config import convergence_targets

    config = physics_config(Physics.MAGNETOSTATICS)
    targets = convergence_targets(config)

    mgdyn = config.solver(MagnetoDynamicsSolver)
    assert targets[("mgdyn", "NS")].max_iterations == mgdyn.nonlinear_system_max_iterations
    assert targets[("mgdyn", "NS")].tolerance == mgdyn.nonlinear_system_convergence_tolerance
    # SS caps come from the Simulation block, tolerances from the solver.
    assert targets[("mgdyn", "SS")].max_iterations == config.simulation.steady_state_max_iterations
    assert targets[("mgdyn", "SS")].tolerance == mgdyn.steady_state_convergence_tolerance


def test_convergence_targets_multiword_equation_and_none_fields() -> None:
    from femforge.elmer.config import StatCurrentSolver, convergence_targets

    config = physics_config(Physics.JOULE_HEATING)
    targets = convergence_targets(config)

    stat = config.solver(StatCurrentSolver)
    key = " ".join(stat.equation.lower().split())
    assert " " not in key or key == key.lower()
    assert targets[(key, "SS")].max_iterations == 10  # the coupled preset raises Steady State Max Iterations

    # A solver without the NonlinearSystem mixin contributes no NS entry.
    result_output_keys = [k for k, _ in targets if k == "resultoutputsolver"]
    assert not result_output_keys
    # The activated constant takes the canonical value.
    assert Constants(permeability_of_vacuum=1.0).to_elmer() == {"Permeability of Vacuum": 1.0}
    mag = physics_config(Physics.MAGNETOSTATICS).constants
    assert mag.permeability_of_vacuum == pytest.approx(1.25663706212e-6)


def test_solved_stack_applies_the_frame() -> None:
    from femforge.common.frame import Axisymmetric, Solid
    from femforge.elmer.config import CoordinateSystem, MagnetoDynamics2DSolver, solved_stack

    assert solved_stack(Physics.MAGNETOSTATICS, Solid()).model_dump() == physics_config(Physics.MAGNETOSTATICS).model_dump()
    assert isinstance(solved_stack(Physics.MAGNETOSTATICS, Axisymmetric()).solvers[0], MagnetoDynamics2DSolver)

    thermal = solved_stack(Physics.THERMAL, Axisymmetric())
    assert thermal.simulation.coordinate_system is CoordinateSystem.AXI_SYMMETRIC
    assert [type(s) for s in thermal.solvers] == [type(s) for s in physics_config(Physics.THERMAL).solvers]


def test_physics_config_round_trips_solver_subclasses() -> None:
    from femforge.elmer.config import PhysicsConfig

    config = physics_config(Physics.COIL_MAGNETOSTATICS)
    restored = PhysicsConfig.model_validate_json(config.model_dump_json())
    assert [type(s) for s in restored.solvers] == [type(s) for s in config.solvers]
    assert restored.solver(MagnetoDynamicsSolver).use_elemental_coilcurrent is True


def test_magnetostatics_2d_stack_is_axisymmetric() -> None:
    from femforge.elmer.config import CoordinateSystem, MagnetoDynamics2DSolver

    config = physics_config(Physics.MAGNETOSTATICS_2D)
    assert [type(s) for s in config.solvers] == [MagnetoDynamics2DSolver, MagnetoDynamicsCalcFieldsSolver, SaveScalarsSolver, ResultOutputSolver]
    assert config.simulation.coordinate_system is CoordinateSystem.AXI_SYMMETRIC
    solver = config.solver(MagnetoDynamics2DSolver)
    # Elmer's own default is a SINGLE nonlinear iteration, which would silently freeze an H-B steel at its initial slope.
    assert solver.to_elmer()["Nonlinear System Max Iterations"] == 50
    assert solver.to_elmer()["Linear System Direct Method"] == "umfpack"
    assert solver.to_elmer()["Variable"] == "Potential"
