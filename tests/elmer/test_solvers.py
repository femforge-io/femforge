"""The typed Elmer solver hierarchy: each solver's ``to_elmer()`` emits the expected keyword/value block for the sif. Pure-logic: a solver is a Pydantic model reflecting its ``Map(...)`` fields."""

from __future__ import annotations

from femforge.elmer.config import (
    ElectrostaticsSolver,
    ExecSolver,
    GenericSolver,
    HeatSolver,
    IterativeMethod,
    LinearSolverType,
    MagnetoDynamicsCalcFieldsSolver,
    MagnetoDynamicsSolver,
    Preconditioning,
    ResultOutputSolver,
    SifValue,
    SolverBase,
    StressSolver,
)
from femforge.materials.properties import ElmerMaterialKeyword

# The expected sif keyword blocks, keyed by solver class, that to_elmer() must reproduce.
# Magnetostatics uses the BarMagnets3D recipe: BiCGStabl + no preconditioning (the gauge-non-unique AV system stalls under ILU),
# Abort Not Converged = False, Picard->Newton schedule, and no explicit Variable (the far-field BC references it as `AV {e}`).
_MAGNETODYNAMICS: dict[str, SifValue] = {
    "Equation": "MgDyn",
    "Procedure": '"MagnetoDynamics" "WhitneyAVSolver"',
    "Exec Solver": "Always",
    "Optimize Bandwidth": True,
    "Nonlinear System Convergence Tolerance": 1.0e-6,
    "Nonlinear System Max Iterations": 50,
    "Nonlinear System Newton After Iterations": 3,
    "Nonlinear System Newton After Tolerance": 1.0e-3,
    "Nonlinear System Relaxation Factor": 1.0,
    "Linear System Solver": "Iterative",
    "Linear System Iterative Method": "BiCGStabl",
    "BiCGStabl polynomial degree": 4,
    "Linear System Max Iterations": 500,
    "Linear System Convergence Tolerance": 1.0e-6,
    "Linear System Preconditioning": "None",
    "Linear System Abort Not Converged": False,
    "Linear System Residual Output": 10,
}

_CALC_FIELDS: dict[str, SifValue] = {
    "Equation": "MgDynCalc",
    "Procedure": '"MagnetoDynamics" "MagnetoDynamicsCalcFields"',
    "Exec Solver": "Always",
    "Optimize Bandwidth": True,
    # The Linear System block: recovering a nodal field is a mass-matrix (SPD) Galerkin solve, so CalcFields needs
    # a linear solver; inert when only elemental fields are computed.
    "Linear System Solver": "Iterative",
    "Linear System Iterative Method": "CG",
    "Linear System Max Iterations": 500,
    "Linear System Convergence Tolerance": 1.0e-8,
    "Linear System Preconditioning": "ILU0",
    "Calculate Magnetic Field Strength": True,
    "Calculate Magnetic Flux Density": True,
    "Calculate Nodal Forces": True,
    "Calculate Nodal Fields": False,
    "Calculate Elemental Fields": True,
    "Average Within Materials": True,
}

_RESULT_OUTPUT: dict[str, SifValue] = {
    "Equation": "ResultOutputSolver",
    "Procedure": '"ResultOutputSolve" "ResultOutputSolver"',
    "Exec Solver": "After timestep",
    "VTU Format": True,
    "Output File Name": 'File "case"',
    "Save Geometry Ids": True,
}

_HEAT: dict[str, SifValue] = {
    "Equation": "HeatSolver",
    "Procedure": '"HeatSolve" "HeatSolver"',
    "Variable": "Temperature",
    "Variable Dofs": 1,
    "Exec Solver": "Always",
    "Stabilize": True,
    "Optimize Bandwidth": True,
    "Steady State Convergence Tolerance": 1.0e-6,
    "Nonlinear System Convergence Tolerance": 1.0e-6,
    "Nonlinear System Max Iterations": 50,
    "Linear System Solver": "Direct",
    "Linear System Direct Method": "umfpack",
    "Linear System Iterative Method": "BiCGStab",
    "Linear System Max Iterations": 1000,
    "Linear System Convergence Tolerance": 1.0e-8,
    "Linear System Preconditioning": "ILU",
}

