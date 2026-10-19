"""The gmsh-free mesh group records: the body groups a mesher builds, their log summaries, and the ``groups.json`` a cached mesh carries."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from femforge.common.files import BundleFile
from femforge.common.tags import TagDict
from femforge.common.vector import Direction, DirectionField, Vec3, Vec3Field
from femforge.materials.properties import MaterialProperties
from femforge.meshing.boundary import BoundaryGroup
from femforge.physical.domain import EntityTag


class Bounds(BaseModel):
    """An axis-aligned bounding box in metres."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    low: Vec3Field
    """`[m]`: The lower corner."""

    high: Vec3Field
    """`[m]`: The upper corner."""

    @property
    def diagonal(self) -> float:
        """`D` `[m]`: The length of the box diagonal."""
        return (self.high - self.low).magnitude()


class PhysicalGroup:
    """A single gmsh physical group plus the metadata to reconstruct the matching Elmer body; both sides share the same ``name`` and integer ``id``."""

    def __init__(
        self,
        dim: int,
        gmsh_id: int,
        name: str,
        material: MaterialProperties,
        tags: list[EntityTag],
        entity_tags: list[int],
        authored: str | None = None,
        centroid: Vec3 | None = None,
        bbox: Bounds | None = None,
        measure: float | None = None,
    ) -> None:
        """Record one body group.

        Args:
            dim: Topological dimension of the group.
            gmsh_id: The gmsh physical-group tag.
            name: The group name shared verbatim with Elmer.
            material: The material of every entity in the group.
            tags: The ``EntityTag`` objects the group matched.
            entity_tags: The gmsh entity tags in the group.
            authored: The ``Name`` tag the entities were authored with, or None when the name is composed.
            centroid: The centre of mass of the group; None until measured.
            bbox: The bounds of the mesh nodes of the group; None until measured.
            measure: The volume of the group, or its section area times one metre in 2D; None until measured.
        """
        self.dim = dim
        """Topological dimension of the group (3 = volume in a 3D model, 2 = surface in a 2D one)."""

        self.id = gmsh_id
        """The integer the sif targets this region by: the gmsh tag until [`reconcile_ids`][femforge.elmer.mesh_names.reconcile_ids] sets the id ElmerGrid assigned."""

        self.name = name
        """The authored part name, else the compound MATERIAL_TAG1_TAG2 name; shared verbatim with Elmer."""

        self.authored = authored
        """The ``Name`` tag the group's entities were authored with, or None when the name is composed."""

        self.material = material
        """The material of every entity in the group."""

        self.tags = tags
        """EntityTag objects matched to this group (carry per-region overrides: fixed temperature, heat flux, magnetization direction)."""

        self.entity_tags = entity_tags
        """The gmsh entity tags (volumes in 3D, surfaces in 2D) that make up this group."""

        self.centroid = centroid
        """`[m]`: The centre of mass of the group in the meshed frame; None until measured."""

        self.bbox = bbox
        """The bounds of the mesh nodes of the group in the meshed frame; None until measured."""

        self.measure = measure
        """`V` `[m³]`: The volume of the group, or its section area times one metre of depth in 2D; None until measured."""

    def __repr__(self) -> str:
        return f"PhysicalGroup(id={self.id}, name={self.name!r}, material={self.material.name!r})"

    def physical_group_name(self) -> str:
        """The stable name the mesher and the sif writer both reference this region by, as [`MeshGroupSource`][femforge.common.protocols.MeshGroupSource] requires."""
        return self.name


def raise_on_duplicate_names(groups: Sequence[PhysicalGroup]) -> None:
    """Raise when two body groups share a name, e.g. one ``Name`` authored on parts of two materials."""
    by_name: dict[str, list[PhysicalGroup]] = {}
    for group in groups:
        by_name.setdefault(group.name, []).append(group)

    clashes = {name: [group.material.tag for group in clashing] for name, clashing in by_name.items() if len(clashing) > 1}
    if clashes:
        detail = "; ".join(f"{name!r} on materials {materials}" for name, materials in clashes.items())
        raise ValueError(f"Body names must be unique: {detail}. Parts share a name only when they share a material and matched tags.")


