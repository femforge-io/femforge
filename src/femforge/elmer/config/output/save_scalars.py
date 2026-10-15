"""Scalar reductions to ``scalars.dat``: Elmer's SaveScalars."""

from __future__ import annotations

from typing import Annotated, Literal

from femforge.common.pydantic import Map
from femforge.elmer.config.common.solver_base import EquationField, ExecSolver, ExecSolverField, ProcedureField, SolverBase
from femforge.elmer.variables import ElmerVariable


class SaveScalarsSolver(SolverBase):
    """SaveScalars (SaveData): export scalar reductions to ``scalars.dat`` and its ``.names`` sidecar.

    Its ``diffusive flux`` operator integrates each ``Save Scalars = True`` electrode's terminal current, the ``I`` in ``R = ΔV / I``.
    """

    kind: Literal["SaveScalars"] = "SaveScalars"
    """Discriminator that names this solver, doubling as its sif Solver label."""

    equation: EquationField = "SaveScalars"
    procedure: ProcedureField = '"SaveData" "SaveScalars"'
    exec_solver: ExecSolverField = ExecSolver.AFTER_TIMESTEP

    save_component_results: Annotated[bool | None, Map("Save Component Results")] = None
    """Also export the scalars arising from Component sections."""

    parallel_reduce: Annotated[bool | None, Map("Parallel Reduce")] = None
    """Reduce a partitioned run's output to one ``scalars.dat`` instead of per-rank ``.dat.<rank>`` files."""

    filename: Annotated[str, Map("Filename")] = '"scalars.dat"'
    """The .dat the scalars land in (the run parser reads it, or its per-rank ``.dat.<rank>`` siblings, via the ``.names`` sidecar)."""

    variable_1: Annotated[str | None, Map("Variable 1")] = ElmerVariable.POTENTIAL
    """The variable the operator reduces."""

    operator_1: Annotated[str | None, Map("Operator 1")] = "diffusive flux"
    """The reduction: boundary integral of the conjugate flux of ``variable_1``."""

    coefficient_1: Annotated[str | None, Map("Coefficient 1")] = "Electric Conductivity"
    """The material coefficient weighting the flux (σ ∂φ/∂n integrates to a current)."""

    variable_2: Annotated[str | None, Map("Variable 2")] = ElmerVariable.JOULE_HEATING
    """Second reduction: the Joule loss field (requires ``Calculate Joule Heating``)."""

    operator_2: Annotated[str | None, Map("Operator 2")] = "int"
    """Volume integral: total dissipated power P (W), the flux-independent cross-check R = ΔV²/P."""

    variable_3: Annotated[str | None, Map("Variable 3")] = None
    """Third reduction; the coupled joule-heating stack sets ``Temperature`` here."""

    operator_3: Annotated[str | None, Map("Operator 3")] = None
    """Reduction for ``variable_3``; the coupled stack uses ``max`` (peak temperature)."""


def bare_save_scalars() -> SaveScalarsSolver:
    """A SaveScalars with no configured reductions: exports every registered ``res:`` simulation/component scalar (Component force/torque, field energy) to ``scalars.dat``."""
    return SaveScalarsSolver(variable_1=None, operator_1=None, coefficient_1=None, variable_2=None, operator_2=None)
