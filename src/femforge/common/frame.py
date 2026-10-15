"""The solve frame: how an authored model maps onto the frame Elmer solves in."""

from __future__ import annotations

import operator
from functools import partial
from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field, ValidationInfo, field_validator

from femforge.common.config import ConfigArea
from femforge.common.vector import DIRECTION_SLACK, Direction, DirectionField, Vec3, Vec3Field


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


class Solid(ConfigArea):
    """Solve the authored 3D model as-is."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["solid"] = "solid"
    """Discriminator that names this frame in a serialized config."""

    @property
    def label(self) -> str:
        """A short human-readable name."""
        return "solid"

    @property
    def model_dim(self) -> Literal[2, 3]:
        """The mesh dimension this frame solves on."""
        return 3


class Axisymmetric(ConfigArea):
    """Solve the r–z half-plane of a model symmetric about ``axis``: X = radial, Y = along the axis, Z = 0."""

    model_config = ConfigDict(frozen=True)

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

        cosine: float = radial.dot(axis.direction)

        # A stored radial is already perpendicular; projecting it again would shift its last digit on every reload.
        if abs(cosine) <= DIRECTION_SLACK:
            return radial

        across: Vec3 = radial - axis.direction * cosine

        if across.magnitude() <= ALIGNMENT_TOLERANCE:
            raise ValueError(f"The radial direction {radial} is parallel to the symmetry axis {axis.direction}.")

        return Direction(across.x, across.y, across.z)

    @property
    def label(self) -> str:
        """A short human-readable name."""
        return f"axisymmetric about {self.axis.label}"

    @property
    def model_dim(self) -> Literal[2, 3]:
        """The mesh dimension this frame solves on."""
        return 2


Frame = Annotated[Solid | Axisymmetric, Field(discriminator="kind")]
