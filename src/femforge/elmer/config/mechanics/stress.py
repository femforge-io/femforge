"""Linear elasticity: Elmer's StressSolve."""

from __future__ import annotations

from typing import Annotated, Literal

from femforge.common.pydantic import Map
from femforge.elmer.config.common.solver_base import (
    EquationField,
    LinearMaxIterationsField,
    LinearSystem,
    NonlinearConvToleranceField,
    NonlinearMaxIterationsField,
    NonlinearSystem,
    OptimizeBandwidthField,
    ProcedureField,
    SolverBase,
    SteadyState,
    SteadyStateConvToleranceField,
    VariableDofsField,
    VariableField,
)
from femforge.elmer.variables import ElmerVariable


class StressSolver(SolverBase, LinearSystem, NonlinearSystem, SteadyState):
    """
    SCAFFOLD: linear elasticity (Elmer's StressSolve). Consumes ``Youngs Modulus`` and ``Poisson Ratio`` material
    keywords; the latter is not yet stored on MechanicalProperties.
    """

    kind: Literal["StressSolver"] = "StressSolver"
    """Discriminator that names this solver, doubling as its sif Solver label."""

    equation: EquationField = "Linear elasticity"
    procedure: ProcedureField = '"StressSolve" "StressSolver"'
    variable: VariableField = ElmerVariable.DISPLACEMENT
    variable_dofs: VariableDofsField = 3
    optimize_bandwidth: OptimizeBandwidthField = True

    steady_state_convergence_tolerance: SteadyStateConvToleranceField = 1.0e-6
    nonlinear_system_convergence_tolerance: NonlinearConvToleranceField = 1.0e-7
    nonlinear_system_max_iterations: NonlinearMaxIterationsField = 1
    linear_system_max_iterations: LinearMaxIterationsField = 5000

    calculate_stresses: Annotated[bool, Map("Calculate Stresses")] = True
    """Recover the stress tensor from the displacement."""
