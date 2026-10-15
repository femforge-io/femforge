"""The single material registry, keyed by each material's ``tag`` — the one source both the CAD/mesh stage and the sif writer read."""

from __future__ import annotations

from femforge.materials.air import Air
from femforge.materials.copper import Copper
from femforge.materials.neodymium import N42, N52
from femforge.materials.pcb import FR4
from femforge.materials.properties import MaterialProperties
from femforge.materials.steel import Steel_316, Steel_1080


# Each value is a zero-arg factory so callers get a fresh, independent instance (materials are mutable Pydantic models; a shared instance would let an edit leak across configs).
_FACTORIES: dict[str, type[MaterialProperties]] = {
    "N52": N52,
    "N42": N42,
    "Air": Air,
    "FR4": FR4,
    "Copper": Copper,
    "Steel1080": Steel_1080,
    "Steel316": Steel_316,
}


def available_materials() -> list[str]:
    """The tags of every registered material."""
    return list(_FACTORIES)


def material(tag: str) -> MaterialProperties:
    """Construct a fresh registered material by its tag (e.g. ``"N52"``)."""
    try:
        return _FACTORIES[tag]()
    except KeyError:
        raise KeyError(f"unknown material tag {tag!r}; registered: {available_materials()}") from None


def all_materials() -> list[MaterialProperties]:
    """A fresh instance of every registered material."""
    return [factory() for factory in _FACTORIES.values()]


def register_material(tag: str, factory: type[MaterialProperties]) -> None:
    """Add or replace the material registered under ``tag``."""
    _FACTORIES[tag] = factory
