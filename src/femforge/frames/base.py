"""The solve frame seam: the ``Frame`` base class every frame implements, and the axes and planes frames are built from."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from femforge.common.config import ConfigArea
from femforge.common.files import write_atomic
from femforge.common.vector import DIRECTION_SLACK, Direction, DirectionField, Vec3, Vec3Field
from femforge.elmer.physics import Physics
from femforge.physical.readout import along


if TYPE_CHECKING:
    from femforge.elmer.config.common.simulation import CoordinateSystem
    from femforge.geometry.tags import TaggedCompound


ALIGNMENT_TOLERANCE = 1e-6
"""`[-]`: The largest transverse share of a unit direction still counted as parallel to an axis."""

CHECK_SUFFIX = ".check.json"
"""The suffix that turns a solve STEP's stem into its geometry check file's name, ``geometry.check.json`` beside ``geometry.step``."""

AUTHORED_SUFFIX = ".authored.vtp"
"""The suffix that turns a solve STEP's stem into the name of its authored-body tessellation, ``geometry.authored.vtp`` beside ``geometry.step``."""


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
    """`[mm]`: The authored point at the plane's origin."""

    x_dir: DirectionField
    """`[-]`: The authored direction of the plane's +X, the radial direction at θ = 0 of an axisymmetric section."""

    y_dir: DirectionField
    """`[-]`: The authored direction of the plane's +Y, the symmetry axis of an axisymmetric section."""

    @property
    def normal(self) -> Direction:
        """The authored direction of the solve +Z, ``x_dir × y_dir``."""
        normal: Vec3 = self.x_dir.cross(self.y_dir)

        return Direction(normal.x, normal.y, normal.z)


def axis_word(direction: Vec3) -> str:
    """``+X`` or ``-Y`` for a signed world axis, else the component form of ``direction``, such as ``(0.6, 0.8, 0)``."""
    components: list[float] = direction.normalized().to_list()
    coordinate: int | None = next((index for index, component in enumerate(components) if abs(abs(component) - 1.0) <= ALIGNMENT_TOLERANCE), None)

    if coordinate is None:
        return along(direction)

    sign: str = "-" if components[coordinate] < 0 else "+"

    return f"{sign}{'XYZ'[coordinate]}"


class FrameFact(BaseModel):
    """One row of the Setup tab's Frame section: a label and the value it reads."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    label: str
    """The row's label, such as ``Plane Origin``."""

    value: str
    """The row's value, such as ``(0, 0, 5) mm``."""


def least_aligned_world_axis(direction: Vec3) -> Vec3:
    """The world unit axis least aligned with ``direction``, X first on a tie."""
    world: tuple[Vec3, ...] = (Vec3(1, 0, 0), Vec3(0, 1, 0), Vec3(0, 0, 1))

    return min(world, key=lambda unit: abs(unit.dot(direction)))


def in_plane(direction: Direction, normal: Direction) -> Direction:
    """The unit part of ``direction`` perpendicular to ``normal``; raises ``ValueError`` when the two are parallel."""
    cosine: float = direction.dot(normal)

    # A stored in-plane direction is already perpendicular; projecting it again would shift its last digit on every reload.
    if abs(cosine) <= DIRECTION_SLACK:
        return direction

    across: Vec3 = direction - normal * cosine

    if across.magnitude() <= ALIGNMENT_TOLERANCE:
        raise ValueError(f"The direction {direction} is parallel to {normal}, so it has no part in the plane normal to it.")

    return Direction(across.x, across.y, across.z)


class FrameCheck(StrEnum):
    """A rule a sectioning frame holds every body and named face of the authored model to."""

    TURNED = "turned"
    """The leaf is unchanged when turned about the symmetry axis."""

    PRISM = "prism"
    """The leaf is its mid-depth section carried through the model's depth along the normal."""

    DEPTH = "depth"
    """The body spans the frame's thickness along the normal."""

    @property
    def failure(self) -> str:
        """What a leaf that fails the check does, worded to follow the leaf's name."""
        from femforge.geometry.conformance import TURN_DEGREES

        match self:
            case FrameCheck.TURNED:
                return f"changes when turned {TURN_DEGREES:g}° about the axis"
            case FrameCheck.PRISM:
                return "is not a prism through the model's depth along the normal"
            case FrameCheck.DEPTH:
                return "spans a depth along the normal other than the thickness"

    @property
    def remedy(self) -> str:
        """How to author the model so that it passes the check."""
        match self:
            case FrameCheck.TURNED:
                return "Author every body, the air included, and every named face as a solid or surface of revolution about the axis: bound the model with boundary_cylinder, not boundary_box."
            case FrameCheck.PRISM:
                return "Author every body as a prism through the model's depth, and name only faces that run through it, never its end faces."
            case FrameCheck.DEPTH:
                return "Set thickness to the model's depth along the normal."


class BodyMismatch(BaseModel):
    """How far one body or named face of the authored model is from passing one frame check."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    body: str
    """The leaf's tags, such as ``Material=Air``."""

    check: FrameCheck
    """The check the leaf was measured against."""

    fraction: float
    """`[-]`: The mismatch, a share of the leaf's volume, area or depth."""


