"""The typed outcome of a solve: Elmer outputs parsed into validated, pint-typed fields."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from femforge.common.files import BundleFile
from femforge.common.frame import Axis, Axisymmetric, Frame, Solid
from femforge.common.vector import Vec3
from femforge.physical.units import FluxDensity, Force, Quantity, Temperature, Torque, quantity


RunStatus = Literal["ok", "diverged", "error"]
"""The terminal state of a solve: completed cleanly, failed to converge, or errored out."""


ManifestStatus = Literal["running", "abandoned"] | RunStatus
"""A manifest's lifecycle status: ``"running"`` while the pipeline executes, then the terminal [`RunStatus`][], or ``"abandoned"`` when its claim went silent first."""


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
    """`Δ_rel` `[-]`: Final linear/nonlinear relative change reported by the solver."""

    wall_time: float | None = None
    """`t_wall` `[s]`: Solver wall-clock time."""

    elmer_version: str | None = None
    """ElmerSolver version parsed from the log, if present."""

    frame: Frame
    """The run's solve frame. Every vector stays in solve coordinates; under ``Axisymmetric`` the typed force/torque fields
    are full-revolution totals with the radial component zeroed (zero net by symmetry), while ``raw`` keeps Elmer's per-radian columns."""

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
        """`F_ax` `[N]`: Component ``body``'s force along the authored ``axis``; an axisymmetric run defaults to its symmetry axis."""
        force: Vec3 = self.component_forces[body].m_as("newton")

        match self.frame:
            case Axisymmetric():
                if axis is None:
                    return quantity(force.y, "newton")

                cosine: float | None = self.frame.axis.along(axis.direction)

                if cosine is None:
                    raise ValueError(f"An axisymmetric run has force only along its symmetry axis {self.frame.axis.direction}, not along {axis.direction}.")

                return quantity(force.y * cosine, "newton")
            case Solid():
                if axis is None:
                    raise ValueError("A solid run has no symmetry axis; pass the axis to project onto.")

                return quantity(force.dot(axis.direction), "newton")
