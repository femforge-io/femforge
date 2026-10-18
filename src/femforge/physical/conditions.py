"""Conditions attached to a region, each carrying its physics, its body or boundary target and its own Elmer sif emission."""
# Must not import meshing.config or elmer.sim: both depend on this module.

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, Union, cast, get_args

from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo

from femforge.common.vector import DirectionField, Vec3, Vec3Field
from femforge.elmer.physics import Physics
from femforge.elmer.sections import SifSection
from femforge.elmer.variables import ElmerVariable
from femforge.physical.units import (
    Current,
    CurrentDensity as CurrentDensityQuantity,
    Force,
    HeatFlux as HeatFluxQuantity,
    HeatTransferCoefficient,
    Length,
    PowerDensity,
    Quantity,
    Temperature,
    U,
    Unit,
    Voltage,
    quantity,
)


if TYPE_CHECKING:
    from femforge.frames import Frame


__all__ = [
    "ConditionTarget",
    "BoundarySite",
    "Condition",
    "Magnetization",
    "CurrentDensity",
    "FixedTemperature",
    "HeatFlux",
    "Convection",
    "HeatSource",
    "FixedPotential",
    "JouleHeat",
    "CoilDrive",
    "CoilTerminal",
    "CalculateMagneticForce",
    "FixedDisplacement",
    "SurfaceForce",
    "ConditionUnion",
    "shipped_conditions",
]


class ConditionTarget(StrEnum):
    """Whether a condition attaches to a 3D body or a 2D boundary."""

    BODY = "body"
    """Attach to a body."""

    BOUNDARY = "boundary"
    """Attach to a boundary surface."""


def _si(quantity: Quantity, unit: Any) -> float:  # pyright: ignore[reportExplicitAny, reportAny]  # unit is any pint unit expression (registry under-typed)
    """Strip ``quantity`` to a bare SI float in ``unit`` (Elmer sif keywords are plain numbers)."""
    return float(quantity.to(unit).magnitude)  # pyright: ignore[reportAny]  # unit arg is Any (registry under-typed)


@dataclass(frozen=True)
class ElmerKeyword:
    """``Annotated`` metadata tying a condition field to an Elmer sif keyword and the SI unit to strip to."""

    key: str
    unit: Unit


def _elmer_keyword(field: FieldInfo) -> ElmerKeyword | None:
    """The ``ElmerKeyword`` metadata on a field, or None (so ``to_elmer`` skips it)."""
    for meta in field.metadata:  # pyright: ignore[reportAny]  # FieldInfo.metadata is list[Any]
        if isinstance(meta, ElmerKeyword):
            return meta
    return None


@dataclass(frozen=True)
class BoundarySite:
    """The boundary group a boundary condition lands on, as the condition sees it when it emits."""

    name: str
    """The boundary group's name."""

    measure: float | None
    """`A` `[m²]`: The group's measured size, area in 3D and length times one metre of depth in 2D; None for an adjacency group."""

    frame: Frame
    """The solve frame the group's model maps onto."""


class Condition(BaseModel):
    """A source or constraint of one physics on the bodies or boundaries a tag selects; each subclass is one shipped condition."""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    external_only: ClassVar[bool] = False
    """Boundary-target conditions with this set land only on a body's *external* (domain-boundary) surface groups,
    not its interfaces — e.g. a coil terminal is the electrode's exposed end face, not its interior contacts."""

    section: ClassVar[SifSection]
    """The sif section this condition's keywords land in; ``BOUNDARY_CONDITION`` exactly when ``target`` is ``BOUNDARY``."""

    physics: Physics
    """The physics whose solve this condition feeds."""

    target: ConditionTarget
    """Whether the condition attaches to a body or a boundary."""

    def to_elmer(self, *args: Any, **kwargs: Any) -> dict[str, float | str]:  # pyright: ignore[reportExplicitAny, reportAny]  # variadic: subclass to_elmer signatures diverge (Magnetization takes a magnitude)
        """This condition's Elmer sif keyword dict, reflected from each field's ``ElmerKeyword`` metadata; an unset field emits nothing."""
        out: dict[str, float | str] = {}
        for name, field in type(self).model_fields.items():
            spec: ElmerKeyword | None = _elmer_keyword(field)

            if spec is None:
                continue

            value: Quantity | None = getattr(self, name)  # pyright: ignore[reportAny]  # getattr is Any

            if value is None:
                continue

            out[spec.key] = _si(value, spec.unit)
        return out

    def on_boundary(self, site: BoundarySite) -> dict[str, float | str]:
        """This condition's keywords on the boundary group ``site``; the default ignores the site."""
        return self.to_elmer()

    def check_frame(self, frame: Frame) -> None:
        """Raise when this condition cannot drive a ``frame`` solve."""


