"""Typed physics presets + OperatingPoint: the per-physics factory returns the expected solver ordering / settings / constants; OperatingPoint is the evaluation input. Pure-logic."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from femforge.elmer.physics import Physics
from femforge.elmer.variables import ElmerVariable
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
    from femforge.frames import Axisymmetric, Solid
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


def test_the_base_solver_fits_and_passes_any_frame_unchanged() -> None:
    from femforge.frames import Axisymmetric, Solid

    solver = HeatSolver()

    assert solver.fit_frame(Solid()) is solver and solver.fit_frame(Axisymmetric()) is solver
    solver.check_frame(Axisymmetric())


def test_solved_stack_fits_every_solver_to_the_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    from femforge.elmer.config import SolverBase, solved_stack
    from femforge.frames import Axisymmetric, Frame

    fitted: list[tuple[str, Frame]] = []

    def recording(self: SolverBase, frame: Frame) -> SolverBase:
        fitted.append((self.kind, frame))

        return self.model_copy()

    monkeypatch.setattr(SolverBase, "fit_frame", recording)
    stack = solved_stack(Physics.THERMAL, Axisymmetric())

    assert fitted == [(solver.kind, Axisymmetric()) for solver in stack.solvers]


def test_a_stack_in_another_coordinate_system_of_the_same_measure_still_raises() -> None:
    from femforge.elmer.config import CoordinateSystem
    from femforge.frames import Axisymmetric

    config = physics_config(Physics.THERMAL)
    config.simulation.coordinate_system = CoordinateSystem.CYLINDRIC_SYMMETRIC

    with pytest.raises(ValueError, match="Coordinate System 'Cylindric Symmetric'"):
        config.check_frame(Axisymmetric())


def test_the_stack_check_asks_every_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    from femforge.frames import Frame, Solid

    def refusing(self: HeatSolver, frame: Frame) -> None:
        raise ValueError(f"{self.kind} cannot solve {frame.label}")

    monkeypatch.setattr(HeatSolver, "check_frame", refusing)

    with pytest.raises(ValueError, match="cannot solve solid"):
        physics_config(Physics.THERMAL).check_frame(Solid())


def test_the_stress_solver_fits_the_frame_dimension_and_plane_state() -> None:
    from femforge.frames import Axisymmetric, Solid

    assert StressSolver().fit_frame(Axisymmetric()).variable_dofs == 2
    assert StressSolver(variable_dofs=2).fit_frame(Solid()).variable_dofs == 3
    assert StressSolver().fit_frame(Solid()).plane_stress is None


def test_the_stress_solver_rejects_a_dimension_the_frame_does_not_solve() -> None:
    from femforge.frames import Solid

    StressSolver(variable_dofs=None).check_frame(Solid())
    config = physics_config(Physics.LINEAR_ELASTICITY)
    config.solver(StressSolver).variable_dofs = 2

    with pytest.raises(ValueError, match="Variable Dofs = 2"):
        config.check_frame(Solid())


def test_the_plane_state_goes_to_the_equation_block_not_the_solver_block() -> None:
    solver = StressSolver(plane_stress=True)

    assert solver.equation_keywords() == {"Plane Stress": True}
    assert "Plane Stress" not in solver.to_elmer()
    assert StressSolver().equation_keywords() == {}


def test_solved_stacks_under_a_cartesian_2d_frame() -> None:
    from femforge.elmer.config import CoordinateSystem, MagnetoDynamics2DSolver, solved_stack
    from femforge.frames import Cartesian2D, PlaneState

    frame = Cartesian2D(thickness=10 * U.mm)
    thermal = solved_stack(Physics.THERMAL, frame)
    magnetic = solved_stack(Physics.MAGNETOSTATICS, frame)
    elastic = solved_stack(Physics.LINEAR_ELASTICITY, frame)
    strained = solved_stack(Physics.LINEAR_ELASTICITY, Cartesian2D(thickness=10 * U.mm, state=PlaneState.STRAIN))

    assert {stack.simulation.coordinate_system for stack in (thermal, magnetic, elastic)} == {CoordinateSystem.CARTESIAN_2D}
    assert isinstance(magnetic.solvers[0], MagnetoDynamics2DSolver)
    assert elastic.solver(StressSolver).variable_dofs == 2
    assert elastic.solver(StressSolver).plane_stress is True
    assert strained.solver(StressSolver).plane_stress is False


def test_a_cartesian_stack_cannot_solve_a_cartesian_2d_frame() -> None:
    from femforge.frames import Cartesian2D

    with pytest.raises(ValueError, match="Coordinate System 'Cartesian' cannot solve frame=Cartesian2D"):
        physics_config(Physics.THERMAL).check_frame(Cartesian2D(thickness=10 * U.mm))


_PRIMARIES: list[tuple[Physics, ElmerVariable]] = [
    (Physics.MAGNETOSTATICS, ElmerVariable.MAGNETIC_FLUX_DENSITY),
    (Physics.MAGNETOSTATICS_2D, ElmerVariable.MAGNETIC_FLUX_DENSITY),
    (Physics.COIL_MAGNETOSTATICS, ElmerVariable.MAGNETIC_FLUX_DENSITY),
    (Physics.THERMAL, ElmerVariable.TEMPERATURE),
    (Physics.JOULE_HEATING, ElmerVariable.TEMPERATURE),
    (Physics.ELECTROSTATICS, ElmerVariable.POTENTIAL),
    (Physics.CURRENT_CONDUCTION, ElmerVariable.POTENTIAL),
    (Physics.LINEAR_ELASTICITY, ElmerVariable.DISPLACEMENT),
]


@pytest.mark.parametrize(("physics", "primary"), _PRIMARIES)
def test_each_preset_names_its_primary_variable(physics: Physics, primary: ElmerVariable) -> None:
    assert physics_config(physics).primary_variable is primary


def test_the_primary_variable_cases_cover_every_physics() -> None:
    assert {physics for physics, _ in _PRIMARIES} == set(Physics)


def test_a_stack_without_a_display_variable_names_none() -> None:
    bare = physics_config(Physics.THERMAL).model_copy(update={"solvers": [ResultOutputSolver()]})

    assert bare.primary_variable is None
    assert "display_variable" not in StressSolver().model_dump()


def test_linear_targets_list_the_linear_solvers_in_stack_order() -> None:
    from femforge.elmer.config import LinearSolverType, LinearSystem, linear_targets
    from femforge.elmer.solver_log import LinearTarget

    config = physics_config(Physics.MAGNETOSTATICS)
    config.solver(MagnetoDynamicsCalcFieldsSolver).linear_system_solver = LinearSolverType.DIRECT

    targets = linear_targets(config)
    mgdyn = config.solver(MagnetoDynamicsSolver)

    assert [target.solver for target in targets] == [" ".join(s.equation.lower().split()) for s in config.solvers if isinstance(s, LinearSystem)]
    assert targets[0] == LinearTarget(
        solver="mgdyn",
        iterative=True,
        method="BiCGStabl",
        tolerance=mgdyn.linear_system_convergence_tolerance,
        max_iterations=mgdyn.linear_system_max_iterations,
    )
    assert (targets[1].iterative, targets[1].method) == (False, None)


def test_an_unset_linear_solver_counts_as_iterative() -> None:
    from femforge.elmer.config import linear_targets

    config = physics_config(Physics.LINEAR_ELASTICITY)
    stress = config.solver(StressSolver)
    stress.linear_system_solver = None
    stress.linear_system_iterative_method = None

    target = linear_targets(config)[0]
    assert (target.solver, target.iterative, target.method) == ("linear elasticity", True, None)


def test_linear_targets_follow_the_sif_solver_order_of_the_coil_stack(tmp_path: Path) -> None:
    from femforge.elmer.config import linear_targets
    from femforge.elmer.sim import Sim

    sim = Sim(physics=Physics.COIL_MAGNETOSTATICS)
    sim.generate().write(str(tmp_path))
    sif = (tmp_path / "case.sif").read_text(encoding="utf-8")

    equations: dict[str, int] = {}
    for block in re.finditer(r"^Solver (?P<number>\d+)\n(?P<body>.*?)^End", sif, re.MULTILINE | re.DOTALL):
        equation = re.search(r"^\s*Equation = (?P<name>.+)$", block.group("body"), re.MULTILINE)
        if equation is not None:
            equations[" ".join(equation.group("name").lower().split())] = int(block.group("number"))

    names = [target.solver for target in linear_targets(sim.config)]

    assert len(names) >= 3
    assert [equations[name] for name in names] == sorted(equations[name] for name in names)
