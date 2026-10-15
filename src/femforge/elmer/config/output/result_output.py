"""VTU output for ParaView: Elmer's ResultOutputSolver."""

from __future__ import annotations

from typing import Annotated, Literal

from femforge.common.pydantic import Map
from femforge.elmer.config.common.solver_base import EquationField, ExecSolver, ExecSolverField, ProcedureField, SolverBase


class ResultOutputSolver(SolverBase):
    """Write VTU output for ParaView."""

    kind: Literal["ResultOutputSolver"] = "ResultOutputSolver"
    """Discriminator that names this solver, doubling as its sif Solver label."""

    equation: EquationField = "ResultOutputSolver"
    procedure: ProcedureField = '"ResultOutputSolve" "ResultOutputSolver"'
    exec_solver: ExecSolverField = ExecSolver.AFTER_TIMESTEP

    vtu_format: Annotated[bool, Map("VTU Format")] = True
    """Write VTU (ParaView) output."""

    output_file_name: Annotated[str, Map("Output File Name")] = 'File "case"'
    """Stem of the written VTU (Elmer emits e.g. ``case_t0001.vtu``); pinned so the name never depends on Elmer defaults."""

    save_geometry_ids: Annotated[bool, Map("Save Geometry Ids")] = True
    """Tag output cells with their gmsh geometry ids."""

    # Naming ANY saved field switches the VTU from save-everything to save-exactly-these (GeometryIds still ride
    # along via Save Geometry Ids); the scalar/vector split is Elmer's — its keyword families are rank-typed.
    saved_scalar_fields: Annotated[list[str] | None, Map("Scalar Field")] = None
    """Scalar fields to save (each a full sif RHS, e.g. ``'String "joule heating"'``); None saves every active variable."""

    saved_vector_fields: Annotated[list[str] | None, Map("Vector Field")] = None
    """Vector fields to save; None saves every active variable."""

    discontinuous_bodies: Annotated[bool | None, Map("Discontinuous Bodies")] = None
    """Average elemental fields body-wise onto a minimal discontinuous node set, so the VTU keeps cross-body discontinuities."""
