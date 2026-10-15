"""The divergence-conforming coil current source: Elmer's CoilSolver."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from femforge.common.pydantic import Map
from femforge.elmer.config.common.solver_base import (
    EquationField,
    ExecSolver,
    ExecSolverField,
    IterativeMethod,
    LinearDirectMethodField,
    LinearIterativeMethodField,
    LinearMaxIterationsField,
    LinearSolverType,
    LinearSolverTypeField,
    LinearSystem,
    NonlinearSystem,
    OptimizeBandwidthField,
    ProcedureField,
    SolverBase,
    SteadyState,
)
from femforge.materials.properties import ElmerMaterialKeyword


class CoilCurrentSolver(SolverBase, LinearSystem, NonlinearSystem, SteadyState):
    """Elmer's ``CoilSolver``: the divergence-conforming coil current distribution, the source stage of the ``COIL_MAGNETOSTATICS`` chain.

    It acts only on [`CoilDrive`][femforge.physical.conditions.CoilDrive] bodies, because Elmer requires every body it covers to belong to a ``Component``.
    """

    kind: Literal["CoilCurrent"] = "CoilCurrent"
    """Discriminator that names this solver, doubling as its sif Solver label."""

    required_material_keys: ClassVar[frozenset[ElmerMaterialKeyword]] = frozenset({ElmerMaterialKeyword.ELECTRIC_CONDUCTIVITY})
    conductor_only: ClassVar[bool] = True

    equation: EquationField = "CoilSolver"
    procedure: ProcedureField = '"CoilSolver" "CoilSolver"'
    exec_solver: ExecSolverField = ExecSolver.BEFORE_SIMULATION
    optimize_bandwidth: OptimizeBandwidthField = True

    linear_system_solver: LinearSolverTypeField = LinearSolverType.DIRECT
    linear_system_direct_method: LinearDirectMethodField = "umfpack"
    linear_system_iterative_method: LinearIterativeMethodField = IterativeMethod.BICGSTAB
    linear_system_max_iterations: LinearMaxIterationsField = 2000

    coil_closed: Annotated[bool, Map("Coil Closed")] = False
    """False: an open coil whose ends are the ``Coil Start`` / ``Coil End`` electrode boundaries."""

    normalize_coil_current: Annotated[bool, Map("Normalize Coil Current")] = False
    """Must stay OFF for a massive coil: it rescales |J| to a UNIFORM magnitude, which on a varying cross-section
    authors a non-conservative source that Jfix redistributes (silently reduced drive current in the winding).
    ``Desired Coil Current`` scaling happens regardless, so the total current is still exact with this off."""

    calculate_elemental_fields: Annotated[bool, Map("Calculate Elemental Fields")] = True
    """Export the DG ``CoilCurrent e`` field (what ``Use Elemental CoilCurrent`` reads); the nodal field stays too."""