class Magnetization(Condition):
    """The magnetization direction of a permanent magnet.

    The magnitude ``|M| = Bᵣ/μ₀`` comes from the magnet's material.
    """

    kind: Literal["magnetization"] = "magnetization"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.MAGNETOSTATICS
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.MATERIAL

    direction: DirectionField | None
    """Magnetization direction in the authored model's frame, None for an unmagnetized body; magnitude comes from the material."""

    def to_elmer(self, magnitude: float, frame: Frame) -> dict[str, float | str]:
        """The ``Magnetization`` components of ``magnitude`` (A/m) along the direction, in ``frame``'s solve coordinates."""
        unit: Vec3 | None = self.direction

        if unit is None:
            raise ValueError("A Magnetization without a direction has no components to emit.")

        components: tuple[float, ...] = frame.solve_vector(unit)

        return {f"Magnetization {index}": f"{component * magnitude:.6g}" for index, component in enumerate(components, start=1)}

    def check_frame(self, frame: Frame) -> None:
        """Raise when ``frame`` has no form for the direction."""
        # A missing direction is left to Sim.validate, which reports it as a missing direction rather than a frame mismatch.
        if self.direction is None:
            return

        try:
            _ = frame.solve_vector(self.direction)
        except ValueError as error:
            raise ValueError(f"Magnetization direction {self.direction} has no form in the {frame.label} frame: {error}") from None


class CurrentDensity(Condition):
    """A uniform signed azimuthal current density J = N·I/A over a winding window, the homogenized drive of a stranded coil in an axisymmetric solve."""

    kind: Literal["current_density"] = "current_density"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.MAGNETOSTATICS_2D
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BODY_FORCE

    value: Annotated[CurrentDensityQuantity, ElmerKeyword("Current Density", U.A / U.m**2)]
    """`J` `[A/m²]`: Azimuthal current density (signed) imposed uniformly over the body."""

    def check_frame(self, frame: Frame) -> None:
        """Raise when ``frame`` has no azimuthal magnetostatic solve for this drive to feed."""
        if not frame.supports(Physics.MAGNETOSTATICS_2D):
            raise ValueError(f"CurrentDensity is the azimuthal coil drive of '{Physics.MAGNETOSTATICS_2D.value}', which the {frame.label} frame has no form for.")


class FixedTemperature(Condition):
    """A fixed temperature on a boundary."""

    kind: Literal["fixed_temperature"] = "fixed_temperature"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.THERMAL
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BOUNDARY_CONDITION

    value: Annotated[Temperature, ElmerKeyword(ElmerVariable.TEMPERATURE, U.K)]
    """`T` `[K]`: Fixed temperature imposed on the boundary."""


class HeatFlux(Condition):
    """A prescribed heat flux through a boundary."""

    kind: Literal["heat_flux"] = "heat_flux"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.THERMAL
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BOUNDARY_CONDITION

    value: Annotated[HeatFluxQuantity, ElmerKeyword("Heat Flux", U.W / U.m**2)]
    """`q_s` `[W/m²]`: Heat flux through the boundary."""