def format_physical_groups(groups: Sequence[PhysicalGroup], *, total_entities: int) -> str:
    """Multi-line material → tag-group → physical-group summary for one log record (embeds newlines)."""
    by_material: dict[str, list[PhysicalGroup]] = {}
    for group in groups:
        by_material.setdefault(group.material.name, []).append(group)

    # (label, group) rows; a None group marks a material header above its tag groups.
    rows: list[tuple[str, PhysicalGroup | None]] = []
    for material_name, material_groups in by_material.items():
        if len(material_groups) == 1 and not material_groups[0].tags:
            rows.append((f"  {material_name}", material_groups[0]))
            continue

        rows.append((f"  {material_name}", None))
        for group in material_groups:
            label = ", ".join(tag.name for tag in group.tags) or "(untagged)"
            rows.append((f"    {label}", group))

    label_width: int = max((len(label) for label, group in rows if group is not None), default=0)

    lines: list[str] = ["", f"{total_entities} Entities → {len(groups)} Physical Groups:"]
    for label, group in rows:
        if group is None:
            lines.append(label)
        else:
            lines.append(f"{label:<{label_width}} {len(group.entity_tags):>4}  → {group.name}")

    return "\n".join(lines)


def format_boundary_groups(groups: Sequence[BoundaryGroup]) -> str:
    """Multi-line boundary-group summary for one log record (embeds newlines)."""
    lines: list[str] = ["", f"{len(groups)} Boundary Groups:"]
    max_group_name = max((len(group.name) for group in groups), default=0)
    for group in groups:
        line: str = f"  {group.name:<{max_group_name}} {len(group.entity_tags):>4}  → id {group.id}"
        if group.named and group.measure is not None:
            line += f"  ({group.measure:.4g} m²)"

        lines.append(line)

    return "\n".join(lines)


class _MaterialSource(Protocol):
    @property
    def tag(self) -> str: ...


class _TagSource(Protocol):
    @property
    def selector(self) -> TagDict: ...


class _BodySource(Protocol):
    """The slice of a body group (``PhysicalGroup``) the record captures."""

    @property
    def dim(self) -> int: ...
    @property
    def name(self) -> str: ...
    @property
    def material(self) -> _MaterialSource: ...
    @property
    def tags(self) -> Sequence[_TagSource]: ...
    @property
    def entity_tags(self) -> Sequence[int]: ...
    @property
    def authored(self) -> str | None: ...
    @property
    def centroid(self) -> Vec3 | None: ...
    @property
    def bbox(self) -> Bounds | None: ...
    @property
    def measure(self) -> float | None: ...


class _BoundarySource(Protocol):
    """The slice of ``BoundaryGroup`` the record captures."""

    @property
    def dim(self) -> int: ...
    @property
    def name(self) -> str: ...
    @property
    def bodies(self) -> tuple[str, ...]: ...
    @property
    def entity_tags(self) -> Sequence[int]: ...
    @property
    def on_axis(self) -> bool: ...
    @property
    def named(self) -> bool: ...
    @property
    def measure(self) -> float | None: ...
    @property
    def tags(self) -> Sequence[_TagSource]: ...
    @property
    def centroid(self) -> Vec3 | None: ...
    @property
    def normal(self) -> Vec3 | None: ...
    @property
    def bbox(self) -> Bounds | None: ...
    @property
    def length(self) -> float | None: ...


