"""The simulation domain: the materials and per-region tag overrides shared by the mesh and FEA backends."""

from typing import Iterable, Mapping

from pydantic import Field
from rich.tree import Tree

from femforge.common.config import ConfigArea
from femforge.common.frame import Frame
from femforge.common.tags import TagDict, TagKey
from femforge.common.utils import COLORS
from femforge.elmer.physics import Physics
from femforge.materials.properties import MaterialProperties
from femforge.physical.conditions import Condition, ConditionTarget, ConditionUnion


def conditions_for(tags: Iterable["EntityTag"], physics: Physics | Iterable[Physics], target: ConditionTarget) -> list[Condition]:
    """The conditions on ``tags`` that belong to ``physics`` and attach to ``target``.

    Args:
        tags: The region overrides that match the region.
        physics: One [`Physics`][femforge.elmer.physics.Physics], or several, such as a coupled preset's constituents.
        target: Whether to collect body or boundary conditions.
    """
    wanted: frozenset[Physics] = frozenset({physics}) if isinstance(physics, Physics) else frozenset(physics)
    matching: list[Condition] = []
    for tag in tags:
        for condition in tag.conditions:
            if condition.physics in wanted and condition.target == target:
                matching.append(condition)
    return matching


class EntityTag(ConfigArea):
    """A per-region override matched to a mesh entity by its authored tags; carries the region's self-describing `conditions`."""

    selector: TagDict = Field(default_factory=dict)
    """Compound-key selector: a mesh entity matches when its tag dict contains ALL of these key=value pairs (an empty selector matches every entity)."""

    conditions: list[ConditionUnion] = Field(default_factory=list)
    """Composable, self-describing conditions (body forces / boundary conditions) this region carries."""

    mesh_size: float | None = None
    """`h` `[m]`: Target mesh size for this region, such as a finer air gap; None inherits ``MeshConfig.global_mesh_size``."""

    def matches(self, tags: Mapping[str, str]) -> bool:
        """Whether an entity's tag dict satisfies every selector pair."""
        return all(tags.get(key) == value for key, value in self.selector.items())

    @property
    def name(self) -> str:
        """Stable human-readable stem shared with gmsh/Elmer names: the uppercased part name for a lone ``Name`` selector, else sorted KeyValue pairs (``CurrentDirEntry_Phase2``)."""
        if self.selector.keys() == {TagKey.NAME}:
            return self.selector[TagKey.NAME].upper()
        return "_".join(f"{key}{value}" for key, value in sorted(self.selector.items())) or "Any"

    def print_tree(self, tree: Tree) -> None:
        """Add this tag and its conditions to ``tree``."""
        report: Tree = tree.add(COLORS.H2(f"Tag: {self.name}"))
        for condition in self.conditions:
            report.add(COLORS.Prop(type(condition).__name__, f"{condition}"))


class DomainConfig(ConfigArea):
    """The materials the mesher matches to STEP parts, plus the per-region `EntityTag` overrides (conditions / mesh refinement)."""

    materials: list[MaterialProperties] = Field(default_factory=list)
    """The candidate materials; the mesher matches each STEP entity to one by `tag`."""

    tags: list[EntityTag] = Field(default_factory=list)
    """The per-region overrides, matched to mesh entities by name; each carries its `conditions` and optional `mesh_size`."""

    def check_frame(self, frame: Frame) -> None:
        """Raise when a condition here cannot drive a ``frame`` solve."""
        for tag in self.tags:
            for condition in tag.conditions:
                condition.check_frame(frame)
