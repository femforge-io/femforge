"""Per-entity geometry read from gmsh: bounding box, center of mass and volume."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntityGeometry:
    """Axis-aligned bounding box, center of mass and volume of one gmsh entity."""

    dim: int
    """Topological dimension of the entity."""

    tag: int
    """The gmsh entity tag."""

    bbox_min: tuple[float, float, float]
    """`[m]`: Lower corner of the axis-aligned bounding box."""

    bbox_max: tuple[float, float, float]
    """`[m]`: Upper corner of the axis-aligned bounding box."""

    center_of_mass: tuple[float, float, float]
    """`[m]`: Center of mass."""

    volume: float
    """`V` `[?]`: Volume, area or length of the entity by ``dim``, in m³, m² or m."""

    @property
    def extent(self) -> tuple[float, float, float]:
        """`[m]`: Size of the bounding box along each axis."""
        return (
            self.bbox_max[0] - self.bbox_min[0],
            self.bbox_max[1] - self.bbox_min[1],
            self.bbox_max[2] - self.bbox_min[2],
        )


def extract_entity_geometry(dim: int, tag: int) -> EntityGeometry:  # pragma: no cover - needs a live gmsh model
    """Read an entity's bounding box, center of mass and volume from a synchronized gmsh model."""
    import gmsh

    bb = gmsh.model.getBoundingBox(dim, tag)
    com = gmsh.model.occ.getCenterOfMass(dim, tag)
    volume: float = gmsh.model.occ.getMass(dim, tag)
    return EntityGeometry(
        dim=dim,
        tag=tag,
        bbox_min=(bb[0], bb[1], bb[2]),
        bbox_max=(bb[3], bb[4], bb[5]),
        center_of_mass=(com[0], com[1], com[2]),
        volume=volume,
    )
