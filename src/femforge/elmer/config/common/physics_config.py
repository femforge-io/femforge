"""The fully populated solver stack for one physics (``PhysicsConfig``) and its stack-wide adjustments and readbacks."""
# Must not import elmer.sim: elmer.sim depends on this module.

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from femforge.elmer.config.common.constants import Constants
from femforge.elmer.config.common.generic import GenericSolver
from femforge.elmer.config.common.operating_point import OperatingPoint
from femforge.elmer.config.common.simulation import CoordinateSystem, SimulationSettings
from femforge.elmer.config.common.solver_base import LinearSolverType, LinearSystem, NonlinearSystem, SolverT, SteadyState
from femforge.elmer.config.electromagnetism.coil_current import CoilCurrentSolver
from femforge.elmer.config.electromagnetism.electrostatics import ElectrostaticsSolver
from femforge.elmer.config.electromagnetism.magnetodynamics import MagnetoDynamicsCalcFieldsSolver, MagnetoDynamicsSolver
from femforge.elmer.config.electromagnetism.magnetodynamics_2d import MagnetoDynamics2DSolver
from femforge.elmer.config.electromagnetism.stat_current import StatCurrentSolver
from femforge.elmer.config.mechanics.stress import StressSolver
from femforge.elmer.config.output.result_output import ResultOutputSolver
from femforge.elmer.config.output.save_scalars import SaveScalarsSolver
from femforge.elmer.config.thermal.heat import HeatSolver
from femforge.elmer.solver_log import LoopTargets, normalize_solver_key
from femforge.elmer.variables import ElmerVariable


if TYPE_CHECKING:
    from femforge.frames import Frame


# The discriminated union PhysicsConfig stores: full subclass fields survive model_dump/validate round-trips
# (a plain list[SolverBase] would strip every concrete field on serialization), which is what lets the solver
# stack ride inside SimulationConfig / cache_key / RunManifest.
SolverUnion = Annotated[
    MagnetoDynamicsSolver
    | MagnetoDynamics2DSolver
    | MagnetoDynamicsCalcFieldsSolver
    | ResultOutputSolver
    | HeatSolver
    | StatCurrentSolver
    | CoilCurrentSolver
    | SaveScalarsSolver
    | ElectrostaticsSolver
    | StressSolver
    | GenericSolver,
    Field(discriminator="kind"),
]


class PhysicsConfig(BaseModel):
    """The fully populated, typed solver stack for one physics: ordered solvers plus simulation settings, activated constants and operating point."""

    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True, use_attribute_docstrings=True)

    solvers: list[SolverUnion]
    """The ordered concrete solver instances Elmer runs for this physics."""

    simulation: SimulationSettings
    """The Elmer Simulation block (run type, coordinate system, output files)."""

    constants: Constants
    """The physical constants this physics activates."""

    operating_point: OperatingPoint
    """Where material property functions are evaluated when stripping to SI."""

    def solver(self, kind: type[SolverT]) -> SolverT:
        """The live solver instance of type ``kind`` in this stack; raises ``KeyError`` when it has none."""
        for spec in self.solvers:
            if isinstance(spec, kind):
                return spec
        raise KeyError(f"No {kind.__name__} in this physics' solver stack ({[type(s).__name__ for s in self.solvers]}).")

    @property
    def primary_variable(self) -> ElmerVariable | None:
        """The field a viewer opens the run on: the display variable of the stack's last solver that names one."""
        declared: list[ElmerVariable] = [solver.display_variable for solver in self.solvers if solver.display_variable is not None]

        return declared[-1] if declared else None

    def check_frame(self, frame: Frame) -> None:
        """Raise when this stack's coordinate system is not ``frame``'s or one of its solvers cannot solve under ``frame``."""
        coordinate_system: CoordinateSystem = self.simulation.coordinate_system

        if coordinate_system is not frame.coordinate_system:
            raise ValueError(f"The solver stack's Coordinate System '{coordinate_system.value}' cannot solve frame={type(frame).__name__}(); re-customize the physics after setting the frame.")

        for solver in self.solvers:
            solver.check_frame(frame)


def prepare_for_mpi(config: PhysicsConfig) -> None:
    """Adjust a solver stack for a partitioned (MPI) solve: CoilSolver's serial-only umfpack Direct solve switches to MUMPS, and any other Direct solve raises."""
    for solver in config.solvers:
        if isinstance(solver, CoilCurrentSolver):
            solver.linear_system_direct_method = "MUMPS"
        elif isinstance(solver, LinearSystem) and solver.linear_system_solver is LinearSolverType.DIRECT:
            raise ValueError(f"{type(solver).__name__} uses a Direct linear solve, which is unvalidated under partitioning — run it serially (mpi_processes=1).")


def needs_halobc(config: PhysicsConfig) -> bool:
    """Whether partitioning must add halo elements (``-halobc``): only the coil stack's boundary-terminated electrodes need them, and elsewhere they skew CalcFields' force lumping."""
    return any(isinstance(solver, CoilCurrentSolver) for solver in config.solvers)


def convergence_targets(config: PhysicsConfig) -> dict[tuple[str, Literal["NS", "SS"]], LoopTargets]:
    """The per-(solver, loop) iteration caps and tolerances the stack authors, keyed the way Elmer tags ComputeChange lines."""
    targets: dict[tuple[str, Literal["NS", "SS"]], LoopTargets] = {}

    for solver in config.solvers:
        key: str = normalize_solver_key(solver.equation)

        if isinstance(solver, NonlinearSystem):
            targets[(key, "NS")] = LoopTargets(
                max_iterations=solver.nonlinear_system_max_iterations,
                tolerance=solver.nonlinear_system_convergence_tolerance,
            )

        if isinstance(solver, SteadyState):
            targets[(key, "SS")] = LoopTargets(
                max_iterations=config.simulation.steady_state_max_iterations,
                tolerance=solver.steady_state_convergence_tolerance,
            )

    return targets
