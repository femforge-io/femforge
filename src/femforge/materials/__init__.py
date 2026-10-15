"""Material library: the property model every material fills in, the concrete materials, and the tag-keyed registry the domain config resolves against."""

from __future__ import annotations

from femforge.materials.air import Air
from femforge.materials.copper import Copper
from femforge.materials.neodymium import N42, N52, Neodymium
from femforge.materials.pcb import FR4
from femforge.materials.properties import ElectricalProperties, MagneticProperties, MaterialProperties, MechanicalProperties, ThermalProperties
from femforge.materials.registry import all_materials, available_materials, material, register_material
from femforge.materials.steel import Steel_316, Steel_1080


__all__ = [
    "MaterialProperties",
    "MechanicalProperties",
    "ThermalProperties",
    "MagneticProperties",
    "ElectricalProperties",
    "Air",
    "Copper",
    "FR4",
    "Neodymium",
    "N42",
    "N52",
    "Steel_1080",
    "Steel_316",
    "material",
    "all_materials",
    "available_materials",
    "register_material",
]
