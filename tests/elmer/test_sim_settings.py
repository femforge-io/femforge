"""Typed SimulationSettings and Constants: each model's ``to_elmer()`` emits the expected Simulation / constants block. Pure-logic Pydantic models reflecting their ``Map(...)`` fields."""

from __future__ import annotations

from femforge.elmer.config import (
    STEFAN_BOLTZMANN,
    Constants,
    CoordinateSystem,
    SifValue,
    SimulationType,
    SimulationSettings,
    TimesteppingMethod,
)
from femforge.materials.properties import PERMEABILITY_OF_VACUUM, PERMITTIVITY_OF_VACUUM

# The expected 3D steady-state Simulation block.
_3D_STEADY: dict[str, SifValue] = {
    "Max Output Level": 5,
    "Coordinate System": "Cartesian",
    "Coordinate Mapping(3)": "1 2 3",
    "Simulation Type": "Steady state",
    "Steady State Max Iterations": 1,
    "Output Intervals": 1,
    "Timestepping Method": "BDF",
    "BDF Order": 1,
    "Solver Input File": "case.sif",
    # No "Post File": VTU output belongs to the ResultOutputSolver; setting both trips AddVtuOutputSolverHack.
    # No "Output File": the default None skips the multi-GB restart/result file that nothing downstream reads.
}


def test_simulation_settings_block_is_byte_compatible() -> None:
    assert SimulationSettings().to_elmer() == _3D_STEADY


def test_post_file_is_opt_in() -> None:
    assert SimulationSettings(post_file="case.vtu").to_elmer()["Post File"] == "case.vtu"


def test_output_file_is_opt_in() -> None:
    assert SimulationSettings(output_file="case.result").to_elmer()["Output File"] == "case.result"


def test_simulation_settings_enum_knobs_are_typed() -> None:
    settings = SimulationSettings()
    assert settings.coordinate_system is CoordinateSystem.CARTESIAN
    assert settings.simulation_type is SimulationType.STEADY_STATE
    assert settings.timestepping_method is TimesteppingMethod.BDF


def test_magnetostatics_constants_emit_only_permeability() -> None:
    assert Constants(permeability_of_vacuum=PERMEABILITY_OF_VACUUM).to_elmer() == {"Permeability of Vacuum": 1.25663706212e-6}


def test_thermal_constants_emit_only_stefan_boltzmann() -> None:
    assert Constants(stefan_boltzmann=STEFAN_BOLTZMANN).to_elmer() == {"Stefan Boltzmann": 5.6704e-8}


def test_electrostatics_constants_emit_only_permittivity() -> None:
    assert Constants(permittivity_of_vacuum=PERMITTIVITY_OF_VACUUM).to_elmer() == {"Permittivity of Vacuum": 8.8541878128e-12}


def test_unset_constants_are_omitted() -> None:
    # A physics that activates no constants (linear elasticity) emits an empty block.
    assert Constants().to_elmer() == {}