class GeometryCheck(BaseModel):
    """A frame's verdict on an authored model: every measured body and named face, and the tolerance each was held to."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    frame: str
    """The label of the frame that checked the model."""

    tolerance: float
    """`[-]`: The largest mismatch fraction that still passes."""

    measured: tuple[BodyMismatch, ...]
    """Every measurement, passing ones included."""

    @property
    def mismatches(self) -> tuple[BodyMismatch, ...]:
        """The measurements above ``tolerance``."""
        return tuple(measurement for measurement in self.measured if measurement.fraction > self.tolerance)

    @property
    def passed(self) -> bool:
        """Whether every measurement is within ``tolerance``."""
        return not self.mismatches

    def raise_for_mismatch(self) -> None:
        """Raise [`FrameGeometryError`][femforge.frames.FrameGeometryError] when a measurement is above ``tolerance``."""
        if not self.passed:
            raise FrameGeometryError(self)

    def write(self, step: Path) -> None:
        """Write the check beside the solve STEP ``step``."""
        write_atomic(check_path(step), self.model_dump_json())

    @classmethod
    def load(cls, step: Path) -> GeometryCheck | None:
        """The check recorded beside ``step``; None when it has no check file."""
        path: Path = check_path(step)

        if not path.is_file():
            return None

        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def check_path(step: Path) -> Path:
    """The geometry check file that belongs to the solve STEP ``step``."""
    return step.with_name(f"{step.stem}{CHECK_SUFFIX}")


def authored_path(step: Path) -> Path:
    """The authored-body tessellation that belongs to the solve STEP ``step``."""
    return step.with_name(f"{step.stem}{AUTHORED_SUFFIX}")


class FrameGeometryError(ValueError):
    """The authored model does not fit the frame: a body or named face mismatches a frame check beyond its tolerance."""

    check: GeometryCheck
    """The whole verdict, passing measurements included."""

    mismatches: tuple[BodyMismatch, ...]
    """The measurements above the tolerance."""

    def __init__(self, check: GeometryCheck) -> None:
        """List every failing leaf of ``check`` with its mismatch, then each remedy once."""
        self.check = check
        self.mismatches = check.mismatches
        failing: set[FrameCheck] = {mismatch.check for mismatch in self.mismatches}
        lines: list[str] = [f"  - {mismatch.body}: {mismatch.check.failure}, mismatch {mismatch.fraction:.2e}" for mismatch in self.mismatches]
        remedies: list[str] = [member.remedy for member in FrameCheck if member in failing]

        super().__init__("\n".join([f"The model does not fit the frame {check.frame}:", *lines, *remedies]))

    def __reduce__(self) -> tuple[type[FrameGeometryError], tuple[GeometryCheck]]:
        """Rebuild from the check, so the error survives a trip between processes."""
        return (FrameGeometryError, (self.check,))


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

    @property
    def revolves(self) -> bool:
        """Whether the solve section stands for a model revolved about an axis."""
        return False

    @property
    def plane_stress(self) -> bool | None:
        """Whether a 2D elasticity solve is plane stress (True) or plane strain (False); None when the frame poses no plane state."""
        return None

    @abstractmethod
    def measure_geometry(self, compound: TaggedCompound) -> GeometryCheck | None:
        """How far every body and named face of the authored ``compound`` is from fitting this frame; None when the frame takes any model."""

    def check_geometry(self, compound: TaggedCompound) -> None:
        """Raise ``FrameGeometryError`` when a body or named face of the authored ``compound`` does not fit this frame."""
        check: GeometryCheck | None = self.measure_geometry(compound)

        if check is not None:
            check.raise_for_mismatch()

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
    def solve_share(self, share_of: Vec3) -> Vec3:
        """The share the solve domain carries of a whole-model solve-frame vector, the inverse of [`total`][femforge.frames.Frame.total]."""

    def authored_share(self, authored: Vec3) -> Vec3:
        """The solve-frame share the solve domain carries of an authored whole-model vector; raises ``ValueError`` when it has no form here."""
        components: list[float] = [*self.solve_vector(authored), 0.0, 0.0]

        return self.solve_share(Vec3(components[0], components[1], components[2]))

    @abstractmethod
    def along(self, vector: Vec3, axis: Axis | None) -> float:
        """The component of a solve-frame vector along an authored ``axis``, or along the frame's own axis for None."""

    def facts(self) -> tuple[FrameFact, ...]:
        """Where the solve plane sits and how its axes run in the authored model, then the plane state when the frame poses one; none for a frame that solves the model whole."""
        plane: SolvePlane | None = self.plane

        if plane is None:
            return ()

        revolves: bool = self.revolves
        origin = FrameFact(label="Axis Origin" if revolves else "Plane Origin", value=f"{plane.origin:.3g} mm")
        axes = FrameFact(label="Radial, Axial" if revolves else "Solve X, Y", value=f"{axis_word(plane.x_dir)}, {axis_word(plane.y_dir)}")
        state: bool | None = self.plane_stress

        if state is None:
            return (origin, axes)

        return (origin, axes, FrameFact(label="Plane State", value="Plane Stress" if state else "Plane Strain"))

    def face_measure(self, length: float, centroid: Vec3) -> FrameFact | None:
        """What a boundary group meshed as a line of ``length`` metres at ``centroid`` measures in the whole model; None for a frame that meshes faces as faces.

        Args:
            length: `L` `[m]`: The group's length in the solve plane.
            centroid: `[m]`: The group's centroid in the solve frame.
        """
        return None
