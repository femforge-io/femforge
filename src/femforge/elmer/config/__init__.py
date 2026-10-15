"""Typed Elmer configuration: Pydantic models whose fields ARE their sif keywords, grouped by physics (`common`, `electromagnetism`, `thermal`, `mechanics`, `output`), plus the per-physics presets."""

from __future__ import annotations

from femforge.elmer.config.common.constants import STEFAN_BOLTZMANN, Constants
from femforge.elmer.config.common.generic import GenericSolver
from femforge.elmer.config.common.keywords import SifKeywordModel, SifValue
from femforge.elmer.config.common.operating_point import OperatingPoint
from femforge.elmer.config.common.physics_config import PhysicsConfig, SolverUnion, convergence_targets, needs_halobc, prepare_for_mpi
from femforge.elmer.config.common.presets import physics_config, solved_stack
from femforge.elmer.config.common.selection import PhysicsSelection
from femforge.elmer.config.common.simulation import CoordinateSystem, SimulationSettings, SimulationType, TimesteppingMethod
from femforge.elmer.config.common.solver_base import (
    ExecSolver,
    IterativeMethod,
    LinearSolverType,
    LinearSystem,
    NonlinearSystem,
    Preconditioning,
    SolverBase,
    SolverT,
    SteadyState,
)
from femforge.elmer.config.electromagnetism.coil_current import CoilCurrentSolver
from femforge.elmer.config.electromagnetism.electrostatics import ElectrostaticsSolver
from femforge.elmer.config.electromagnetism.magnetodynamics import MagnetoDynamicsCalcFieldsSolver, MagnetoDynamicsSolver
from femforge.elmer.config.electromagnetism.magnetodynamics_2d import MagnetoDynamics2DSolver
from femforge.elmer.config.electromagnetism.stat_current import StatCurrentSolver
from femforge.elmer.config.mechanics.stress import StressSolver
from femforge.elmer.config.output.result_output import ResultOutputSolver
from femforge.elmer.config.output.save_scalars import SaveScalarsSolver, bare_save_scalars
from femforge.elmer.config.thermal.heat import HeatSolver


__all__ = [
    "PhysicsSelection",
    "PhysicsConfig",
    "physics_config",
    "solved_stack",
    "prepare_for_mpi",
    "needs_halobc",
    "convergence_targets",
    "OperatingPoint",
    "SimulationSettings",
    "Constants",
    "STEFAN_BOLTZMANN",
    "SifValue",
    "SifKeywordModel",
    "SolverBase",
    "SolverT",
    "SolverUnion",
    "LinearSystem",
    "NonlinearSystem",
    "SteadyState",
    "MagnetoDynamicsSolver",
    "MagnetoDynamics2DSolver",
    "MagnetoDynamicsCalcFieldsSolver",
    "ResultOutputSolver",
    "HeatSolver",
    "StatCurrentSolver",
    "CoilCurrentSolver",
    "SaveScalarsSolver",
    "bare_save_scalars",
    "ElectrostaticsSolver",
    "StressSolver",
    "GenericSolver",
    "CoordinateSystem",
    "SimulationType",
    "TimesteppingMethod",
    "ExecSolver",
    "LinearSolverType",
    "IterativeMethod",
    "Preconditioning",
]
