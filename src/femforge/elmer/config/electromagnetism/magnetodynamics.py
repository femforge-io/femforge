"""3D magnetostatics (Elmer's WhitneyAVSolver) and its MagnetoDynamicsCalcFields post-processor."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from femforge.common.pydantic import Map
from femforge.elmer.config.common.solver_base import (
    BiCGStablPolynomialDegreeField,
    EquationField,
    IterativeMethod,
    LinearAbortNotConvergedField,
    LinearConvToleranceField,
    LinearIterativeMethodField,
    LinearMaxIterationsField,
    LinearPreconditioningField,
    LinearResidualOutputField,
    LinearSystem,
    NonlinearConvToleranceField,
    NonlinearMaxIterationsField,
    NonlinearNewtonAfterIterationsField,
    NonlinearNewtonAfterToleranceField,
    NonlinearRelaxationFactorField,
    NonlinearSystem,
    OptimizeBandwidthField,
    Preconditioning,
    ProcedureField,
    SolverBase,
    SteadyState,
)
from femforge.materials.properties import ElmerMaterialKeyword


class MagnetoDynamicsSolver(SolverBase, LinearSystem, NonlinearSystem, SteadyState):
    """3D magnetostatics (WhitneyAVSolver), with each magnet's drive entering as ``Magnetization`` on its material rather than as a body force.

    Leave ``variable`` unset, so the far-field boundary condition can still name ``AV {e}``.
    """

    kind: Literal["MagnetoDynamics"] = "MagnetoDynamics"
    """Discriminator that names this solver, doubling as its sif Solver label."""

    required_material_keys: ClassVar[frozenset[ElmerMaterialKeyword]] = frozenset({ElmerMaterialKeyword.RELATIVE_PERMEABILITY})

    equation: EquationField = "MgDyn"
    procedure: ProcedureField = '"MagnetoDynamics" "WhitneyAVSolver"'
    optimize_bandwidth: OptimizeBandwidthField = True

    nonlinear_system_convergence_tolerance: NonlinearConvToleranceField = 1.0e-6
    nonlinear_system_max_iterations: NonlinearMaxIterationsField = 50
    nonlinear_system_newton_after_iterations: NonlinearNewtonAfterIterationsField = 3
    nonlinear_system_newton_after_tolerance: NonlinearNewtonAfterToleranceField = 1.0e-3
    nonlinear_system_relaxation_factor: NonlinearRelaxationFactorField = 1.0

    linear_system_iterative_method: LinearIterativeMethodField = IterativeMethod.BICGSTABL
    bicgstabl_polynomial_degree: BiCGStablPolynomialDegreeField = 4
    linear_system_preconditioning: LinearPreconditioningField = Preconditioning.NONE
    linear_system_max_iterations: LinearMaxIterationsField = 500
    linear_system_convergence_tolerance: LinearConvToleranceField = 1.0e-6
    linear_system_abort_not_converged: LinearAbortNotConvergedField = False
    linear_system_residual_output: LinearResidualOutputField = 10

    fix_input_current_density: Annotated[bool | None, Map("Fix Input Current Density")] = None
    """Project the source J onto a divergence-free field before the AV solve: the discrete J is not exactly solenoidal
    edge-element-wise, and an unfixed source makes the gauge-non-unique system inconsistent. None omits the keyword."""

    use_elemental_coilcurrent: Annotated[bool | None, Map("Use Elemental CoilCurrent")] = None
    """Read the source J from CoilSolver's *elemental* (DG) field instead of a nodal Body Force: nodal J is
    Galerkin-averaged across the conductor-air interface and silently dilutes the drive ~2x on thin traces.
    Requires a CoilSolver in the stack; None omits the keyword."""


class MagnetoDynamicsCalcFieldsSolver(SolverBase, LinearSystem):
    """
    Post-processing: recover B, H, fluxes and nodal forces from the computed A. Inherits ``Linear System *`` because
    nodal-field recovery solves an (SPD, hence CG) mass-matrix system — without a linear solver Elmer aborts.
    """

    kind: Literal["MagnetoDynamicsCalcFields"] = "MagnetoDynamicsCalcFields"
    """Discriminator that names this solver, doubling as its sif Solver label."""

    equation: EquationField = "MgDynCalc"
    procedure: ProcedureField = '"MagnetoDynamics" "MagnetoDynamicsCalcFields"'
    optimize_bandwidth: OptimizeBandwidthField = True

    linear_system_iterative_method: LinearIterativeMethodField = IterativeMethod.CG
    linear_system_max_iterations: LinearMaxIterationsField = 500

    potential_variable: Annotated[str | None, Map("Potential Variable")] = None
    """The vector-potential variable to post-process; None lets the solver pick up the (default-named) MgDyn variable."""

    average_within_materials: Annotated[bool | None, Map("Average Within Materials")] = True
    """Average recovered fields within each material so the B/H jump at a magnet surface is resolved cleanly."""

    calculate_magnetic_field_strength: Annotated[bool, Map("Calculate Magnetic Field Strength")] = True
    """Recover H."""

    calculate_magnetic_flux_density: Annotated[bool, Map("Calculate Magnetic Flux Density")] = True
    """Recover B."""

    calculate_nodal_forces: Annotated[bool, Map("Calculate Nodal Forces")] = True
    """Recover nodal forces (drives the motor-force optimisation)."""

    calculate_nodal_fields: Annotated[bool, Map("Calculate Nodal Fields")] = False
    """Recover nodal field values."""

    calculate_joule_heating: Annotated[bool | None, Map("Calculate Joule Heating")] = None
    """Recover the eddy/Joule loss field. Identically zero for a steady real-valued solve — set False there:
    the zero-RHS projection it triggers when a Current Density body force is present segfaults CalcFields."""

    calculate_electric_field: Annotated[bool | None, Map("Calculate Electric Field")] = None
    """Recover E = -dA/dt - grad V. Identically zero at steady state — set False for the chained coil solve,
    where its zero-RHS projection is what segfaults CalcFields after the B components."""

    calculate_magnetic_vector_potential: Annotated[bool | None, Map("Calculate Magnetic Vector Potential")] = None
    """Project the edge-element av onto a nodal A field; None keeps the solver default."""

    separate_magnetic_energy: Annotated[bool | None, Map("Separate Magnetic Energy")] = None
    """Export the magnetic field energy as SaveScalars-visible scalars (the ElmerCSC mgdyn_bh recipe) — the
    energy-method inductance observable: L_eff = 2W/I²."""

    calculate_relative_permeability: Annotated[bool | None, Map("Calculate Relative Permeability")] = None
    """Recover the local relative permeability; the mgdyn_bh energy recipe enables it alongside the energy export."""

    calculate_elemental_fields: Annotated[bool, Map("Calculate Elemental Fields")] = True
    """Recover elemental field values."""