class Convection(Condition):
    """Newton cooling of an outer surface to an ambient temperature.

    It lands on the outer shell only, since a film coefficient on an interior interface would bypass the conduction path.
    """

    kind: Literal["convection"] = "convection"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.THERMAL
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BOUNDARY_CONDITION

    external_only: ClassVar[bool] = True

    coefficient: Annotated[HeatTransferCoefficient, ElmerKeyword("Heat Transfer Coefficient", U.W / (U.m**2 * U.K))]
    """`h` `[W/(m²·K)]`: Convective heat-transfer coefficient."""

    ext_temperature: Annotated[Temperature, ElmerKeyword("External Temperature", U.K)]
    """`T_amb` `[K]`: External (ambient) temperature the boundary exchanges with."""


class HeatSource(Condition):
    """A volumetric heat source in a body.

    With ``temperature_coefficient`` set, the source scales linearly with the local temperature, the σ(T) Joule feedback of a prescribed-current winding.
    """

    kind: Literal["heat_source"] = "heat_source"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.THERMAL
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BODY_FORCE

    power_density: Annotated[PowerDensity, ElmerKeyword("Volumetric Heat Source", U.W / U.m**3)]
    """`q` `[W/m³]`: Heat generation density at ``reference_temperature`` when a coefficient is set, else the constant source."""

    temperature_coefficient: float | None = None
    """`α` `[1/K]`: Linear coefficient in q(T) = power_density·(1 + α·(T − reference_temperature)); None emits the constant."""

    reference_temperature: Temperature | None = None
    """`T_ref` `[K]`: The temperature ``power_density`` holds at; required with ``temperature_coefficient``."""

    def to_elmer(self) -> dict[str, float | str]:
        """The ``Volumetric Heat Source`` keyword, a MATC expression in temperature when ``temperature_coefficient`` is set."""
        out: dict[str, float | str] = super().to_elmer()
        if self.temperature_coefficient is not None:
            if self.reference_temperature is None:
                raise ValueError("temperature_coefficient requires reference_temperature (the temperature power_density holds at).")
            source_at_reference: float = _si(self.power_density, U.W / U.m**3)
            reference_kelvin: float = _si(self.reference_temperature, U.K)
            out["Volumetric Heat Source"] = f'Variable Temperature\n    Real MATC "{source_at_reference:.9g}*(1.0+{self.temperature_coefficient:.9g}*(tx(0)-{reference_kelvin:.9g}))"'
        return out


class FixedPotential(Condition):
    """A fixed electric potential on an electrode, one end of a phase's terminal pair.

    The run also integrates the current through the electrode.
    """

    kind: Literal["fixed_potential"] = "fixed_potential"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.CURRENT_CONDUCTION
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BOUNDARY_CONDITION

    value: Annotated[Voltage, ElmerKeyword(ElmerVariable.POTENTIAL, U.V)]
    """`V` `[V]`: Electric potential imposed on the electrode boundary; the keyword is the solver variable's name (Elmer Dirichlet semantics), single-sourced via the enum."""

    def to_elmer(self) -> dict[str, float | str]:
        """The electrode potential plus the ``Save Scalars`` flag that makes SaveScalars integrate the electrode current."""
        out: dict[str, float | str] = super().to_elmer()
        # Mark the electrode for SaveScalars boundary integration (the per-phase current measurement).
        out["Save Scalars"] = "Logical True"
        return out


class JouleHeat(Condition):
    """Heats a body with the Joule losses of its solved current.

    It takes no value: HeatSolve integrates the loss density StatCurrentSolve solves for.
    """

    kind: Literal["joule_heat"] = "joule_heat"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.JOULE_HEATING
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BODY_FORCE

    def to_elmer(self) -> dict[str, float | str]:
        """The ``Joule Heat`` flag."""
        return {"Joule Heat": "Logical True"}


