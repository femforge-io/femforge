from .config import (
    CoilCurrentSolver as CoilCurrentSolver,
    Constants as Constants,
    CoordinateSystem as CoordinateSystem,
    ElectrostaticsSolver as ElectrostaticsSolver,
    ExecSolver as ExecSolver,
    GenericSolver as GenericSolver,
    HeatSolver as HeatSolver,
    IterativeMethod as IterativeMethod,
    LinearSolverType as LinearSolverType,
    LinearSystem as LinearSystem,
    MagnetoDynamics2DSolver as MagnetoDynamics2DSolver,
    MagnetoDynamicsCalcFieldsSolver as MagnetoDynamicsCalcFieldsSolver,
    MagnetoDynamicsSolver as MagnetoDynamicsSolver,
    NonlinearSystem as NonlinearSystem,
    OperatingPoint as OperatingPoint,
    PhysicsConfig as PhysicsConfig,
    PhysicsSelection as PhysicsSelection,
    Preconditioning as Preconditioning,
    ResultOutputSolver as ResultOutputSolver,
    SaveScalarsSolver as SaveScalarsSolver,
    SifValue as SifValue,
    SimulationSettings as SimulationSettings,
    SimulationType as SimulationType,
    SolverBase as SolverBase,
    StatCurrentSolver as StatCurrentSolver,
    SteadyState as SteadyState,
    StressSolver as StressSolver,
    TimesteppingMethod as TimesteppingMethod,
    physics_config as physics_config,
    prepare_for_mpi as prepare_for_mpi,
)
from .mesh_names import MeshNames as MeshNames, parse_mesh_names as parse_mesh_names, reconcile_ids as reconcile_ids
from .physics import Physics as Physics, condition_physics as condition_physics, runtime_variables as runtime_variables
from .runtime.backend import ExecutionBackend as ExecutionBackend
from .runtime.docker import DockerElmer as DockerElmer
from .runtime.native import NativeElmer as NativeElmer
from .runtime.solve import solve_case as solve_case
from .variables import ElmerVariable as ElmerVariable, elemental as elemental