_ELECTROSTATICS: dict[str, SifValue] = {
    "Equation": "Electrostatics",
    "Procedure": '"StatElecSolve" "StatElecSolver"',
    "Variable": "Potential",
    "Exec Solver": "Always",
    "Stabilize": True,
    "Steady State Convergence Tolerance": 1.0e-5,
    "Linear System Solver": "Iterative",
    "Linear System Iterative Method": "BiCGStab",
    "Linear System Max Iterations": 500,
    "Linear System Convergence Tolerance": 1.0e-10,
    "Linear System Preconditioning": "ILU0",
    "Calculate Electric Field": True,
}

_STRESS: dict[str, SifValue] = {
    "Equation": "Linear elasticity",
    "Procedure": '"StressSolve" "StressSolver"',
    "Variable": "Displacement",
    "Variable Dofs": 3,
    "Exec Solver": "Always",
    "Optimize Bandwidth": True,
    "Steady State Convergence Tolerance": 1.0e-6,
    "Nonlinear System Convergence Tolerance": 1.0e-7,
    "Nonlinear System Max Iterations": 1,
    "Linear System Solver": "Iterative",
    "Linear System Iterative Method": "BiCGStab",
    "Linear System Max Iterations": 5000,
    "Linear System Convergence Tolerance": 1.0e-8,
    "Linear System Preconditioning": "ILU0",
    "Calculate Stresses": True,
    "Calculate Strains": True,
    "Calculate Principal": True,
    "Displace Mesh": False,
}


def test_magnetodynamics_block_is_byte_compatible() -> None:
    assert MagnetoDynamicsSolver().to_elmer() == _MAGNETODYNAMICS


def test_calc_fields_block_is_byte_compatible() -> None:
    assert MagnetoDynamicsCalcFieldsSolver().to_elmer() == _CALC_FIELDS


def test_result_output_block_is_byte_compatible() -> None:
    assert ResultOutputSolver().to_elmer() == _RESULT_OUTPUT


def test_heat_block_is_byte_compatible() -> None:
    assert HeatSolver().to_elmer() == _HEAT


def test_electrostatics_block_is_byte_compatible() -> None:
    assert ElectrostaticsSolver().to_elmer() == _ELECTROSTATICS


def test_stress_block_is_byte_compatible() -> None:
    assert StressSolver().to_elmer() == _STRESS


def test_max_iterations_is_a_typed_field_with_the_reference_default() -> None:
    # The workbench's `Linear System Max Iterations` knob is a typed field (not a post-hoc dict mutation); the
    # magnetostatics default mirrors the BarMagnets3D reference (500).
    solver = MagnetoDynamicsSolver()
    assert solver.linear_system_max_iterations == 500
    assert "linear_system_max_iterations" in type(solver).model_fields
    # And it round-trips through to_elmer under its sif keyword.
    assert solver.to_elmer()["Linear System Max Iterations"] == 500


def test_none_fields_are_omitted_from_the_block() -> None:
    # Optional keywords left at None must not appear in the emitted block.
    calc = MagnetoDynamicsCalcFieldsSolver().to_elmer()
    assert "Variable" not in calc  # CalcFields declares no Variable
    assert "Linear System Residual Output" not in calc  # left at None even though CalcFields has a Linear System block
    # ResultOutputSolver overrides only the exec phase; it carries no Stabilize.
    assert "Stabilize" not in ResultOutputSolver().to_elmer()


def test_enum_knobs_use_typed_values() -> None:
    # The string-valued knobs are enums (no magic words), and emit their exact sif strings.
    solver = MagnetoDynamicsSolver()
    assert solver.linear_system_solver is LinearSolverType.ITERATIVE
    assert solver.linear_system_iterative_method is IterativeMethod.BICGSTABL
    assert solver.linear_system_preconditioning is Preconditioning.NONE
    assert solver.exec_solver is ExecSolver.ALWAYS
    assert ResultOutputSolver().exec_solver is ExecSolver.AFTER_TIMESTEP


def test_solver_kinds_match_the_sif_block_labels() -> None:
    # `kind` is the discriminator AND the sif Solver label used by the equation and get_solver; it carries no Map, so it is never an emitted keyword.
    assert MagnetoDynamicsSolver().kind == "MagnetoDynamics"
    assert MagnetoDynamicsCalcFieldsSolver().kind == "MagnetoDynamicsCalcFields"
    assert ResultOutputSolver().kind == "ResultOutputSolver"
    assert HeatSolver().kind == "HeatSolver"
    assert ElectrostaticsSolver().kind == "Electrostatics"
    assert StressSolver().kind == "StressSolver"
    assert "kind" not in MagnetoDynamicsSolver().to_elmer()


