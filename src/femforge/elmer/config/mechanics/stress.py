"""Linear elasticity: Elmer's StressSolve."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, ClassVar, Literal, Self

from femforge.common.pydantic import Map
from femforge.elmer.config.common.keywords import EquationScope
from femforge.elmer.config.common.solver_base import (
    EquationField,
    IterativeMethod,
    LinearConvToleranceField,
    LinearIterativeMethodField,
    LinearMaxIterationsField,
    LinearPreconditioningField,
    LinearSystem,
    NonlinearConvToleranceField,
    NonlinearMaxIterationsField,
    NonlinearSystem,
    OptimizeBandwidthField,
    Preconditioning,
    ProcedureField,
    SolverBase,
    SteadyState,
    SteadyStateConvToleranceField,
    VariableDofsField,
    VariableField,
)
from femforge.elmer.variables import ElmerVariable
from femforge.materials.properties import ElmerMaterialKeyword


if TYPE_CHECKING:
    from femforge.frames import Frame


class StressSolver(SolverBase, LinearSystem, NonlinearSystem, SteadyState):
    """Linear elasticity (Elmer's StressSolve), reading ``Youngs Modulus`` and ``Poisson Ratio`` from every body's material."""

    required_material_keys: ClassVar[frozenset[ElmerMaterialKeyword]] = frozenset({ElmerMaterialKeyword.YOUNGS_MODULUS, ElmerMaterialKeyword.POISSON_RATIO})

    kind: Literal["StressSolver"] = "StressSolver"
    """Discriminator that names this solver, doubling as its sif Solver label."""

    display_variable: ClassVar[ElmerVariable | None] = ElmerVariable.DISPLACEMENT

    equation: EquationField = "Linear elasticity"
    procedure: ProcedureField = '"StressSolve" "StressSolver"'
    variable: VariableField = ElmerVariable.DISPLACEMENT
    variable_dofs: VariableDofsField = 3
    optimize_bandwidth: OptimizeBandwidthField = True

    steady_state_convergence_tolerance: SteadyStateConvToleranceField = 1.0e-6
    nonlinear_system_convergence_tolerance: NonlinearConvToleranceField = 1.0e-7
    nonlinear_system_max_iterations: NonlinearMaxIterationsField = 1
    linear_system_iterative_method: LinearIterativeMethodField = IterativeMethod.CG
    linear_system_preconditioning: LinearPreconditioningField = Preconditioning.ILU1
    linear_system_max_iterations: LinearMaxIterationsField = 5000
    # ILU-preconditioned Krylov solves of these systems stagnate just above 1e-8.
    linear_system_convergence_tolerance: LinearConvToleranceField = 1.0e-7

    calculate_stresses: Annotated[bool, Map("Calculate Stresses")] = True
    """Recover the stress tensor from the displacement."""

    calculate_strains: Annotated[bool, Map("Calculate Strains")] = True
    """Recover the strain tensor from the displacement."""

    calculate_principal: Annotated[bool, Map("Calculate Principal")] = True
    """Recover the principal stresses and strains."""

    displace_mesh: Annotated[bool, Map("Displace Mesh")] = False
    """Move the output mesh by the displacement; off keeps the VTU undeformed, which Elmer's default does not."""

    plane_stress: Annotated[bool | None, Map("Plane Stress"), EquationScope()] = None
    """Solve a 2D section as plane stress (True) or plane strain (False); None in 3D, set from the frame."""

    def fit_frame(self, frame: Frame) -> Self:
        """A copy solving ``frame``'s dimension, with its plane state."""
        return self.model_copy(update={"variable_dofs": frame.model_dim, "plane_stress": frame.plane_stress})

    def check_frame(self, frame: Frame) -> None:
        """Raise when ``variable_dofs`` is not ``frame``'s dimension."""
        if self.variable_dofs not in (None, frame.model_dim):
            raise ValueError(f"StressSolver has Variable Dofs = {self.variable_dofs}, but the {frame.label} frame solves {frame.model_dim} displacement components.")
