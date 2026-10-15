"""Resolves the gmsh entities of an imported STEP against the domain's materials and region tags."""

import gmsh
from rich.tree import Tree

from femforge.common.tags import HasTags, TagKey, parse_tags
from femforge.common.utils import COLORS
from femforge.common.vector import Box, Vec3
from femforge.materials.properties import MaterialProperties
from femforge.physical.domain import DomainConfig, EntityTag


class MeshEntity(HasTags):
    """One gmsh body (a volume in 3D, a surface in 2D), resolved to a material and per-region EntityTags via the key=value tags in its STEP name."""

    def __init__(self, dim: int, tag: int, domain: DomainConfig):  # pragma: no cover - reads/writes entity names on a live gmsh model
        """Read the entity's name from gmsh, match it against ``domain``, and write the name back in canonical key=value form.

        Args:
            dim: Topological dimension of the entity.
            tag: The gmsh entity tag.
            domain: The materials and region tags the entity is matched against.
        """
        self.dim = dim
        self.tag = tag
        self.domain = domain
        """The materials + region tags this entity is matched against; the only config slice MeshEntity needs."""

        self.name_raw: str = gmsh.model.getEntityName(dim, tag)
        self.set_tags(parse_tags(self.name_raw))
        self.material: MaterialProperties | None = self._match_material()
        self.name = self.tags_str()

        # Normalize the name to the canonical key=value form. Idempotent by construction: the mesher resolves
        # entities both before AND after the conformal fragment, so the rewritten name must re-parse to the
        # same tags (a non-pair format here silently strips every tag on the second pass).
        gmsh.model.setEntityName(dim, tag, self.name)

    def print_tree(self, tree: Tree):
        """Add this entity's tag, raw name, dimension, bounding box, material and matched tags to ``tree``."""

        report = tree.add(COLORS.H1(self.name))
        report.add(COLORS.Prop("Tag", f"{self.tag}"))
        report.add(COLORS.Prop("Name (Raw)", self.name_raw))
        report.add(COLORS.Prop("Dim", str(self.dim)))
        report.add(COLORS.Prop("Bounding Box", f"{self.bounding_box:.3f}"))

        if self.material:
            report.add(COLORS.Prop("Material", self.material.name))

        for tag in self.entity_tags:
            tag.print_tree(report)

    def _match_material(self) -> MaterialProperties | None:
        """The domain material whose ``tag`` equals this entity's ``Material`` tag value (casefold), or None."""
        value = self.tags.get(TagKey.MATERIAL)
        if value is None:
            return None

        matches = (material for material in self.domain.materials if material.tag.casefold() == value.casefold())
        return next(matches, None)

    @property
    def entity_tags(self) -> list[EntityTag]:
        """The domain EntityTags whose selectors this entity's tags satisfy, in domain order."""
        return [tag for tag in self.domain.tags if tag.matches(self.tags)]

    @property
    def bounding_box(self) -> Box:  # pragma: no cover - reads extents from a live gmsh model
        """The axis-aligned bounding box of this entity in the gmsh model."""
        elements = gmsh.model.getBoundingBox(self.dim, self.tag)
        min_vec = Vec3(*elements[0:3])
        max_vec = Vec3(*elements[3:6])
        return Box(min_vec, max_vec)
