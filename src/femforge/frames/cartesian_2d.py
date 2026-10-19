"""The Cartesian 2D frame: the planar section of a prismatic model, solved per metre of depth and scaled by its thickness."""

from __future__ import annotations

import operator
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, ValidationInfo, field_validator

from femforge.common.vector import Direction, DirectionField, Vec3
from femforge.elmer.physics import Physics
from femforge.frames.base import ALIGNMENT_TOLERANCE, Axis, Frame, GeometryCheck, SolvePlane, in_plane, least_aligned_world_axis
from femforge.physical.units import Length


if TYPE_CHECKING:
    from femforge.elmer.config.common.simulation import CoordinateSystem
    from femforge.geometry.tags import TaggedCompound


class PlaneState(StrEnum):
    """How a 2D elasticity solve treats the out-of-plane direction."""

    STRESS = "plane_stress"
    """No out-of-plane stress: a thin plate loaded in its plane."""

    STRAIN = "plane_strain"
    """No out-of-plane strain: a long prism held between rigid ends."""


class Cartesian2D(Frame):
    """Solve the section of a prismatic model on the plane normal to ``normal``: X = ``x_dir``, Y = normal × X, Z = 0, scaled by ``thickness``."""

    kind: Literal["cartesian_2d"] = "cartesian_2d"
    """Discriminator that names this frame in a serialized config."""

    normal: Axis = Axis.Z
    """The section plane: through the axis origin, normal to its direction."""

    # Left out of dumps when unset, so an unset x_dir adds nothing to the run identity.
    x_dir: DirectionField | None = Field(default=None, exclude_if=partial(operator.is_, None))
    """`[-]`: The authored direction of the solve +X, stored as its unit part in the plane; None picks the world axis least aligned with the normal."""

    thickness: Length
    """`t` `[mm]`: The depth of the prism along the normal, which turns per-metre results into whole-model totals."""

    state: PlaneState = PlaneState.STRESS
    """How a linear elasticity solve treats the out-of-plane direction."""

    @field_validator("x_dir")
    @classmethod
    def in_plane_x(cls, x_dir: Direction | None, info: ValidationInfo) -> Direction | None:
        """Reject an ``x_dir`` along the normal and store its unit part in the plane."""
        normal: object = info.data.get("normal")

        if x_dir is None or not isinstance(normal, Axis):
            return x_dir

        try:
            return in_plane(x_dir, normal.direction)
        except ValueError:
            raise ValueError(f"The x direction {x_dir} is parallel to the plane normal {normal.direction}.") from None

    @field_validator("thickness")
    @classmethod
    def positive(cls, thickness: Length) -> Length:
        """Reject a zero or negative thickness."""
        if thickness.m_as("mm") <= 0.0:
            raise ValueError(f"The thickness {thickness} must be positive.")

        return thickness

    @property
    def label(self) -> str:
        """A short human-readable name."""
        return f"Cartesian 2D normal to {self.normal.label}"

    @property
    def model_dim(self) -> Literal[2, 3]:
        """The mesh dimension this frame solves on."""
        return 2

    @property
    def coordinate_system(self) -> CoordinateSystem:
        """Elmer's Cartesian 2D system, which integrates per metre of depth."""
        from femforge.elmer.config.common.simulation import CoordinateSystem

        return CoordinateSystem.CARTESIAN_2D

    @property
    def plane(self) -> SolvePlane:
        """The plane through the normal's origin: +X toward ``x_dir``, else the world axis least aligned with the normal (X first), and +Y = normal × X."""
        normal: Direction = self.normal.direction
        seed: Vec3 = self.x_dir if self.x_dir is not None else least_aligned_world_axis(normal)
        across: Vec3 = seed - normal * seed.dot(normal)
        x_dir = Direction(across.x, across.y, across.z)
        y_dir: Vec3 = normal.cross(x_dir)

        return SolvePlane(origin=self.normal.origin, x_dir=x_dir, y_dir=Direction(y_dir.x, y_dir.y, y_dir.z))

    @property
    def lumps_torque(self) -> bool:
        """False: the frame lumps no torque."""
        return False

    @property
    def plane_stress(self) -> bool:
        """Whether a 2D elasticity solve is plane stress."""
        return self.state is PlaneState.STRESS

    @property
    def thickness_m(self) -> float:
        """`t` `[m]`: The thickness in metres, the factor between a per-metre result and its whole-model total."""
        return self.thickness.m_as("m")

    def measure_geometry(self, compound: TaggedCompound) -> GeometryCheck:
        """How far every body and named face of ``compound`` is from a prism through ``thickness`` along the normal."""
        from femforge.geometry.conformance import MISMATCH_TOLERANCE, prism_mismatches

        return GeometryCheck(frame=self.label, tolerance=MISMATCH_TOLERANCE, measured=tuple(prism_mismatches(compound, self.plane, self.thickness)))

    def prepare_geometry(self, step_in: Path, step_out: Path) -> Path:
        """``step_out``, written with the section of ``step_in`` on ``plane`` once the model passes the geometry check, which is recorded beside it."""
        from femforge.common.log import log
        from femforge.geometry.section import export_section, planar_cross_section
        from femforge.geometry.step import import_tagged_step

        compound = import_tagged_step(step_in)
        check: GeometryCheck = self.measure_geometry(compound)
        check.raise_for_mismatch()

        section = planar_cross_section(compound, self.plane)
        export_section(section, step_out)
        check.write(step_out)
        log.bind(step=str(step_out), normal=self.normal.model_dump(mode="json"), thickness=self.thickness_m).info("Planar section written.")

        return step_out

    def solves(self, physics: Physics) -> Physics:
        """``MAGNETOSTATICS_2D`` for magnetostatics, and heat conduction and linear elasticity as themselves; every other physics raises."""
        match physics:
            case Physics.MAGNETOSTATICS | Physics.MAGNETOSTATICS_2D:
                return Physics.MAGNETOSTATICS_2D
            case Physics.THERMAL | Physics.LINEAR_ELASTICITY:
                return physics
            case _:
                raise ValueError(f"Physics '{physics.value}' has no Cartesian 2D form; solve it with frame=Solid().")

    def solve_vector(self, authored: Vec3) -> tuple[float, ...]:
        """``(v·x, v·y)`` on the plane basis; a vector with a part along the normal raises."""
        plane: SolvePlane = self.plane

        if abs(authored.dot(plane.normal)) > ALIGNMENT_TOLERANCE * authored.magnitude():
            raise ValueError(f"The vector {authored} has a part along the plane normal {self.normal.label}, so it has no Cartesian 2D form.")

        return (authored.dot(plane.x_dir), authored.dot(plane.y_dir))

    def total(self, integrated: Vec3) -> Vec3:
        """The whole prism's total of a per-metre integral: its in-plane components times the thickness."""
        return Vec3(integrated.x * self.thickness_m, integrated.y * self.thickness_m, 0.0)

    def solve_share(self, share_of: Vec3) -> Vec3:
        """The per-metre share of a whole-prism solve-frame vector: the vector over the thickness."""
        return share_of / self.thickness_m

    def along(self, vector: Vec3, axis: Axis | None) -> float:
        """The projection of the solve-frame ``vector`` onto the authored ``axis``; None raises, since the frame has no axis of its own."""
        if axis is None:
            raise ValueError("A Cartesian 2D run has no axis of its own; pass the axis to project onto.")

        plane: SolvePlane = self.plane

        return vector.x * axis.direction.dot(plane.x_dir) + vector.y * axis.direction.dot(plane.y_dir)
