"""The axisymmetric frame: the r–z half-plane of a model symmetric about an axis."""

from __future__ import annotations

import math
import operator
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, ValidationInfo, field_validator

from femforge.common.vector import Direction, DirectionField, Vec3
from femforge.elmer.physics import Physics
from femforge.frames.base import ALIGNMENT_TOLERANCE, Axis, Frame, FrameFact, GeometryCheck, SolvePlane, authored_path, in_plane, least_aligned_world_axis
from femforge.physical.readout import readout
from femforge.physical.units import quantity


if TYPE_CHECKING:
    from femforge.elmer.config.common.simulation import CoordinateSystem
    from femforge.geometry.tags import TaggedCompound


class Axisymmetric(Frame):
    """Solve the r–z half-plane of a model symmetric about ``axis``: X = radial, Y = along the axis, Z = 0."""

    kind: Literal["axisymmetric"] = "axisymmetric"
    """Discriminator that names this frame in a serialized config."""

    axis: Axis = Axis.Z
    """The authored model's axis of symmetry."""

    # Left out of dumps when unset, so an unset radial adds nothing to the run identity.
    radial: DirectionField | None = Field(default=None, exclude_if=partial(operator.is_, None))
    """`[-]`: The direction from the axis toward the sectioned half-plane, stored as its unit part perpendicular to the
    axis; None picks the world axis least aligned with the symmetry axis."""

    @field_validator("radial")
    @classmethod
    def perpendicular(cls, radial: Direction | None, info: ValidationInfo) -> Direction | None:
        """Reject an axis-parallel radial and store its unit component perpendicular to the axis."""
        axis: object = info.data.get("axis")

        if radial is None or not isinstance(axis, Axis):
            return radial

        try:
            return in_plane(radial, axis.direction)
        except ValueError:
            raise ValueError(f"The radial direction {radial} is parallel to the symmetry axis {axis.direction}.") from None

    @property
    def label(self) -> str:
        """A short human-readable name."""
        return f"axisymmetric about {self.axis.label}"

    @property
    def description(self) -> str:
        """One sentence on what the frame solves, for a reader who has not met it."""
        return "A half section through the axis, solved as the solid it sweeps when revolved."

    @property
    def model_dim(self) -> Literal[2, 3]:
        """The mesh dimension this frame solves on."""
        return 2

    @property
    def coordinate_system(self) -> CoordinateSystem:
        """Elmer's Axi Symmetric system, which integrates per radian."""
        from femforge.elmer.config.common.simulation import CoordinateSystem

        return CoordinateSystem.AXI_SYMMETRIC

    @property
    def plane(self) -> SolvePlane:
        """The half-plane through ``axis`` toward ``radial``, else toward the world axis least aligned with ``axis`` (X first)."""
        direction: Direction = self.axis.direction
        seed: Vec3 = self.radial if self.radial is not None else least_aligned_world_axis(direction)
        across: Vec3 = seed - direction * seed.dot(direction)

        return SolvePlane(origin=self.axis.origin, x_dir=Direction(across.x, across.y, across.z), y_dir=direction)

    @property
    def lumps_torque(self) -> bool:
        """False: a torque has no axisymmetric form."""
        return False

    @property
    def revolves(self) -> bool:
        """True: the section stands for the model revolved about ``axis``."""
        return True

    def face_measure(self, length: float, centroid: Vec3) -> FrameFact | None:
        """The surface a section line of ``length`` metres sweeps about the axis, ``2π·r̄·L`` with ``r̄`` the radius of ``centroid``; None for a line on the axis, which sweeps none.

        Args:
            length: `L` `[m]`: The group's length in the solve plane.
            centroid: `[m]`: The group's centroid in the solve frame, X radial.
        """
        if abs(centroid.x) <= ALIGNMENT_TOLERANCE * length:
            return None

        area = quantity(math.tau * centroid.x * length, "m**2")

        return FrameFact(label="Revolved Area", value=f"2π·r̄·L = {readout(area)}")

    def measure_geometry(self, compound: TaggedCompound) -> GeometryCheck:
        """How far every body and named face of ``compound`` changes when turned about ``axis``."""
        from femforge.geometry.conformance import MISMATCH_TOLERANCE, revolution_mismatches

        return GeometryCheck(frame=self.label, tolerance=MISMATCH_TOLERANCE, measured=tuple(revolution_mismatches(compound, self.axis)))

    def prepare_geometry(self, step_in: Path, step_out: Path) -> Path:
        """``step_out``, written with the section of ``step_in`` on ``plane`` once the model passes the geometry check; the check and the authored-body tessellation land beside it."""
        from femforge.common.log import log
        from femforge.geometry.authored import write_authored_geometry
        from femforge.geometry.section import axisymmetric_cross_section, export_section
        from femforge.geometry.step import import_tagged_step

        compound = import_tagged_step(step_in)
        _ = write_authored_geometry(compound, self.plane, authored_path(step_out))
        check: GeometryCheck = self.measure_geometry(compound)
        check.raise_for_mismatch()

        section = axisymmetric_cross_section(compound, self.plane)
        export_section(section, step_out)
        check.write(step_out)
        log.bind(step=str(step_out), axis=self.axis.model_dump(mode="json")).info("Axisymmetric section written.")

        return step_out

    def solves(self, physics: Physics) -> Physics:
        """``MAGNETOSTATICS_2D`` for magnetostatics, ``THERMAL`` for heat conduction; every other physics raises."""
        match physics:
            case Physics.MAGNETOSTATICS | Physics.MAGNETOSTATICS_2D:
                return Physics.MAGNETOSTATICS_2D
            case Physics.THERMAL:
                return Physics.THERMAL
            case _:
                raise ValueError(f"Physics '{physics.value}' has no axisymmetric form; solve it with frame=Solid().")

    def solve_vector(self, authored: Vec3) -> tuple[float, ...]:
        """``(0, a)`` with ``a`` the signed length along the axis; a vector off the axis raises."""
        cosine: float | None = self.axis.along(authored)

        if cosine is None:
            raise ValueError(f"The vector {authored} is not along the symmetry axis {self.axis.label}, so it has no axisymmetric form.")

        return (0.0, cosine * authored.magnitude())

    def total(self, integrated: Vec3) -> Vec3:
        """The full revolution of a per-radian integral: the axial component times 2π, the radial one zero by symmetry."""
        return Vec3(0.0, math.tau * integrated.y, 0.0)

    def solve_share(self, share_of: Vec3) -> Vec3:
        """The per-radian share of a full-revolution solve-frame vector: its axial component over 2π, the radial one zero by symmetry."""
        return Vec3(0.0, share_of.y / math.tau, 0.0)

    def along(self, vector: Vec3, axis: Axis | None) -> float:
        """The axial component of ``vector`` along ``axis``, the symmetry axis for None; an axis off the symmetry axis raises."""
        if axis is None:
            return vector.y

        cosine: float | None = self.axis.along(axis.direction)

        if cosine is None:
            raise ValueError(f"An axisymmetric run has force only along its symmetry axis {self.axis.direction}, not along {axis.direction}.")

        return vector.y * cosine
