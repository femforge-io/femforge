"""Conditions attached to a region, each carrying its physics, its body or boundary target and its own Elmer sif emission."""
# Must not import meshing.config or elmer.sim: both depend on this module.

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo

from femforge.common.frame import Axisymmetric, Frame, Solid
from femforge.common.vector import DirectionField, Vec3, Vec3Field
from femforge.elmer.physics import Physics
from femforge.elmer.variables import ElmerVariable
from femforge.physical.units import (
    Current,
    CurrentDensity as CurrentDensityQuantity,
    HeatFlux as HeatFluxQuantity,
    HeatTransferCoefficient,
    PowerDensity,
    Quantity,
    Temperature,
    U,
    Unit,
    Voltage,
)


__all__ = [
    "ConditionTarget",
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
    "ForceComponent",
    "ConditionUnion",
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


class Condition(BaseModel):
    """Base condition: subclasses pin ``physics``/``target`` defaults and implement ``to_elmer``. Never instantiated directly (no ``kind`` discriminator)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    external_only: ClassVar[bool] = False
    """Boundary-target conditions with this set land only on a body's *external* (domain-boundary) surface groups,
    not its interfaces — e.g. a coil terminal is the electrode's exposed end face, not its interior contacts."""

    physics: Physics
    """The physics whose solve this condition feeds."""

    target: ConditionTarget
    """Whether the condition attaches to a body or a boundary."""

    def to_elmer(self, *args: Any, **kwargs: Any) -> dict[str, float | str]:  # pyright: ignore[reportExplicitAny, reportAny]  # variadic: subclass to_elmer signatures diverge (Magnetization takes a magnitude)
        """This condition's Elmer sif keyword dict, reflected from each field's ``ElmerKeyword`` metadata."""
        out: dict[str, float | str] = {}
        for name, field in type(self).model_fields.items():
            spec: ElmerKeyword | None = _elmer_keyword(field)
            if spec is None:
                continue
            out[spec.key] = _si(getattr(self, name), spec.unit)  # pyright: ignore[reportAny]  # getattr is Any
        return out

    def check_frame(self, frame: Frame) -> None:
        """Raise when this condition cannot drive a ``frame`` solve."""


class Magnetization(Condition):
    """
    Permanent-magnet body force (magnetostatics, body target).

    The direction is per-region and lives here; the magnitude ``|M| = Bᵣ/μ₀`` is a material property passed into ``to_elmer``.
    """

    kind: Literal["magnetization"] = "magnetization"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.MAGNETOSTATICS
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

    direction: DirectionField | None
    """Magnetization direction in the authored model's frame, None for an unmagnetized body; magnitude comes from the material."""

    def to_elmer(self, magnitude: float, frame: Frame) -> dict[str, float | str]:
        """The ``Magnetization`` components of ``magnitude`` (A/m) along the direction, in ``frame``'s solve coordinates."""
        unit: Vec3 | None = self.direction

        if unit is None:
            raise ValueError("A Magnetization without a direction has no components to emit.")

        match frame:
            case Solid():
                m: Vec3 = unit * magnitude
                return {
                    "Magnetization 1": f"{m.x:.6g}",
                    "Magnetization 2": f"{m.y:.6g}",
                    "Magnetization 3": f"{m.z:.6g}",
                }
            case Axisymmetric():
                along: float | None = frame.axis.along(unit)

                if along is None:
                    raise ValueError(f"Magnetization direction {self.direction} is not along the symmetry axis {frame.axis.direction}; a transverse magnetization is not axisymmetric.")

                return {
                    "Magnetization 1": "0",
                    "Magnetization 2": f"{along * magnitude:.6g}",
                }

    def check_frame(self, frame: Frame) -> None:
        """Raise when an axisymmetric ``frame`` meets a direction off its symmetry axis."""
        # A missing direction is left to Sim.validate, which reports it as a missing direction rather than a frame mismatch.
        if not isinstance(frame, Axisymmetric) or self.direction is None:
            return

        if frame.axis.along(self.direction) is None:
            raise ValueError(f"Magnetization direction {self.direction} is not along the symmetry axis {frame.axis.direction}; a transverse magnetization is not axisymmetric.")


class CurrentDensity(Condition):
    """Uniform signed azimuthal current density J = N·I/A over a winding window (axisymmetric magnetostatics, body target), the homogenized stranded-coil drive.

    Requires ``frame=Axisymmetric()``.
    """

    kind: Literal["current_density"] = "current_density"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.MAGNETOSTATICS_2D
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

    value: Annotated[CurrentDensityQuantity, ElmerKeyword("Current Density", U.A / U.m**2)]
    """`J` `[A/m²]`: Azimuthal current density (signed) imposed uniformly over the body."""

    def check_frame(self, frame: Frame) -> None:
        """Raise unless ``frame`` is axisymmetric."""
        if isinstance(frame, Solid):
            raise ValueError("CurrentDensity is the axisymmetric coil drive; it requires frame=Axisymmetric().")


class FixedTemperature(Condition):
    """Dirichlet temperature boundary condition (thermal, boundary target)."""

    kind: Literal["fixed_temperature"] = "fixed_temperature"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.THERMAL
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    value: Annotated[Temperature, ElmerKeyword(ElmerVariable.TEMPERATURE, U.K)]
    """`T` `[K]`: Fixed temperature imposed on the boundary."""


class HeatFlux(Condition):
    """Prescribed heat flux boundary condition (thermal, boundary target)."""

    kind: Literal["heat_flux"] = "heat_flux"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.THERMAL
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    value: Annotated[HeatFluxQuantity, ElmerKeyword("Heat Flux", U.W / U.m**2)]
    """`q_s` `[W/m²]`: Heat flux through the boundary."""


class Convection(Condition):
    """Convective (Newton-cooling) boundary condition (thermal, boundary target); ``external_only`` — a film BC on an interior interface would bypass the conduction path."""

    kind: Literal["convection"] = "convection"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.THERMAL
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    external_only: ClassVar[bool] = True

    coefficient: Annotated[HeatTransferCoefficient, ElmerKeyword("Heat Transfer Coefficient", U.W / (U.m**2 * U.K))]
    """`h` `[W/(m²·K)]`: Convective heat-transfer coefficient."""

    ext_temperature: Annotated[Temperature, ElmerKeyword("External Temperature", U.K)]
    """`T_amb` `[K]`: External (ambient) temperature the boundary exchanges with."""


class HeatSource(Condition):
    """Volumetric heat source in W/m³ (thermal, body target).

    With ``temperature_coefficient`` set, the source scales linearly with the local temperature, the σ(T) Joule feedback of a prescribed-current winding.
    """

    kind: Literal["heat_source"] = "heat_source"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.THERMAL
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

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
    """Dirichlet electric-potential boundary condition (current conduction, boundary target), one electrode of a phase's terminal pair."""

    kind: Literal["fixed_potential"] = "fixed_potential"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.CURRENT_CONDUCTION
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    value: Annotated[Voltage, ElmerKeyword(ElmerVariable.POTENTIAL, U.V)]
    """`V` `[V]`: Electric potential imposed on the electrode boundary; the keyword is the solver variable's name (Elmer Dirichlet semantics), single-sourced via the enum."""

    def to_elmer(self) -> dict[str, float | str]:
        """The electrode potential plus the ``Save Scalars`` flag that makes SaveScalars integrate the electrode current."""
        out: dict[str, float | str] = super().to_elmer()
        # Mark the electrode for SaveScalars boundary integration (the per-phase current measurement).
        out["Save Scalars"] = "Logical True"
        return out


class JouleHeat(Condition):
    """
    Joule heat source body force (coupled current->thermal, body target): HeatSolve integrates StatCurrentSolve's loss density as the heat source.

    Carries no value — the magnitude is the solved current field itself.
    """

    kind: Literal["joule_heat"] = "joule_heat"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.JOULE_HEATING
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

    def to_elmer(self) -> dict[str, float | str]:
        """The ``Joule Heat`` flag."""
        return {"Joule Heat": "Logical True"}


class CoilDrive(Condition):
    """A driven coil under ``COIL_MAGNETOSTATICS`` (body target): bodies sharing this condition become one massive CoilSolver ``Component`` normalized to ``current``.

    Reverse the drive by swapping the terminal roles, not with a negative current.
    """

    kind: Literal["coil_drive"] = "coil_drive"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.COIL_MAGNETOSTATICS
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

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
    """One end of an open coil's terminal pair, Elmer's ``Coil Start`` or ``Coil End`` marker (``COIL_MAGNETOSTATICS``, boundary target).

    The electrode's end face must lie on the domain boundary (``GeometryConfig.terminate_electrodes``); positive coil current flows from start to end.
    """

    kind: Literal["coil_terminal"] = "coil_terminal"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.COIL_MAGNETOSTATICS
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    external_only: ClassVar[bool] = True

    role: Literal["start", "end"]
    """Which end of the coil this electrode is; CoilSolver's normalized current flows start -> end."""

    def to_elmer(self) -> dict[str, float | str]:
        """The ``Coil Start`` or ``Coil End`` flag for this ``role``."""
        return {"Coil Start" if self.role == "start" else "Coil End": "Logical True"}


class ForceComponent(Condition):
    """An Elmer ``Component`` lumping CalcFields' magnetic force and torque over the bodies this condition's tag matches (magnetic physics, body target)."""

    kind: Literal["force_component"] = "force_component"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.MAGNETOSTATICS
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

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
        """Raise when torque is requested under an axisymmetric ``frame``."""
        torque: bool = self.calculate_magnetic_torque or self.torque_origin is not None or self.torque_axis is not None

        if torque and isinstance(frame, Axisymmetric):
            raise ValueError("ForceComponent torque has no axisymmetric form; drop calculate_magnetic_torque, torque_origin and torque_axis under frame=Axisymmetric().")


# A region holds a polymorphic ``list[Condition]``. A distinct ``kind`` per subclass lets Pydantic build a discriminated union so the list round-trips and exports clean JSON schema.
ConditionUnion = Annotated[
    Union[Magnetization, CurrentDensity, FixedTemperature, HeatFlux, Convection, HeatSource, FixedPotential, JouleHeat, CoilDrive, CoilTerminal, ForceComponent],
    Field(discriminator="kind"),
]
"""Any concrete condition, discriminated by its ``kind``."""
