"""The typed outcome of a solve: Elmer outputs parsed into validated, pint-typed fields."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from femforge.common.files import BundleFile
from femforge.common.vector import Vec3
from femforge.frames import Axis, FrameUnion
from femforge.physical.units import FluxDensity, Force, Quantity, Temperature, Torque, quantity


class RunStatus(StrEnum):
    """The terminal state of a solve."""

    OK = "ok"
    """The solve completed cleanly."""

    DIVERGED = "diverged"
    """The solve finished without converging."""

    ERROR = "error"
    """The solve or its pipeline failed."""

    STALLED = "stalled"
    """The driver's watchdog stopped a linear solve whose residual stopped improving."""


class LifecycleStatus(StrEnum):
    """A manifest's status before its solve reached a terminal [`RunStatus`][]."""

    RUNNING = "running"
    """The pipeline is executing."""

    ABANDONED = "abandoned"
    """The run's claim went silent before it finished."""


ManifestStatus = RunStatus | LifecycleStatus
"""A manifest's status: a [`LifecycleStatus`][] while it runs or after it was abandoned, else the terminal [`RunStatus`][]."""


class MeshStats(BaseModel):
    """Per-run mesh size/quality summary, recorded in the manifest so a sweep can be audited for mesh consistency."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    element_count: int
    """Total number of volume elements in the solved mesh."""

    min_element_size: float
    """`h_min` `[m]`: Smallest element edge length."""

    max_element_size: float
    """`h_max` `[m]`: Largest element edge length."""


class Result(BaseModel):
    """
    The typed outcome of one solve.

    Quantities stay pint-typed; they are stripped to floats only at the analysis boundary (``Study.to_dataframe``).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True, extra="forbid")

    status: RunStatus
    """Terminal state of the solve."""

    run_id: str | None = None
    """The run's identity (== the manifest's ``run_id`` and the bundle directory name — the memorable name derived
    from the cache key); the driver stamps it after parsing, so a bare parse (no run lifecycle) leaves it None."""

    run_dir: Path | None = Field(default=None, exclude=True)
    """The bundle directory this result lives in, stamped by the driver and ``load_result``; never serialized, and None once retention removed the directory."""

    force: Force | None = None
    """`F⃗` `[N]`: Net force on the moving array, as a vector pint quantity."""

    torque: Torque | None = None
    """`M⃗` `[N·m]`: Net torque on the moving array, as a vector pint quantity."""

    flux_density_max: FluxDensity | None = None
    """`|B|_max` `[T]`: Peak flux density magnitude in the domain."""

    temperature_max: Temperature | None = None
    """`T_max` `[K]`: Peak temperature in the domain, from the thermal or coupled joule-heating solve — the max-current search observable."""

    residual: float | None = None
    """`r` `[-]`: How far the last nonlinear solver stopped from converging: its final relative change, or its last linear residual when its loop took one step."""

    wall_time: float | None = None
    """`t_wall` `[s]`: Solver wall-clock time."""

    elmer_version: str | None = None
    """ElmerSolver version parsed from the log, if present."""

    frame: FrameUnion
    """The run's solve frame. Every vector stays in solve coordinates; the typed force/torque fields are the frame's
    whole-model totals ([`Frame.total`][femforge.frames.Frame.total]), while ``raw`` keeps Elmer's columns as printed."""

    component_forces: dict[str, Force] = Field(default_factory=dict)
    """`F⃗` `[N]`: Lumped magnetic force on each force-carrying sif Component, keyed by its ``components.yml`` name."""

    component_torques: dict[str, Torque] = Field(default_factory=dict)
    """`M⃗` `[N·m]`: Lumped magnetic torque (about the authored origin/axis) on each sif Component, keyed by its ``components.yml`` name."""

    raw: dict[str, float] = Field(default_factory=dict)
    """`[?]`: Escape hatch for un-modelled scalars the parser found but Result doesn't type yet, in whatever unit Elmer printed them."""

    @classmethod
    def load(cls, path: str | Path) -> Result:
        """Load a bundle's ``result.json`` (pass the bundle directory or the file itself), stamping ``run_dir`` with where it was read from."""
        file: Path = Path(path)
        if file.is_dir():
            file = file / BundleFile.RESULT
        if not file.is_file():
            raise FileNotFoundError(f"no {BundleFile.RESULT} at {file}")

        result: Result = cls.model_validate_json(file.read_text(encoding="utf-8"))
        result.run_dir = file.parent
        return result

    def axial_force(self, body: str, axis: Axis | None = None) -> Quantity:
        """`F_ax` `[N]`: Component ``body``'s force along the authored ``axis``, the frame's own axis for None ([`Frame.along`][femforge.frames.Frame.along])."""
        force: Vec3 = self.component_forces[body].m_as("newton")

        return quantity(self.frame.along(force, axis), "newton")
