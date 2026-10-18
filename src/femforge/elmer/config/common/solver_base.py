"""The typed Elmer solver hierarchy's shared structure: keyword enums, single-sourced field declarations, the Linear/Nonlinear/SteadyState mixins, and ``SolverBase``."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, ClassVar, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from femforge.common.pydantic import Map
from femforge.elmer.config.common.keywords import SifKeywordModel, SifValue, equation_items
from femforge.materials.properties import ElmerMaterialKeyword


if TYPE_CHECKING:
    from femforge.frames import Frame


class ExecSolver(StrEnum):
    """When Elmer executes a solver (the ``Exec Solver`` keyword)."""

    ALWAYS = "Always"
    """Run in the normal solve sequence."""

    BEFORE_SIMULATION = "Before simulation"
    """Run once before the simulation."""

    AFTER_SIMULATION = "After simulation"
    """Run once after the simulation."""

    BEFORE_TIMESTEP = "Before timestep"
    """Run at the start of each timestep."""

    AFTER_TIMESTEP = "After timestep"
    """Run at the end of each timestep."""

    NEVER = "Never"
    """Never run."""


class LinearSolverType(StrEnum):
    """The ``Linear System Solver`` strategy: an iterative Krylov method or a direct factorization."""

    ITERATIVE = "Iterative"
    """A Krylov iteration, chosen by ``linear_system_iterative_method``."""

    DIRECT = "Direct"
    """A direct factorization, chosen by ``linear_system_direct_method``."""


class IterativeMethod(StrEnum):
    """The Krylov method for an iterative linear solve (``Linear System Iterative Method``)."""

    BICGSTAB = "BiCGStab"
    """Biconjugate gradient stabilized."""

    # BiCGStab(l), paired with `bicgstabl_polynomial_degree`: the gauge-non-unique WhitneyAV system stalls plain
    # BiCGStab+ILU; BiCGStabl with no preconditioner is the reference BarMagnets3D recipe.
    BICGSTABL = "BiCGStabl"
    """BiCGStab(l), of degree ``bicgstabl_polynomial_degree``."""

    CG = "CG"
    """Conjugate gradient, for symmetric positive-definite systems."""

    CGS = "CGS"
    """Conjugate gradient squared."""

    GMRES = "GMRES"
    """Generalized minimal residual."""

    GCR = "GCR"
    """Generalized conjugate residual."""

    TFQMR = "TFQMR"
    """Transpose-free quasi-minimal residual."""


class Preconditioning(StrEnum):
    """The ``Linear System Preconditioning`` for an iterative solve."""

    NONE = "None"
    """No preconditioning."""

    DIAGONAL = "Diagonal"
    """Diagonal (Jacobi) scaling."""

    ILU0 = "ILU0"
    """Incomplete LU without fill-in."""

    ILU1 = "ILU1"
    """Incomplete LU with fill level 1."""

    ILU2 = "ILU2"
    """Incomplete LU with fill level 2."""

    ILU = "ILU"
    """Incomplete LU without an explicit fill level."""

    ILUT = "ILUT"
    """Incomplete LU with threshold dropping."""


# Single-sourced field declarations. A mixin/base declares the field with one of these
# aliases, and a concrete solver redeclares it with the same alias but a different
# default — so the `Map(...)` keyword is written once and cannot drift between them.
EquationField = Annotated[str, Map("Equation"), Field(description="Elmer equation/solver name.")]
ProcedureField = Annotated[str, Map("Procedure"), Field(description='Elmer Procedure: "library" "function".')]
VariableField = Annotated[str | None, Map("Variable"), Field(description="Primary solver variable; None for solvers that declare none.")]
VariableDofsField = Annotated[int | None, Map("Variable Dofs"), Field(description="Degrees of freedom per node; None lets Elmer infer.")]
ExecSolverField = Annotated[ExecSolver, Map("Exec Solver"), Field(description="When Elmer runs this solver.")]
StabilizeField = Annotated[bool | None, Map("Stabilize"), Field(description="Enable stabilization; None omits the keyword.")]
OptimizeBandwidthField = Annotated[bool | None, Map("Optimize Bandwidth"), Field(description="Reorder DOFs to reduce matrix bandwidth; None omits the keyword.")]

LinearSolverTypeField = Annotated[LinearSolverType | None, Map("Linear System Solver"), Field(description="Iterative (Krylov) or Direct linear solve.")]
LinearDirectMethodField = Annotated[str | None, Map("Linear System Direct Method"), Field(description='Factorization backend for a Direct solve (e.g. "umfpack"); None omits the keyword.')]
LinearIterativeMethodField = Annotated[IterativeMethod | None, Map("Linear System Iterative Method"), Field(description="Krylov method when the solve is iterative.")]
LinearMaxIterationsField = Annotated[int | None, Map("Linear System Max Iterations"), Field(description="Iteration cap for the linear solve.")]
LinearConvToleranceField = Annotated[float | None, Map("Linear System Convergence Tolerance"), Field(description="Residual tolerance for the linear solve.")]
LinearPreconditioningField = Annotated[Preconditioning | None, Map("Linear System Preconditioning"), Field(description="Preconditioner for the iterative solve.")]
LinearResidualOutputField = Annotated[int | None, Map("Linear System Residual Output"), Field(description="Print the linear residual every N iterations; None omits it.")]
LinearAbortNotConvergedField = Annotated[
    bool | None,
    Map("Linear System Abort Not Converged"),
    Field(description="Abort the run if the linear solve doesn't converge; False lets a stalled gauge-non-unique AV solve proceed."),
]
BiCGStablPolynomialDegreeField = Annotated[int | None, Map("BiCGStabl polynomial degree"), Field(description="Polynomial degree l for the BiCGStab(l) Krylov method.")]

NonlinearConvToleranceField = Annotated[float | None, Map("Nonlinear System Convergence Tolerance"), Field(description="Residual tolerance for the nonlinear (outer) iteration.")]
NonlinearMaxIterationsField = Annotated[int | None, Map("Nonlinear System Max Iterations"), Field(description="Iteration cap for the nonlinear (outer) iteration.")]
NonlinearNewtonAfterIterationsField = Annotated[int | None, Map("Nonlinear System Newton After Iterations"), Field(description="Switch from Picard to Newton after this many outer iterations.")]
NonlinearNewtonAfterToleranceField = Annotated[
    float | None,
    Map("Nonlinear System Newton After Tolerance"),
    Field(description="Switch from Picard to Newton once the residual drops below this."),
]
NonlinearRelaxationFactorField = Annotated[float | None, Map("Nonlinear System Relaxation Factor"), Field(description="Under/over-relaxation factor for the nonlinear update (1 = none).")]
NonlinearConvMeasureField = Annotated[str | None, Map("Nonlinear System Convergence Measure"), Field(description='Nonlinear residual measure (e.g. "solution"); None keeps Elmer\'s default.')]

SteadyStateConvToleranceField = Annotated[float | None, Map("Steady State Convergence Tolerance"), Field(description="Steady-state convergence tolerance.")]


class LinearSystem(BaseModel):
    """Mixin: the ``Linear System *`` settings for a solver that does a linear solve. Defaults are sensible iterative values; concrete solvers tune the iteration cap and tolerances they need."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    # Overridable knobs keep their descriptions in the shared `*Field` aliases: a concrete override would reset a docstring-derived description to None.
    linear_system_solver: LinearSolverTypeField = LinearSolverType.ITERATIVE
    linear_system_direct_method: LinearDirectMethodField = None
    linear_system_iterative_method: LinearIterativeMethodField = IterativeMethod.BICGSTAB
    linear_system_max_iterations: LinearMaxIterationsField = None
    linear_system_convergence_tolerance: LinearConvToleranceField = 1.0e-8
    linear_system_preconditioning: LinearPreconditioningField = Preconditioning.ILU0
    linear_system_residual_output: LinearResidualOutputField = None
    linear_system_abort_not_converged: LinearAbortNotConvergedField = None
    bicgstabl_polynomial_degree: BiCGStablPolynomialDegreeField = None


