"""Static current conduction: Elmer's StatCurrentSolve."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from femforge.common.pydantic import Map
from femforge.elmer.config.common.solver_base import (
    EquationField,
    IterativeMethod,
    LinearConvToleranceField,
    LinearDirectMethodField,
    LinearIterativeMethodField,
    LinearMaxIterationsField,
    LinearSolverType,
    LinearSolverTypeField,
    LinearSystem,
    NonlinearConvToleranceField,
    NonlinearMaxIterationsField,
    NonlinearSystem,
    OptimizeBandwidthField,
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


class StatCurrentSolver(SolverBase, LinearSystem, NonlinearSystem, SteadyState):
    """
    Static current conduction (Elmer's StatCurrentSolve): solves ``Potential`` from ∇·(σ∇φ) = 0, excited by per-phase
    [`FixedPotential`][femforge.physical.conditions.FixedPotential] terminal pairs. The nonlinear block is inert while σ is Static.
    """

    kind: Literal["StatCurrent"] = "StatCurrent"
    """Discriminator that names this solver, doubling as its sif Solver label."""

    required_material_keys: ClassVar[frozenset[ElmerMaterialKeyword]] = frozenset({ElmerMaterialKeyword.ELECTRIC_CONDUCTIVITY})
    # Copper only: with the dielectrics in the same equation CG+ILU0 stalls and a direct factorization is outright singular (umf4num -1).
    conductor_only: ClassVar[bool] = True

    equation: EquationField = "Stat Current Solver"
    procedure: ProcedureField = '"StatCurrentSolve" "StatCurrentSolver"'
    variable: VariableField = ElmerVariable.POTENTIAL
    variable_dofs: VariableDofsField = 1
    stabilize: StabilizeField = True
    optimize_bandwidth: OptimizeBandwidthField = True

    steady_state_convergence_tolerance: SteadyStateConvToleranceField = 1.0e-6
    nonlinear_system_convergence_tolerance: NonlinearConvToleranceField = 1.0e-8
    nonlinear_system_max_iterations: NonlinearMaxIterationsField = 20
    # Direct/umfpack, not CG: the long thin winding's low-frequency Potential modes stall preconditioned CG even copper-only.
    # The iterative knobs below stay populated as the fallback for scales where a factorization no longer fits in memory.
    linear_system_solver: LinearSolverTypeField = LinearSolverType.DIRECT
    linear_system_direct_method: LinearDirectMethodField = "umfpack"
    linear_system_iterative_method: LinearIterativeMethodField = IterativeMethod.CG
    # 5000, not 1000: the thin-trace geometry stalls CG+ILU0 near 1e-8 for ~1k iterations before recovering; iterations are cheap, a spurious abort is not.
    linear_system_max_iterations: LinearMaxIterationsField = 5000
    # 1e-6 suffices (fallback-only, inert under Direct): R is the Joule volume integral, quadratic in φ, so a 1e-6 residual gives R to ~ppm.
    linear_system_convergence_tolerance: LinearConvToleranceField = 1.0e-6

    calculate_volume_current: Annotated[bool, Map("Calculate Volume Current")] = True
    """Recover the volume current density J = -σ∇φ."""

    calculate_joule_heating: Annotated[bool, Map("Calculate Joule Heating")] = True
    """Recover the Joule loss density σ|∇φ|² (the heat-solve coupling source)."""