class CoilDrive(Condition):
    """A driven massive coil: the bodies that share one ``CoilDrive`` form one coil that carries ``current``.

    Reverse the drive by swapping the terminal roles, not with a negative current.
    """

    kind: Literal["coil_drive"] = "coil_drive"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.COIL_MAGNETOSTATICS
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.COMPONENT

    current: Annotated[Current, ElmerKeyword("Desired Coil Current", U.A)]
    """`I_coil` `[A]`: The total coil current CoilSolver normalizes this coil's solved distribution to."""

    def to_elmer(self) -> dict[str, float | str]:
        """The ``Desired Coil Current`` and the massive ``Coil Type``."""
        out: dict[str, float | str] = super().to_elmer()
        # Massive: one solid conductor whose current distribution is the solved conduction field (the PCB winding),
        # as opposed to "stranded" (a wound bundle with imposed uniform fill).
        out["Coil Type"] = '"massive"'
        return out


class CoilTerminal(Condition):
    """One end of an open coil, its start or end electrode.

    The electrode's end face must lie on the domain boundary (``GeometryConfig.terminate_electrodes``); positive coil current flows from start to end.
    """

    kind: Literal["coil_terminal"] = "coil_terminal"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.COIL_MAGNETOSTATICS
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BOUNDARY_CONDITION

    external_only: ClassVar[bool] = True

    role: Literal["start", "end"]
    """Which end of the coil this electrode is; CoilSolver's normalized current flows start -> end."""

    def to_elmer(self) -> dict[str, float | str]:
        """The ``Coil Start`` or ``Coil End`` flag for this ``role``."""
        return {"Coil Start" if self.role == "start" else "Coil End": "Logical True"}


class CalculateMagneticForce(Condition):
    """Lumps the magnetic force, and optionally the torque, over the bodies its tag matches into one Elmer ``Component``."""

    kind: Literal["calculate_magnetic_force"] = "calculate_magnetic_force"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.MAGNETOSTATICS
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.COMPONENT

    label: str | None = None
    """The Component's ``Name``; None uses the authoring tag's name."""

    calculate_magnetic_force: bool = True
    """Lump the nodal magnetic force over the master bodies."""

    calculate_magnetic_torque: bool = False
    """Also lump the magnetic torque (requires ``torque_origin``/``torque_axis``)."""

    torque_origin: Vec3Field | None = None
    """`r_0` `[m]`: Torque pivot point."""

    torque_axis: DirectionField | None = None
    """Torque axis direction."""

    def to_elmer(self) -> dict[str, float | str]:
        """The CalcFields force and torque flags, plus the torque origin and axis when set."""
        out: dict[str, float | str] = {}
        if self.calculate_magnetic_force:
            out["Calculate Magnetic Force"] = "Logical True"
        if self.calculate_magnetic_torque:
            out["Calculate Magnetic Torque"] = "Logical True"
        if self.torque_origin is not None:
            out["Torque Origin(3)"] = f"Real {self.torque_origin.x:.6g} {self.torque_origin.y:.6g} {self.torque_origin.z:.6g}"
        if self.torque_axis is not None:
            out["Torque Axis(3)"] = f"Real {self.torque_axis.x:.6g} {self.torque_axis.y:.6g} {self.torque_axis.z:.6g}"
        return out

    def check_frame(self, frame: Frame) -> None:
        """Raise when torque is requested under a ``frame`` whose force lumping yields none."""
        torque: bool = self.calculate_magnetic_torque or self.torque_origin is not None or self.torque_axis is not None

        if torque and not frame.lumps_torque:
            raise ValueError(f"CalculateMagneticForce torque has no form in the {frame.label} frame; drop calculate_magnetic_torque, torque_origin and torque_axis.")


