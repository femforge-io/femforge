"""The Elmer ``Simulation`` block and its enumerated keyword values."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from femforge.common.pydantic import Map
from femforge.elmer.config.common.keywords import SifKeywordModel


class CoordinateSystem(StrEnum):
    """Elmer's ``Coordinate System``."""

    CARTESIAN = "Cartesian"
    """Cartesian coordinates."""

    CYLINDRIC = "Cylindric"
    """Full cylindrical coordinates."""

    CYLINDRIC_SYMMETRIC = "Cylindric Symmetric"
    """2D cylindrically symmetric (r, z) coordinates, integrated per radian."""

    AXI_SYMMETRIC = "Axi Symmetric"
    """2D axisymmetric (r, z) coordinates, integrated per radian."""

    POLAR = "Polar"
    """Polar coordinates."""

    @property
    def per_radian(self) -> bool:
        """Whether Elmer integrates domain integrals per radian here (the 2D symmetric systems), so lumped outputs need a 2π revolution factor."""
        return self in (CoordinateSystem.AXI_SYMMETRIC, CoordinateSystem.CYLINDRIC_SYMMETRIC)


class SimulationType(StrEnum):
    """Elmer's ``Simulation Type``."""

    STEADY_STATE = "Steady state"
    """A steady-state solve."""

    TRANSIENT = "Transient"
    """A time-dependent solve."""

    SCANNING = "Scanning"
    """A sweep of steady-state solves over a scanned parameter."""


class TimesteppingMethod(StrEnum):
    """Elmer's ``Timestepping Method`` (paired with ``BDF Order`` for BDF)."""

    BDF = "BDF"
    """Backward differentiation formula, of order ``bdf_order``."""

    NEWMARK = "Newmark"
    """The Newmark-beta method."""

    RUNGE_KUTTA = "Runge-Kutta"
    """A Runge-Kutta method."""


class SimulationSettings(SifKeywordModel):
    """The Elmer ``Simulation`` block. Defaults are a 3D Cartesian steady-state run, used for magnetostatics and steady thermal."""

    max_output_level: Annotated[int, Map("Max Output Level")] = 5
    """Verbosity of ElmerSolver's console output (0-quiet … higher-noisier)."""

    coordinate_system: Annotated[CoordinateSystem, Map("Coordinate System")] = CoordinateSystem.CARTESIAN
    """The simulation coordinate system."""

    coordinate_mapping: Annotated[str, Map("Coordinate Mapping(3)")] = "1 2 3"
    """Axis permutation for the coordinate system."""

    simulation_type: Annotated[SimulationType, Map("Simulation Type")] = SimulationType.STEADY_STATE
    """Steady-state, transient, or scanning run."""

    steady_state_max_iterations: Annotated[int, Map("Steady State Max Iterations")] = 1
    """Max steady-state (coupled-system) iterations."""

    output_intervals: Annotated[int, Map("Output Intervals")] = 1
    """Write results every N steps."""

    timestepping_method: Annotated[TimesteppingMethod, Map("Timestepping Method")] = TimesteppingMethod.BDF
    """Time integration scheme."""

    bdf_order: Annotated[int, Map("BDF Order")] = 1
    """Order of the BDF timestepping method."""

    solver_input_file: Annotated[str, Map("Solver Input File")] = "case.sif"
    """Name of the generated .sif."""

    post_file: Annotated[str | None, Map("Post File")] = None
    """ParaView/VTU post-processing output file; None (the default) leaves VTU output to the ResultOutputSolver —
    setting both trips Elmer's AddVtuOutputSolverHack and its log spam."""

    output_file: Annotated[str | None, Map("Output File")] = None
    """ElmerSolver restart/result file; None (the default) skips it — nothing downstream reads it and it grows to
    multi-GB on large solves. Set a name only when a restart is wanted."""