class NonlinearSystem(BaseModel):
    """Mixin: the ``Nonlinear System *`` (outer-iteration) convergence settings."""

    nonlinear_system_convergence_tolerance: NonlinearConvToleranceField = None
    nonlinear_system_max_iterations: NonlinearMaxIterationsField = None
    nonlinear_system_newton_after_iterations: NonlinearNewtonAfterIterationsField = None
    nonlinear_system_newton_after_tolerance: NonlinearNewtonAfterToleranceField = None
    nonlinear_system_relaxation_factor: NonlinearRelaxationFactorField = None
    nonlinear_system_convergence_measure: NonlinearConvMeasureField = None


class SteadyState(BaseModel):
    """Mixin: the ``Steady State Convergence Tolerance`` for a steady-state run."""

    steady_state_convergence_tolerance: SteadyStateConvToleranceField = None


class SolverBase(SifKeywordModel):
    """Base for a typed Elmer solver: its fields are its sif keywords."""

    kind: str
    """Discriminator that names this solver, doubling as its sif Solver label."""

    required_material_keys: ClassVar[frozenset[ElmerMaterialKeyword]] = frozenset()
    """The material keywords every body this solver acts on must supply, checked by ``Sim.validate``."""

    conductor_only: ClassVar[bool] = False
    """Restrict this solver's equation to conductor bodies: solving a conduction variable over dielectrics too puts
    ~21 decades of σ contrast into one matrix — singular for a direct factorization, a stall for preconditioned CG."""

    equation: EquationField
    procedure: ProcedureField
    variable: VariableField = None
    variable_dofs: VariableDofsField = None
    exec_solver: ExecSolverField = ExecSolver.ALWAYS
    stabilize: StabilizeField = None
    optimize_bandwidth: OptimizeBandwidthField = None

    def equation_keywords(self) -> dict[str, SifValue]:
        """The keywords this solver contributes to the Equation block, reflected from its ``EquationScope()`` fields."""
        out: dict[str, SifValue] = {}

        for key, value in equation_items(self):
            if isinstance(value, (str, int, float)):
                out[key] = value

        return out

    def fit_frame(self, frame: Frame) -> Self:
        """This solver as it solves under ``frame``: itself when the frame changes nothing, else a fitted copy."""
        return self

    def check_frame(self, frame: Frame) -> None:
        """Raise when this solver cannot solve under ``frame``."""


# A concrete solver type, for the typed PhysicsConfig.solver(kind) accessor.
SolverT = TypeVar("SolverT", bound=SolverBase)
