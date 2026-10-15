"""Analytical magnet sources: a `DomainConfig` and built geometry turned into SI `MagnetSource`s for magpylib."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from femforge.common.tags import TagKey, parse_tags
from femforge.common.vector import Vec3, Vec3Field
from femforge.elmer.physics import Physics
from femforge.materials.properties import PERMEABILITY_OF_VACUUM, MaterialProperties
from femforge.physical.conditions import ConditionTarget, Magnetization
from femforge.physical.domain import DomainConfig, EntityTag, conditions_for
from femforge.physical.units import Quantity, U, quantity


if TYPE_CHECKING:
    from build123d import Compound, Part

MM_TO_M = 0.001
"""build123d / gmsh author geometry in millimetres; the pipeline (and magpylib) work in SI metres."""


def _room_temperature() -> dict[str, Quantity]:
    """The default operating point (300 K) a magnet's remanence is evaluated at, matching `elmer.config.OperatingPoint`'s default."""
    return {"temperature": quantity(300, U.K)}


class MagnetSource(BaseModel):
    """
    One uniformly-magnetized cuboid magnet, in SI units, ready to hand to magpylib.

    `polarization` is J = Bᵣ · direction̂ (tesla) — the same physical drive Elmer applies via `Magnetization` (J = μ₀·M).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    center: Vec3Field
    """Magnet centre in the world frame (metres)."""

    size: Vec3Field
    """Full extents along x/y/z (metres) — magpylib's cuboid `dimension`."""

    polarization: Vec3Field
    """Magnetic polarization J = Bᵣ · direction̂ (tesla)."""


def material_for_label(label: str, materials: list[MaterialProperties]) -> MaterialProperties | None:
    """The material whose `tag` equals (casefold) the label's `Material` tag value, or None — mirrors `MeshEntity._match_material`."""
    value: str | None = parse_tags(label).get(TagKey.MATERIAL)
    if value is None:
        return None
    return next((material for material in materials if material.tag.casefold() == value.casefold()), None)


def magnetization_for_label(label: str, tags: list[EntityTag]) -> Magnetization | None:
    """The usable (directed) `Magnetization` carried by an `EntityTag` matching the label's tags, or None — mirrors `elmer.sim`'s magnet wiring."""
    matched: list[EntityTag] = [t for t in tags if t.matches(parse_tags(label))]
    conditions = conditions_for(matched, Physics.MAGNETOSTATICS, ConditionTarget.BODY)
    magnetizations: list[Magnetization] = [c for c in conditions if isinstance(c, Magnetization)]

    return next((m for m in magnetizations if m.direction is not None), None)


def magnet_source(label: str, center_m: Vec3, size_m: Vec3, domain: DomainConfig, *, at: Mapping[str, Quantity] | None = None) -> MagnetSource | None:
    """Build the `MagnetSource` for one body, or None if it is not a magnet; a magnet with no usable `Magnetization` direction raises `ValueError`.

    Args:
        label: The body's label, carrying its tags.
        center_m: The body's center, in metres.
        size_m: The body's axis-aligned extent, in metres.
        domain: The domain config whose materials and tags the label matches.
        at: The operating point the remanence is evaluated at; None is room temperature.
    """
    material: MaterialProperties | None = material_for_label(label, domain.materials)
    if material is None or not material.is_magnet:
        return None

    point: Mapping[str, Quantity] = at if at is not None else _room_temperature()
    magnitude: float | None = material.magnetic.magnetization_magnitude(at=point)  # |M| in A/m

    assert magnitude is not None  # is_magnet (remanence present) guarantees a magnitude; narrow the Optional

    magnetization: Magnetization | None = magnetization_for_label(label, domain.tags)

    if magnetization is None or magnetization.direction is None:
        raise ValueError(f"magnet body {label!r} (material {material.tag!r}) has no usable Magnetization direction tag")

    polarization: Vec3 = magnetization.direction * (magnitude * PERMEABILITY_OF_VACUUM)  # J = μ₀·|M| (tesla)

    return MagnetSource(center=center_m, size=size_m, polarization=polarization)


def sources_from_assembly(
    assembly: Compound, domain: DomainConfig, *, at: Mapping[str, Quantity] | None = None
) -> list[MagnetSource]:  # pragma: no cover - reads a live build123d assembly (OCC, no stubs); proven by the gated integration test
    """Walk a built build123d assembly and return a `MagnetSource` per magnet leaf, reading only its label and bounding box (mm → m).

    Args:
        assembly: The built assembly, whose magnets turn only by multiples of 90° and so stay axis-aligned.
        domain: The domain config whose materials and tags the leaf labels match.
        at: The operating point the remanence is evaluated at; None is room temperature.
    """
    sources: list[MagnetSource] = []

    for leaf in assembly.leaves:  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]  # build123d ships no stubs
        leaf: Part = leaf  # pyright: ignore[ reportUnknownVariableType]  # build123d ships no stubs
        box = leaf.located(leaf.global_location).bounding_box()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
        center = box.center()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        size = box.size  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        center_m: Vec3 = Vec3(float(center.X), float(center.Y), float(center.Z)) * MM_TO_M  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        size_m: Vec3 = Vec3(float(size.X), float(size.Y), float(size.Z)) * MM_TO_M  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]

        source: MagnetSource | None = magnet_source(str(leaf.label), center_m, size_m, domain, at=at)  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        if source is not None:
            sources.append(source)

    return sources