class FixedDisplacement(Condition):
    """A prescribed displacement on a boundary, clamped to zero in every direction by default."""

    kind: Literal["fixed_displacement"] = "fixed_displacement"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.LINEAR_ELASTICITY
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BOUNDARY_CONDITION

    x: Annotated[Length | None, ElmerKeyword(f"{ElmerVariable.DISPLACEMENT} 1", U.m)] = Field(default_factory=lambda: quantity(0.0, "m"))
    """`u_x` `[m]`: Prescribed x displacement; None leaves x free."""

    y: Annotated[Length | None, ElmerKeyword(f"{ElmerVariable.DISPLACEMENT} 2", U.m)] = Field(default_factory=lambda: quantity(0.0, "m"))
    """`u_y` `[m]`: Prescribed y displacement; None leaves y free."""

    z: Annotated[Length | None, ElmerKeyword(f"{ElmerVariable.DISPLACEMENT} 3", U.m)] = Field(default_factory=lambda: quantity(0.0, "m"))
    """`u_z` `[m]`: Prescribed z displacement; None leaves z free."""

    def check_frame(self, frame: Frame) -> None:
        """Raise when a nonzero prescribed component lies along a direction ``frame`` has no solve coordinate for."""
        components: tuple[tuple[str, Length | None, Vec3], ...] = (
            ("x", self.x, Vec3(1.0, 0.0, 0.0)),
            ("y", self.y, Vec3(0.0, 1.0, 0.0)),
            ("z", self.z, Vec3(0.0, 0.0, 1.0)),
        )

        for name, value, direction in components:
            if value is None or _si(value, U.m) == 0.0:
                continue

            try:
                _ = frame.solve_vector(direction)
            except ValueError:
                raise ValueError(f"FixedDisplacement prescribes a nonzero {name}, which has no form in the {frame.label} frame; set {name}=None.") from None


class SurfaceForce(Condition):
    """A total force spread uniformly over a named face as a traction.

    Attach it to a ``TaggedFace``'s tag: the traction is the force over the face's measured area, which an adjacency group does not carry.
    """

    kind: Literal["surface_force"] = "surface_force"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.LINEAR_ELASTICITY
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BOUNDARY_CONDITION

    force: Force
    """`F⃗` `[N]`: Total force spread uniformly over the face, in the authored frame."""

    def _solve_force(self, frame: Frame) -> tuple[float, ...]:
        total: Vec3 = self.force.to(U.N).magnitude

        return frame.solve_vector(frame.solve_share(total))

    def to_elmer(self) -> dict[str, float | str]:
        """Raise: the traction needs the face area, which only [`on_boundary`][femforge.physical.conditions.SurfaceForce.on_boundary] knows."""
        raise ValueError("SurfaceForce needs the area of the face it lands on; it emits through on_boundary only.")

    def on_boundary(self, site: BoundarySite) -> dict[str, float | str]:
        """The ``Force i`` traction components, the solve-frame force share over the measured area of ``site``."""
        measure: float | None = site.measure

        if measure is None or measure <= 0.0:
            raise ValueError(f"SurfaceForce on boundary group {site.name} has no measured area; attach SurfaceForce to a TaggedFace's EntityTag.")

        components: tuple[float, ...] = self._solve_force(site.frame)

        return {f"Force {index}": component / measure for index, component in enumerate(components, start=1)}

    def check_frame(self, frame: Frame) -> None:
        """Raise when ``frame`` has no form for the force direction."""
        try:
            _ = self._solve_force(frame)
        except ValueError as error:
            raise ValueError(f"SurfaceForce has no form in the {frame.label} frame: {error}") from None


# A region holds a polymorphic ``list[Condition]``. A distinct ``kind`` per subclass lets Pydantic build a discriminated union so the list round-trips and exports clean JSON schema.
ConditionUnion = Annotated[
    Union[
        Magnetization, CurrentDensity, FixedTemperature, HeatFlux, Convection, HeatSource, FixedPotential, JouleHeat, CoilDrive, CoilTerminal, CalculateMagneticForce, FixedDisplacement, SurfaceForce
    ],
    Field(discriminator="kind"),
]
"""Any concrete condition, discriminated by its ``kind``."""


def shipped_conditions() -> tuple[type[Condition], ...]:
    """The condition classes ``ConditionUnion`` admits, in its order."""
    parts: tuple[object, ...] = get_args(ConditionUnion)

    return cast("tuple[type[Condition], ...]", get_args(parts[0]))
