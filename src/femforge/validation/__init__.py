"""Cross-validation of the Elmer solve against magpylib's analytical fields, a sanity bound rather than an equality check."""

from __future__ import annotations

from femforge.validation.compare import FieldComparison, compare_fields
from femforge.validation.field import extract_vtu_b, gap_line, sample_magpylib_b, sample_vtu_b
from femforge.validation.sources import MagnetSource, magnet_source, magnetization_for_label, material_for_label, sources_from_assembly
from femforge.validation.viz import DEFAULT_PADDING, GridBoundsMode, MagpylibField, bounded_grid, bounds_of_sources, grid_spec, load_gmsh_mesh, magpylib_field_grid


__all__ = [
    "MagnetSource",
    "magnet_source",
    "sources_from_assembly",
    "material_for_label",
    "magnetization_for_label",
    "gap_line",
    "sample_magpylib_b",
    "sample_vtu_b",
    "extract_vtu_b",
    "FieldComparison",
    "compare_fields",
    "MagpylibField",
    "GridBoundsMode",
    "grid_spec",
    "bounds_of_sources",
    "bounded_grid",
    "magpylib_field_grid",
    "load_gmsh_mesh",
    "DEFAULT_PADDING",
]
