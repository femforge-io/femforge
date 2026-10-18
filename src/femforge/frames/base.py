"""The solve frame seam: the ``Frame`` base class every frame implements, and the axes and planes frames are built from."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import ConfigDict, Field

from femforge.common.config import ConfigArea
from femforge.common.vector import Direction, DirectionField, Vec3, Vec3Field
from femforge.elmer.physics import Physics


if TYPE_CHECKING:
    from femforge.elmer.config.common.simulation import CoordinateSystem


ALIGNMENT_TOLERANCE = 1e-6
"""`[-]`: The largest transverse share of a unit direction still counted as parallel to an axis."""


class Axis(ConfigArea):
    """A line through ``origin`` along ``direction``, in the authored model's frame."""

    model_config = ConfigDict(frozen=True)

    origin: Vec3Field = Field(default_factory=lambda: Vec3(0, 0, 0))
    """`[mm]`: A point on the axis."""

    direction: DirectionField
    """`[-]`: The axis direction; a scaled direction is the same axis."""

    X: ClassVar[Axis]
    Y: ClassVar[Axis]
    Z: ClassVar[Axis]

    @property
    def label(self) -> str:
        """``Z`` or ``-X`` for a coordinate axis through the origin, else its direction and origin."""
        components: list[float] = self.direction.to_list()
        coordinate: int | None = next((index for index, component in enumerate(components) if abs(component) == 1.0), None)

        if coordinate is None or self.origin.magnitude() != 0:
            return f"{self.direction:.3g} through {self.origin:.3g}"

        sign: str = "-" if components[coordinate] < 0 else ""

        return f"{sign}{'XYZ'[coordinate]}"

    def along(self, direction: Vec3) -> float | None:
        """The cosine between ``direction`` and this axis (±1), or None when they are not parallel within ``ALIGNMENT_TOLERANCE``."""
        unit: Vec3 = direction.normalized()
        cosine: float = unit.dot(self.direction)
        transverse: float = (unit - self.direction * cosine).magnitude()

        if transverse > ALIGNMENT_TOLERANCE:
            return None

        return cosine


Axis.X = Axis(direction=Direction(1, 0, 0))
Axis.Y = Axis(direction=Direction(0, 1, 0))
Axis.Z = Axis(direction=Direction(0, 0, 1))


class SolvePlane(ConfigArea):
    """Where a sectioned solve's plane sits in the authored model: its origin and the authored directions of its +X and +Y."""

    model_config = ConfigDict(frozen=True)

    origin: Vec3Field
    """`[mm]`: The authored point at the plane's origin, on the axis."""

    radial: DirectionField
    """`[-]`: The authored direction of the plane's +X, the radial direction at θ = 0."""

    axis: DirectionField
    """`[-]`: The authored direction of the plane's +Y, along the axis."""


class Frame(ConfigArea, ABC):
    """How an authored model maps onto the coordinates the solve runs in; each subclass is one frame."""

    model_config = ConfigDict(frozen=True)

    kind: str
    """Discriminator that names this frame in a serialized config."""

    @property
    @abstractmethod
    def label(self) -> str:
        """A short human-readable name."""

    @property
    @abstractmethod
    def model_dim(self) -> Literal[2, 3]:
        """The mesh dimension this frame solves on."""

    @property
    @abstractmethod
    def coordinate_system(self) -> CoordinateSystem:
        """The Elmer coordinate system the frame solves in."""

    @property
    @abstractmethod
    def plane(self) -> SolvePlane | None:
        """Where the solve plane sits in the authored model; None when the model solves whole."""

    @property
    def rewrites_geometry(self) -> bool:
        """Whether the solve meshes a converted STEP rather than the authored one."""
        return self.plane is not None

    @property
    @abstractmethod
    def lumps_torque(self) -> bool:
        """Whether Elmer's force lumping yields a torque in this frame."""

    @abstractmethod
    def prepare_geometry(self, step_in: Path, step_out: Path) -> Path:
        """The STEP the solve meshes: ``step_in`` itself, or its conversion written to ``step_out``."""

    @abstractmethod
    def solves(self, physics: Physics) -> Physics:
        """What ``physics`` solves as in this frame; raises ``ValueError`` when it has no form here."""

    def supports(self, physics: Physics) -> bool:
        """Whether ``physics`` has a form in this frame."""
        try:
            _ = self.solves(physics)
        except ValueError:
            return False

        return True

    @abstractmethod
    def solve_vector(self, authored: Vec3) -> tuple[float, ...]:
        """The solve-coordinate components of an authored-frame vector; raises ``ValueError`` when it has no form here."""

    @abstractmethod
    def total(self, integrated: Vec3) -> Vec3:
        """The whole model's total of a vector Elmer integrated over the solve domain."""

    @abstractmethod
    def along(self, vector: Vec3, axis: Axis | None) -> float:
        """The component of a solve-frame vector along an authored ``axis``, or along the frame's own axis for None."""