class BodyGroupRecord(BaseModel):
    """One body group of a cached mesh: what it takes to rebuild its ``PhysicalGroup`` against a config."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    dim: int
    """Topological dimension of the group."""

    name: str
    """The group name shared verbatim with Elmer."""

    material: str
    """The material tag."""

    selectors: list[TagDict] = Field(default_factory=list)
    """The selectors of the tags the group matched, in domain order."""

    entity_tags: list[int] = Field(default_factory=list)
    """The gmsh entity tags in the group."""

    authored: str | None = None
    """The ``Name`` tag the group was named by, or None when its name is composed."""

    centroid: Vec3Field | None = None
    """`[m]`: The centre of mass of the group in the meshed frame; None for a mesh from before groups were measured."""

    bbox: Bounds | None = None
    """The bounds of the mesh nodes of the group in the meshed frame; None for a mesh from before groups were measured."""

    measure: float | None = None
    """`V` `[m³]`: The volume of the group, or its section area times one metre of depth in 2D; None for a mesh from before groups were measured."""


class BoundaryGroupRecord(BaseModel):
    """One boundary group of a cached mesh."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    dim: int
    """Topological dimension of the group."""

    name: str
    """The compound boundary name, or a named group's face name, shared verbatim with Elmer."""

    bodies: tuple[str, ...] = ()
    """The one or two body group names the boundary borders."""

    face_tags: list[int] = Field(default_factory=list)
    """The gmsh entity tags in the group."""

    on_axis: bool = False
    """Whether the group lies on the symmetry axis of a 2D axisymmetric model."""

    named: bool = False
    """Whether the group is a tagged face's own group."""

    selectors: list[TagDict] = Field(default_factory=list)
    """The selectors of the tags a named group's faces matched, in domain order."""

    measure: float | None = None
    """`A` `[m²]`: Measured size of a named group, area in 3D and length times one metre of depth in 2D; None for adjacency groups."""

    centroid: Vec3Field | None = None
    """`[m]`: The centre of mass of the group in the meshed frame; None for a mesh from before groups were measured."""

    normal: DirectionField | None = None
    """`[-]`: The mean outward normal out of the first bordering body; None when the faces cancel, as on a closed shell, or their side is unknown."""

    bbox: Bounds | None = None
    """The bounds of the mesh nodes of the group in the meshed frame; None for a mesh from before groups were measured."""

    length: float | None = None
    """`L` `[m]`: The summed length of the boundary curves of a 2D model; None in 3D."""


class MeshGroupsFile(BaseModel):
    """``groups.json``: the group records and element count a mesh artifact carries beside ElmerGrid's files."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    bodies: list[BodyGroupRecord] = Field(default_factory=list)
    """The body group records, in mesher order."""

    boundaries: list[BoundaryGroupRecord] = Field(default_factory=list)
    """The boundary group records, in mesher order."""

    element_count: int = 0
    """Elements of the body dimension in the mesh."""

    @classmethod
    def capture(cls, physical_groups: Iterable[_BodySource], boundary_groups: Iterable[_BoundarySource], element_count: int) -> MeshGroupsFile:
        """Record the mesher's groups.

        Args:
            physical_groups: The body groups, in mesher order.
            boundary_groups: The boundary groups, in mesher order.
            element_count: Elements of the body dimension in the mesh.
        """
        return cls(
            bodies=[
                BodyGroupRecord(
                    dim=group.dim,
                    name=group.name,
                    material=group.material.tag,
                    selectors=[dict(tag.selector) for tag in group.tags],
                    entity_tags=list(group.entity_tags),
                    authored=group.authored,
                    centroid=group.centroid,
                    bbox=group.bbox,
                    measure=group.measure,
                )
                for group in physical_groups
            ],
            boundaries=[
                BoundaryGroupRecord(
                    dim=group.dim,
                    name=group.name,
                    bodies=tuple(group.bodies),
                    face_tags=list(group.entity_tags),
                    on_axis=group.on_axis,
                    named=group.named,
                    selectors=[dict(tag.selector) for tag in group.tags],
                    measure=group.measure,
                    centroid=group.centroid,
                    normal=None if group.normal is None else Direction(group.normal.x, group.normal.y, group.normal.z),
                    bbox=group.bbox,
                    length=group.length,
                )
                for group in boundary_groups
            ],
            element_count=element_count,
        )

    @classmethod
    def load(cls, sim_dir: str | Path) -> MeshGroupsFile:
        """Read ``groups.json`` from ``sim_dir``."""
        return cls.model_validate_json((Path(sim_dir) / BundleFile.GROUPS).read_text(encoding="utf-8"))

    def write(self, sim_dir: str | Path) -> Path:
        """Write ``groups.json`` into ``sim_dir`` and return its path."""
        path: Path = Path(sim_dir) / BundleFile.GROUPS
        _ = path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path
