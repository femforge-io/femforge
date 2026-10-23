"""Conditions attached to a region, each carrying its physics, its body or boundary target and its own Elmer sif emission."""
# Must not import meshing.config or elmer.sim: both depend on this module.

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, Union, cast, get_args

from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo

from femforge.common.introspect import FIELD_DOCSTRING
from femforge.common.utils import title
from femforge.common.vector import DirectionField, Vec3, Vec3Field
from femforge.elmer.physics import Physics
from femforge.elmer.sections import SifSection
from femforge.elmer.variables import ElmerVariable
from femforge.frames.base import ALIGNMENT_TOLERANCE
from femforge.physical.readout import along, components_readout, readout, vector_readout
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
    from femforge.elmer.config import SifValue
    from femforge.frames import Frame
    from femforge.frames.base import SolvePlane


__all__ = [
    "Combine",
    "ConditionTarget",
    "ConditionGlyph",
    "BoundarySite",
    "Component",
    "SummaryPart",
    "SummaryLine",
    "Condition",
    "Magnetization",
    "CurrentDensity",
    "FixedTemperature",
    "HeatFlux",
    "Convection",
    "HeatSource",
    "FixedVoltage",
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


class Combine(StrEnum):
    """How a condition's keywords combine with another condition's in the same sif block."""

    SUM = "sum"
    """A load: values of the same keyword add."""

    MATCH = "match"
    """A fixed value or a flag: values of the same keyword must agree."""

    ALONE = "alone"
    """At most one condition of the kind per block."""


class ConditionGlyph(StrEnum):
    """The kind of scene glyph and legend row a condition draws as."""

    SURFACE_FORCE = "surface_force"
    """A load arrow on a face."""

    FIXED_DISPLACEMENT = "fixed_displacement"
    """A held face."""

    MAGNETIZATION = "magnetization"
    """A magnet's direction arrow."""

    CURRENT_DENSITY = "current_density"
    """A coil drive's current marker."""

    THERMAL = "thermal"
    """A thermal source or boundary value."""

    VOLTAGE = "voltage"
    """A voltage potential or coil terminal."""


def _solve_axes(frame: Frame | None) -> tuple[Vec3, ...]:
    """The authored directions of the solve coordinates of ``frame``: the world axes for a whole-model or absent frame, or the solve plane's X and Y."""
    plane: SolvePlane | None = frame.plane if frame is not None else None

    if plane is None:
        return (Vec3(1.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0), Vec3(0.0, 0.0, 1.0))

    return (plane.x_dir, plane.y_dir)


def _snapped(cosine: float) -> float:
    """``cosine`` snapped to 0 or ±1 within ``ALIGNMENT_TOLERANCE``."""
    if abs(cosine) <= ALIGNMENT_TOLERANCE:
        return 0.0

    if abs(abs(cosine) - 1.0) <= ALIGNMENT_TOLERANCE:
        return math.copysign(1.0, cosine)

    return cosine


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


type Component = Literal["x", "y", "z"]
"""An authored axis, named as the ``FixedDisplacement`` field that holds it."""


@dataclass(frozen=True)
class SummaryPart:
    """One value of a condition as the Setup tab reads it, with the glyph or word read before it."""

    value: str
    """The value as read, such as ``100 N Along (1, 0, 0)``."""

    label: str = ""
    """The glyph or word read before the value, such as ``h`` or ``Traction``; empty for none."""

    component: Component | None = None
    """The authored axis the part reads, so the Setup tab can mark an axis the frame drops; None for any other part."""

    tip: str = ""
    """What a placeholder in the value stands for, such as the expression an ``expr`` replaces; empty for none."""


type SummaryLine = tuple[SummaryPart, ...]
"""One line of a condition's summary, its parts in reading order."""


_DROPPED = "–"
"""What an authored axis the frame has no solve coordinate for reads as."""

_EXPRESSION = "expr"
"""What a keyword holding an expression rather than a number reads as."""


def _numbers(emitted: Mapping[str, float | str] | None, stem: str) -> list[tuple[int, float | str]]:
    """The 1-based index and value of each ``stem 1`` to ``stem 3`` keyword in ``emitted``, in solve order: a number as a float, an expression as its text."""
    if emitted is None:
        return []

    found: list[tuple[int, float | str]] = []

    for index in range(1, 4):
        value: float | str | None = emitted.get(f"{stem} {index}")

        if value is None:
            continue

        try:
            found.append((index, float(value)))
        except ValueError:
            found.append((index, str(value)))

    return found


def _components_part(components: list[tuple[int, float | str]], unit: str, label: str, *, scale: float = 1.0) -> SummaryPart:
    """``components`` scaled by ``scale`` as one signed tuple in ``unit`` after ``label``; an expression reads ``expr`` in its place, its text the tooltip."""
    values: list[float | str] = [value * scale if isinstance(value, float) else _EXPRESSION for _, value in components]
    expressions: list[str] = [value for _, value in components if isinstance(value, str)]

    return SummaryPart(components_readout(values, unit), label=label, tip="; ".join(expressions))


class Condition(BaseModel):
    """A source or constraint of one physics on the bodies or boundaries a tag selects; each subclass is one shipped condition."""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    external_only: ClassVar[bool] = False
    """Boundary-target conditions with this set land only on a body's *external* (domain-boundary) surface groups,
    not its interfaces — e.g. a coil terminal is the electrode's exposed end face, not its interior contacts."""

    section: ClassVar[SifSection]
    """The sif section this condition's keywords land in; ``BOUNDARY_CONDITION`` exactly when ``target`` is ``BOUNDARY``."""

    glyph: ClassVar[ConditionGlyph | None]
    """The scene glyph this condition draws as; None for an output that draws nothing."""

    combine: ClassVar[Combine] = Combine.MATCH
    """How its keywords combine with another condition's in the same sif block."""

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

    def yields_to(self, emitted: Mapping[str, SifValue]) -> tuple[tuple[str, str], ...]:
        """Each load keyword in ``emitted`` paired with the fixed-value keyword that makes Elmer ignore it in the same block; the default yields to none."""
        return ()

    def check_frame(self, frame: Frame) -> None:
        """Raise when this condition cannot drive a ``frame`` solve."""

    def solve_components(self, frame: Frame) -> tuple[tuple[int, float], ...]:
        """The 1-based solve coordinates and values of this condition's vector in ``frame``; the default has none."""
        return ()

    def dropped_fields(self, frame: Frame) -> tuple[str, ...]:
        """The authored fields ``frame`` has no solve coordinate for; the default drops none."""
        return ()

    def summary(self, emitted: Mapping[str, float | str] | None = None, *, frame: Frame | None = None) -> tuple[SummaryLine, ...]:
        """How the Setup tab reads this condition: one line of its sif-keyword fields, each after its glyph when there are several; the default ignores ``emitted`` and ``frame``.

        Args:
            emitted: The keywords the condition's sif block received, by name.
            frame: The frame the run solved in; None reads the authored axes as the solve axes.
        """

        def glyph_of(field: FieldInfo) -> str:
            match: re.Match[str] | None = FIELD_DOCSTRING.match(field.description or "")
            symbol: str | None = match.group("symbol") if match is not None else None

            return symbol.split("_")[0] if symbol else ""

        values: list[tuple[FieldInfo, Quantity]] = []

        for name, field in type(self).model_fields.items():
            value: Quantity | None = getattr(self, name) if _elmer_keyword(field) is not None else None

            if value is not None:
                values.append((field, value))

        labelled: bool = len(values) > 1
        parts: SummaryLine = tuple(SummaryPart(readout(value), label=glyph_of(field) if labelled else "") for field, value in values)

        return (parts,)


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

    glyph: ClassVar[ConditionGlyph | None] = ConditionGlyph.MAGNETIZATION

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

    def solve_components(self, frame: Frame) -> tuple[tuple[int, float], ...]:
        """The solve components of the unit direction in ``frame``; none without a direction."""
        if self.direction is None:
            return ()

        components: tuple[float, ...] = frame.solve_vector(self.direction)

        return tuple(enumerate(components, start=1))

    def summary(self, emitted: Mapping[str, float | str] | None = None, *, frame: Frame | None = None) -> tuple[SummaryLine, ...]:
        """The authored direction, then the remanence ``Br = μ₀·M`` of the emitted ``Magnetization i`` components in solve order; ``frame`` is ignored.

        Args:
            emitted: The keywords the magnet's Material block received, by name.
            frame: The frame the run solved in.
        """
        if self.direction is None:
            return ((SummaryPart("No Direction"),),)

        direction: SummaryLine = (SummaryPart(f"Along {along(self.direction)}"),)
        components: list[tuple[int, float | str]] = _numbers(emitted, "Magnetization")

        if not components:
            return (direction,)

        mu_0: float = quantity(1.0, "mu_0").m_as("T*m/A")

        return (direction, (_components_part(components, "T", "Br", scale=mu_0),))


class CurrentDensity(Condition):
    """A uniform signed out-of-plane current density J = N·I/A over a winding window, the homogenized drive of a stranded coil in a 2D solve."""

    kind: Literal["current_density"] = "current_density"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.MAGNETOSTATICS_2D
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BODY_FORCE

    glyph: ClassVar[ConditionGlyph | None] = ConditionGlyph.CURRENT_DENSITY

    combine: ClassVar[Combine] = Combine.SUM

    value: Annotated[CurrentDensityQuantity, ElmerKeyword("Current Density", U.A / U.m**2)]
    """`J` `[A/m²]`: Signed out-of-plane current density imposed uniformly over the body: azimuthal under Axisymmetric, along +normal under Cartesian2D."""

    def check_frame(self, frame: Frame) -> None:
        """Raise when ``frame`` has no 2D magnetostatic solve for this drive to feed."""
        if not frame.supports(Physics.MAGNETOSTATICS_2D):
            raise ValueError(f"CurrentDensity is the out-of-plane coil drive of '{Physics.MAGNETOSTATICS_2D.value}', which the {frame.label} frame has no form for.")


class FixedTemperature(Condition):
    """A fixed temperature on a boundary."""

    kind: Literal["fixed_temperature"] = "fixed_temperature"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.THERMAL
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BOUNDARY_CONDITION

    glyph: ClassVar[ConditionGlyph | None] = ConditionGlyph.THERMAL

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

    glyph: ClassVar[ConditionGlyph | None] = ConditionGlyph.THERMAL

    combine: ClassVar[Combine] = Combine.SUM

    value: Annotated[HeatFluxQuantity, ElmerKeyword("Heat Flux", U.W / U.m**2)]
    """`q_s` `[W/m²]`: Heat flux through the boundary."""

    def yields_to(self, emitted: Mapping[str, SifValue]) -> tuple[tuple[str, str], ...]:
        """The heat flux, which a fixed temperature on the same boundary overrides."""
        return tuple((key, str(ElmerVariable.TEMPERATURE)) for key in emitted)


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

    glyph: ClassVar[ConditionGlyph | None] = ConditionGlyph.THERMAL

    external_only: ClassVar[bool] = True

    combine: ClassVar[Combine] = Combine.ALONE

    coefficient: Annotated[HeatTransferCoefficient, ElmerKeyword("Heat Transfer Coefficient", U.W / (U.m**2 * U.K))]
    """`h` `[W/(m²·K)]`: Convective heat-transfer coefficient."""

    ext_temperature: Annotated[Temperature, ElmerKeyword("External Temperature", U.K)]
    """`T_amb` `[K]`: External (ambient) temperature the boundary exchanges with."""

    def yields_to(self, emitted: Mapping[str, SifValue]) -> tuple[tuple[str, str], ...]:
        """The film exchange, which a fixed temperature on the same boundary overrides."""
        return tuple((key, str(ElmerVariable.TEMPERATURE)) for key in emitted)


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

    glyph: ClassVar[ConditionGlyph | None] = ConditionGlyph.THERMAL

    combine: ClassVar[Combine] = Combine.SUM

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


class FixedVoltage(Condition):
    """A fixed voltage potential on an electrode, one end of a phase's terminal pair.

    The run also integrates the current through the electrode.
    """

    kind: Literal["fixed_voltage"] = "fixed_voltage"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.CURRENT_CONDUCTION
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BOUNDARY_CONDITION

    glyph: ClassVar[ConditionGlyph | None] = ConditionGlyph.VOLTAGE

    value: Annotated[Voltage, ElmerKeyword(ElmerVariable.POTENTIAL, U.V)]
    """`V` `[V]`: The voltage potential held on the boundary."""

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

    glyph: ClassVar[ConditionGlyph | None] = ConditionGlyph.THERMAL

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

    glyph: ClassVar[ConditionGlyph | None] = ConditionGlyph.CURRENT_DENSITY

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

    glyph: ClassVar[ConditionGlyph | None] = ConditionGlyph.VOLTAGE

    external_only: ClassVar[bool] = True

    role: Literal["start", "end"]
    """Which end of the coil this electrode is; CoilSolver's normalized current flows start -> end."""

    def to_elmer(self) -> dict[str, float | str]:
        """The ``Coil Start`` or ``Coil End`` flag for this ``role``."""
        return {"Coil Start" if self.role == "start" else "Coil End": "Logical True"}

    def summary(self, emitted: Mapping[str, float | str] | None = None, *, frame: Frame | None = None) -> tuple[SummaryLine, ...]:
        """The ``role``, ``Start`` or ``End``; ``emitted`` and ``frame`` are ignored.

        Args:
            emitted: The keywords the electrode's sif block received, by name.
            frame: The frame the run solved in.
        """
        return ((SummaryPart(title(self.role)),),)


class CalculateMagneticForce(Condition):
    """Lumps the magnetic force, and optionally the torque, over the bodies its tag matches into one Elmer ``Component``."""

    kind: Literal["calculate_magnetic_force"] = "calculate_magnetic_force"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.MAGNETOSTATICS
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BODY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.COMPONENT

    glyph: ClassVar[ConditionGlyph | None] = None

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

    def summary(self, emitted: Mapping[str, float | str] | None = None, *, frame: Frame | None = None) -> tuple[SummaryLine, ...]:
        """That the condition is an output the scene draws no glyph for; ``emitted`` and ``frame`` are ignored.

        Args:
            emitted: The keywords the force Component received, by name.
            frame: The frame the run solved in.
        """
        return ((SummaryPart("(Output, No Glyph)"),),)


class FixedDisplacement(Condition):
    """A prescribed displacement on a boundary, clamped to zero in every direction by default."""

    kind: Literal["fixed_displacement"] = "fixed_displacement"
    """Discriminator that names this condition type in a serialized config."""

    physics: Physics = Physics.LINEAR_ELASTICITY
    """The physics whose solve this condition feeds."""

    target: ConditionTarget = ConditionTarget.BOUNDARY
    """Whether the condition attaches to a body or a boundary."""

    section: ClassVar[SifSection] = SifSection.BOUNDARY_CONDITION

    glyph: ClassVar[ConditionGlyph | None] = ConditionGlyph.FIXED_DISPLACEMENT

    x: Length | None = Field(default_factory=lambda: quantity(0.0, "m"))
    """`u_x` `[m]`: Prescribed displacement along the authored X axis; None leaves it free."""

    y: Length | None = Field(default_factory=lambda: quantity(0.0, "m"))
    """`u_y` `[m]`: Prescribed displacement along the authored Y axis; None leaves it free."""

    z: Length | None = Field(default_factory=lambda: quantity(0.0, "m"))
    """`u_z` `[m]`: Prescribed displacement along the authored Z axis; None leaves it free."""

    def _constraints(self) -> tuple[tuple[Component, Vec3, float | None], ...]:
        """Each authored axis's name, unit direction and prescribed SI displacement, None when it is free."""
        x: float | None = None if self.x is None else _si(self.x, U.m)
        y: float | None = None if self.y is None else _si(self.y, U.m)
        z: float | None = None if self.z is None else _si(self.z, U.m)

        return (
            ("x", Vec3(1.0, 0.0, 0.0), x),
            ("y", Vec3(0.0, 1.0, 0.0), y),
            ("z", Vec3(0.0, 0.0, 1.0), z),
        )

    def solve_components(self, frame: Frame) -> tuple[tuple[int, float], ...]:
        """Each 1-based solve coordinate the constrained axes run along in ``frame``, with its signed prescribed value; raises when the mapping is ill posed."""
        solve_axes: tuple[Vec3, ...] = _solve_axes(frame)
        constraints: tuple[tuple[Component, Vec3, float | None], ...] = self._constraints()

        for name, axis, value in constraints:
            if value is None or value == 0.0:
                continue

            in_plane: Vec3 = sum((solve_axis * axis.dot(solve_axis) for solve_axis in solve_axes), start=Vec3(0.0, 0.0, 0.0))

            # A residual vector, not sqrt(1 - Σcos²): the 12-digit Direction rounding puts the latter right at the tolerance.
            if (axis - in_plane).magnitude() > ALIGNMENT_TOLERANCE:
                raise ValueError(f"FixedDisplacement prescribes a nonzero {name}, which has no form in the {frame.label} frame; set {name}=None.")

        components: list[tuple[int, float]] = []

        for index, solve_axis in enumerate(solve_axes, start=1):
            held: list[tuple[str, float, float]] = []
            free: list[str] = []

            for name, axis, value in constraints:
                cosine: float = _snapped(axis.dot(solve_axis))

                if cosine == 0.0:
                    continue

                if value is None:
                    free.append(name)

                    continue

                held.append((name, cosine, value))

            if held and free:
                raise ValueError(
                    f"FixedDisplacement constrains {held[0][0]} but leaves {free[0]} free, and the {frame.label} frame solves coordinate {index} as a mix of the two; "
                    f"constrain {free[0]} too, or orient the frame so its x_dir runs along {held[0][0]}."
                )

            if held:
                terms: list[float] = [cosine * value for _, cosine, value in held]
                components.append((index, sum(terms, start=0.0)))

        return tuple(components)

    def to_elmer(self) -> dict[str, float | str]:
        """Raise: the keywords depend on the frame, which only [`on_boundary`][femforge.physical.conditions.FixedDisplacement.on_boundary] knows."""
        raise ValueError("FixedDisplacement maps its axes through the frame of the boundary it lands on; it emits through on_boundary only.")

    def on_boundary(self, site: BoundarySite) -> dict[str, float | str]:
        """The ``Displacement i`` keywords of the solve coordinates the constrained axes run along in the frame of ``site``, signed by their alignment; an axis off the solve plane adds none."""
        components: tuple[tuple[int, float], ...] = self.solve_components(site.frame)

        return {f"{ElmerVariable.DISPLACEMENT} {index}": value for index, value in components}

    def check_frame(self, frame: Frame) -> None:
        """Raise when a nonzero component leaves the solve plane or a constrained axis runs oblique to a free one in ``frame``."""
        _ = self.solve_components(frame)

    def dropped_fields(self, frame: Frame) -> tuple[str, ...]:
        """The constrained authored axes that run along no solve coordinate of ``frame``, such as ``("z",)`` under a Cartesian 2D frame normal to Z."""
        solve_axes: tuple[Vec3, ...] = _solve_axes(frame)
        dropped: list[str] = []

        for name, axis, value in self._constraints():
            if value is None:
                continue

            if all(_snapped(axis.dot(solve_axis)) == 0.0 for solve_axis in solve_axes):
                dropped.append(name)

        return tuple(dropped)

    def summary(self, emitted: Mapping[str, float | str] | None = None, *, frame: Frame | None = None) -> tuple[SummaryLine, ...]:
        """``X 0 · Y Free · Z –``: each authored axis in order, held at the value its solve coordinate received, ``Free``, or ``–`` when ``frame`` drops it.

        Args:
            emitted: The keywords the boundary's sif block received, by name; None reads the authored values.
            frame: The frame the boundary's model solved in; None reads the authored axes as the solve axes.
        """
        solve_axes: tuple[Vec3, ...] = _solve_axes(frame)
        received: dict[int, float | str] = dict(_numbers(emitted, str(ElmerVariable.DISPLACEMENT)))

        def shown(value: float) -> str:
            return "0" if value == 0.0 else readout(quantity(value, "m"))

        def part(component: Component, axis: Vec3, value: float | None) -> SummaryPart:
            label: str = component.upper()

            if value is None:
                return SummaryPart("Free", label=label, component=component)

            cosines: list[float] = [_snapped(axis.dot(solve_axis)) for solve_axis in solve_axes]
            aligned: list[tuple[int, float]] = [(index, cosine) for index, cosine in enumerate(cosines, start=1) if cosine != 0.0]

            if not aligned:
                return SummaryPart(_DROPPED, label=label, component=component)

            index, cosine = aligned[0]
            solved: float | str | None = received.get(index)

            if len(aligned) == 1 and abs(cosine) == 1.0 and isinstance(solved, float):
                return SummaryPart(shown(cosine * solved), label=label, component=component)

            return SummaryPart(shown(value), label=label, component=component)

        parts: SummaryLine = tuple(part(name, axis, value) for name, axis, value in self._constraints())

        return (parts,)


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

    glyph: ClassVar[ConditionGlyph | None] = ConditionGlyph.SURFACE_FORCE

    combine: ClassVar[Combine] = Combine.SUM

    force: Force
    """`F⃗` `[N]`: Total force spread uniformly over the face, in the authored frame."""

    def yields_to(self, emitted: Mapping[str, SifValue]) -> tuple[tuple[str, str], ...]:
        """Each nonzero ``Force i``, which a fixed ``Displacement i`` on the same boundary overrides."""
        return tuple((key, f"{ElmerVariable.DISPLACEMENT} {key.removeprefix('Force ')}") for key, value in emitted.items() if key.startswith("Force ") and value != 0.0)

    def _solve_force(self, frame: Frame) -> tuple[float, ...]:
        """The solve components of the solve domain's share of the force."""
        share: Vec3 = frame.authored_share(self.force.to(U.N).magnitude)

        return share.to_tuple()[: frame.model_dim]

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

    def solve_components(self, frame: Frame) -> tuple[tuple[int, float], ...]:
        """The solve components, in N, of the share of the force the solve domain carries in ``frame``: per metre of depth in Cartesian 2D."""
        components: tuple[float, ...] = self._solve_force(frame)

        return tuple(enumerate(components, start=1))

    def summary(self, emitted: Mapping[str, float | str] | None = None, *, frame: Frame | None = None) -> tuple[SummaryLine, ...]:
        """The authored total and its direction, then the signed traction the emitted ``Force i`` components hold on the face, in solve order; ``frame`` is ignored.

        Args:
            emitted: The keywords the face's sif block received, by name.
            frame: The frame the run solved in.
        """
        total: SummaryLine = (SummaryPart(vector_readout(self.force)),)
        components: list[tuple[int, float | str]] = _numbers(emitted, "Force")

        if not components:
            return (total,)

        return (total, (_components_part(components, "Pa", "Traction"),))


# A region holds a polymorphic ``list[Condition]``. A distinct ``kind`` per subclass lets Pydantic build a discriminated union so the list round-trips and exports clean JSON schema.
ConditionUnion = Annotated[
    Union[Magnetization, CurrentDensity, FixedTemperature, HeatFlux, Convection, HeatSource, FixedVoltage, JouleHeat, CoilDrive, CoilTerminal, CalculateMagneticForce, FixedDisplacement, SurfaceForce],
    Field(discriminator="kind"),
]
"""Any concrete condition, discriminated by its ``kind``."""


def shipped_conditions() -> tuple[type[Condition], ...]:
    """The condition classes ``ConditionUnion`` admits, in its order."""
    parts: tuple[object, ...] = get_args(ConditionUnion)

    return cast("tuple[type[Condition], ...]", get_args(parts[0]))
