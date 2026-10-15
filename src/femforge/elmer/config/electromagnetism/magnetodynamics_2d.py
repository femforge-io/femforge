"""Axisymmetric magnetostatics: Elmer's MagnetoDynamics2D."""

from __future__ import annotations

from typing import ClassVar, Literal

from femforge.elmer.config.common.solver_base import (
    EquationField,
    LinearDirectMethodField,
    LinearSolverType,
    LinearSolverTypeField,
    LinearSystem,
    NonlinearConvMeasureField,
    NonlinearConvToleranceField,
    NonlinearMaxIterationsField,
    NonlinearRelaxationFactorField,
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
from femforge.materials.properties import ElmerMaterialKeyword


class MagnetoDynamics2DSolver(SolverBase, LinearSystem, NonlinearSystem, SteadyState):
    """Axisymmetric magnetostatics (MagnetoDynamics2D): the scalar azimuthal potential A_phi under ``Coordinate System = Axi Symmetric``.

    The mesh lies flat in the XY plane (Z = 0), with X the radial distance from the symmetry axis (X >= 0) and Y the position along it.
    """

    kind: Literal["MagnetoDynamics2D"] = "MagnetoDynamics2D"
    """Discriminator that names this solver, doubling as its sif Solver label."""

    required_material_keys: ClassVar[frozenset[ElmerMaterialKeyword]] = frozenset({ElmerMaterialKeyword.RELATIVE_PERMEABILITY})

    equation: EquationField = "MgDyn2D"
    procedure: ProcedureField = '"MagnetoDynamics2D" "MagnetoDynamics2D"'
    variable: VariableField = ElmerVariable.POTENTIAL
    variable_dofs: VariableDofsField = 1
    optimize_bandwidth: OptimizeBandwidthField = True

    # Elmer's OWN default here is ONE nonlinear iteration: left unset, an H-B steel is evaluated once at its
    # initial slope and never updated — the cap must be authored. Newton engages automatically after iteration 1.
    nonlinear_system_max_iterations: NonlinearMaxIterationsField = 50
    nonlinear_system_convergence_tolerance: NonlinearConvToleranceField = 1.0e-7
    nonlinear_system_convergence_measure: NonlinearConvMeasureField = "solution"
    nonlinear_system_relaxation_factor: NonlinearRelaxationFactorField = 1.0

    linear_system_solver: LinearSolverTypeField = LinearSolverType.DIRECT
    linear_system_direct_method: LinearDirectMethodField = "umfpack"

    steady_state_convergence_tolerance: SteadyStateConvToleranceField = 1.0e-6
