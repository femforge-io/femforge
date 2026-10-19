"""Electrostatics: Elmer's StatElecSolver."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from femforge.common.pydantic import Map
from femforge.elmer.config.common.solver_base import (
    EquationField,
    LinearConvToleranceField,
    LinearMaxIterationsField,
    LinearSystem,
    ProcedureField,
    SolverBase,
    StabilizeField,
    SteadyState,
    SteadyStateConvToleranceField,
    VariableField,
)
from femforge.elmer.variables import ElmerVariable
from femforge.materials.properties import ElmerMaterialKeyword


class ElectrostaticsSolver(SolverBase, LinearSystem, SteadyState):
    """SCAFFOLD: electrostatics (StatElecSolver). No nonlinear iteration."""

    kind: Literal["Electrostatics"] = "Electrostatics"
    """Discriminator that names this solver, doubling as its sif Solver label."""

    display_variable: ClassVar[ElmerVariable | None] = ElmerVariable.POTENTIAL

    required_material_keys: ClassVar[frozenset[ElmerMaterialKeyword]] = frozenset({ElmerMaterialKeyword.RELATIVE_PERMITTIVITY})

    equation: EquationField = "Electrostatics"
    procedure: ProcedureField = '"StatElecSolve" "StatElecSolver"'
    variable: VariableField = ElmerVariable.POTENTIAL
    stabilize: StabilizeField = True

    steady_state_convergence_tolerance: SteadyStateConvToleranceField = 1.0e-5
    linear_system_max_iterations: LinearMaxIterationsField = 500
    linear_system_convergence_tolerance: LinearConvToleranceField = 1.0e-10

    calculate_electric_field: Annotated[bool, Map("Calculate Electric Field")] = True
    """Recover E from the potential."""
