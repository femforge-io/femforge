"""Tagged CAD infrastructure: the ``GeometryArea`` interface, tagged build123d shapes, cross-sections, STEP import and the winding packer."""

from __future__ import annotations

from femforge.geometry.build123d_geometry import Build123dGeometry
from femforge.geometry.config import GeometryArea


__all__ = ["Build123dGeometry", "GeometryArea"]
