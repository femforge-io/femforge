"""Steady heat conduction: Elmer's HeatSolver."""

from __future__ import annotations

from typing import ClassVar, Literal

from femforge.elmer.config.common.solver_base import (
    EquationField,
    LinearDirectMethodField,
    LinearMaxIterationsField,
    LinearPreconditioningField,
    LinearSolverType,
    LinearSolverTypeField,
    LinearSystem,
    NonlinearConvToleranceField,
    NonlinearMaxIterationsField,
    NonlinearSystem,
    OptimizeBandwidthField,
    Preconditioning,
    ProcedureField,
    SolverBase,
    StabilizeField,
    SteadyState,
    SteadyStateConvToleranceField,
    VariableDofsField,
    VariableField,
)
from femforge.elmer.variables import ElmerVariable
from femforge.materials.properties import ElmerMaterialKeyword


class HeatSolver(SolverBase, LinearSystem, NonlinearSystem, SteadyState):
    """SCAFFOLD: steady heat conduction. Wire-ready; its boundary conditions land on the adjacency-derived surface groups via the condition-driven boundary loop."""

    kind: Literal["HeatSolver"] = "HeatSolver"
    """Discriminator that names this solver, doubling as its sif Solver label."""

    required_material_keys: ClassVar[frozenset[ElmerMaterialKeyword]] = frozenset({ElmerMaterialKeyword.HEAT_CONDUCTIVITY})

    equation: EquationField = "HeatSolver"
    procedure: ProcedureField = '"HeatSolve" "HeatSolver"'
    variable: VariableField = ElmerVariable.TEMPERATURE
    variable_dofs: VariableDofsField = 1
    stabilize: StabilizeField = True
    optimize_bandwidth: OptimizeBandwidthField = True

    # Direct/umfpack, not BiCGStab: thin-winding thermal meshes leave the iterative residual stalled ~1e-5 vs the
    # 1e-8 tolerance, flipping pass/fail on last-digit source perturbations; 2D heat systems are small, direct is exact.
    linear_system_solver: LinearSolverTypeField = LinearSolverType.DIRECT
    linear_system_direct_method: LinearDirectMethodField = "umfpack"

    steady_state_convergence_tolerance: SteadyStateConvToleranceField = 1.0e-6
    nonlinear_system_convergence_tolerance: NonlinearConvToleranceField = 1.0e-6
    nonlinear_system_max_iterations: NonlinearMaxIterationsField = 50
    linear_system_max_iterations: LinearMaxIterationsField = 1000
    linear_system_preconditioning: LinearPreconditioningField = Preconditioning.ILU