def test_required_material_keys_are_declared_on_the_solver() -> None:
    # Each solver owns the material keywords it consumes as ElmerMaterialKeyword members (the same enum the material fields emit, so emit and require are statically linked).
    assert MagnetoDynamicsSolver.required_material_keys == frozenset({ElmerMaterialKeyword.RELATIVE_PERMEABILITY})
    assert HeatSolver.required_material_keys == frozenset({ElmerMaterialKeyword.HEAT_CONDUCTIVITY})
    assert ElectrostaticsSolver.required_material_keys == frozenset({ElmerMaterialKeyword.RELATIVE_PERMITTIVITY})
    # Post-processors and the linear-elasticity solver declare no per-body required property (the base default).
    assert MagnetoDynamicsCalcFieldsSolver.required_material_keys == frozenset()
    assert ResultOutputSolver.required_material_keys == frozenset()
    assert StressSolver.required_material_keys == frozenset({ElmerMaterialKeyword.YOUNGS_MODULUS, ElmerMaterialKeyword.POISSON_RATIO})


def test_solver_base_is_the_shared_supertype() -> None:
    # Every concrete solver is a SolverBase, so a preset can hold a list[SolverBase].
    for solver in (
        MagnetoDynamicsSolver(),
        MagnetoDynamicsCalcFieldsSolver(),
        ResultOutputSolver(),
        HeatSolver(),
        ElectrostaticsSolver(),
        StressSolver(),
    ):
        assert isinstance(solver, SolverBase)


def test_prepare_for_mpi_moves_the_coil_solver_onto_mumps() -> None:
    import pytest

    from femforge.elmer.config import CoilCurrentSolver, StatCurrentSolver, physics_config, prepare_for_mpi
    from femforge.elmer.physics import Physics

    # CoilSolver stays Direct but on MUMPS (umfpack is serial-only); an iterative fallback is band-unsafe.
    stack = physics_config(Physics.COIL_MAGNETOSTATICS)
    prepare_for_mpi(stack)
    coil = stack.solver(CoilCurrentSolver)
    assert coil.linear_system_solver is LinearSolverType.DIRECT
    assert coil.to_elmer()["Linear System Direct Method"] == "MUMPS"

    # Any other Direct solve must refuse loudly rather than run unvalidated under partitioning.
    conduction = physics_config(Physics.CURRENT_CONDUCTION)
    assert conduction.solver(StatCurrentSolver).linear_system_solver is LinearSolverType.DIRECT
    with pytest.raises(ValueError, match="unvalidated"):
        prepare_for_mpi(conduction)

    # A stack with no Direct solve passes through untouched.
    prepare_for_mpi(physics_config(Physics.MAGNETOSTATICS))


def test_needs_halobc_only_for_the_coil_stack() -> None:
    # Halo elements are for the coil stack's electrode boundary integrals ONLY: they double-count the Component
    # force lumping of partition-cut bodies, so every other stack must partition without them.
    from femforge.elmer.config import needs_halobc, physics_config
    from femforge.elmer.physics import Physics

    assert needs_halobc(physics_config(Physics.COIL_MAGNETOSTATICS))
    assert not needs_halobc(physics_config(Physics.MAGNETOSTATICS))
    assert not needs_halobc(physics_config(Physics.MAGNETOSTATICS_2D))


def test_extra_keywords_merge_last_and_win() -> None:
    # The escape hatch: any not-yet-typed sif keyword rides through verbatim, and a collision overrides the typed field.
    emitted = MagnetoDynamicsSolver(extra_keywords={"Quadratic Approximation": True, "Equation": "Overridden"}).to_elmer()
    assert emitted["Quadratic Approximation"] is True
    assert emitted["Equation"] == "Overridden"


def test_generic_solver_authors_an_arbitrary_block() -> None:
    solver = GenericSolver(equation="MyEq", procedure='"MyLib" "MySolver"', extra_keywords={"Special Flag": True})
    emitted = solver.to_elmer()
    assert emitted["Equation"] == "MyEq"
    assert emitted["Procedure"] == '"MyLib" "MySolver"'
    assert emitted["Special Flag"] is True
    assert solver.kind == "Generic"
