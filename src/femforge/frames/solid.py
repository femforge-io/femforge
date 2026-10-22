"""The solid frame: the authored 3D model solved as-is."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from femforge.elmer.physics import Physics
from femforge.frames.base import Axis, Frame


if TYPE_CHECKING:
    from femforge.common.vector import Vec3
    from femforge.elmer.config.common.simulation import CoordinateSystem
    from femforge.frames.base import GeometryCheck
    from femforge.geometry.tags import TaggedCompound


class Solid(Frame):
    """Solve the authored 3D model as-is."""

    kind: Literal["solid"] = "solid"
    """Discriminator that names this frame in a serialized config."""

    @property
    def label(self) -> str:
        """A short human-readable name."""
        return "solid"

    @property
    def description(self) -> str:
        """One sentence on what the frame solves, for a reader who has not met it."""
        return "The authored 3D model, solved whole."

    @property
    def model_dim(self) -> Literal[2, 3]:
        """The mesh dimension this frame solves on."""
        return 3

    @property
    def coordinate_system(self) -> CoordinateSystem:
        """Cartesian coordinates."""
        from femforge.elmer.config.common.simulation import CoordinateSystem

        return CoordinateSystem.CARTESIAN

    @property
    def plane(self) -> None:
        """None: the model solves whole."""
        return None

    @property
    def lumps_torque(self) -> bool:
        """True: Elmer lumps a torque about any authored axis."""
        return True

    def measure_geometry(self, compound: TaggedCompound) -> GeometryCheck | None:
        """None: the solve meshes the model as authored."""
        return None

    def prepare_geometry(self, step_in: Path, step_out: Path) -> Path:
        """``step_in`` itself: nothing is converted."""
        return step_in

    def solves(self, physics: Physics) -> Physics:
        """``physics`` itself, except the 2D magnetostatic stack, which raises."""
        if physics is Physics.MAGNETOSTATICS_2D:
            raise ValueError(f"Physics '{physics.value}' is the 2D magnetostatic stack: select '{Physics.MAGNETOSTATICS.value}' with a 2D frame (Axisymmetric() or Cartesian2D()).")

        return physics

    def solve_vector(self, authored: Vec3) -> tuple[float, ...]:
        """The authored components unchanged."""
        return authored.to_tuple()

    def total(self, integrated: Vec3) -> Vec3:
        """``integrated`` itself: the solve domain is the whole model."""
        return integrated

    def solve_share(self, share_of: Vec3) -> Vec3:
        """``share_of`` itself: the solve domain is the whole model."""
        return share_of

    def along(self, vector: Vec3, axis: Axis | None) -> float:
        """The projection of ``vector`` onto ``axis``; None raises, since a solid has no axis of its own."""
        if axis is None:
            raise ValueError("A solid run has no symmetry axis; pass the axis to project onto.")

        return vector.dot(axis.direction)
