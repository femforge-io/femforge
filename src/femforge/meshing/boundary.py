"""Boundary physical groups: the mesh surfaces (3D) or curves (2D) boundary conditions attach to, derived from body adjacency and tagged faces."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from femforge.meshing.geometry_utils import extract_entity_geometry


if TYPE_CHECKING:
    from femforge.common.vector import Vec3
    from femforge.meshing.groups import Bounds
    from femforge.physical.domain import EntityTag


# A double underscore never collides with the single underscores inside a body group's own MATERIAL_TAG name.
BOUNDARY_SEP = "__"
"""The separator between the body names in a boundary group's name, as in ``AIR__N52_MAG_N`` or ``AIR__EXT``."""

EXTERNAL_SUFFIX = "EXT"
"""The name suffix of a body's external shell group: the faces it shares with no other body."""

AXIS_SUFFIX = "AXIS"
"""The name suffix of a body's boundary group on the x=0 symmetry axis of a 2D axisymmetric model."""

# A module constant rather than a MeshConfig field, because every MeshConfig field enters cache_key.
AXIS_TOLERANCE_M = 1e-6
"""`[m]`: The largest distance from the symmetry axis at which an entity's bounding box still counts as on it."""


class BoundaryGroup:
    """
    A single gmsh boundary physical group — the one-dimension-down sibling of ``PhysicalGroup``, targeted by Elmer as ``Target Boundaries``.

    Names encode the bordering bodies, ``<BODYA>__<BODYB>``, ``<BODY>__EXT`` or ``<BODY>__AXIS``, or carry a tagged face's own name, such as ``TIP``.
    """

    def __init__(
        self,
        gmsh_id: int,
        name: str,
        bodies: tuple[str, ...],
        face_tags: list[int],
        dim: int = 2,
        on_axis: bool = False,
        tags: list[EntityTag] | None = None,
        measure: float | None = None,
        named: bool = False,
        centroid: Vec3 | None = None,
        normal: Vec3 | None = None,
        bbox: Bounds | None = None,
        length: float | None = None,
    ):
        """Record one boundary group.

        Args:
            gmsh_id: The gmsh physical-group tag.
            name: The compound name shared verbatim with Elmer.
            bodies: The one or two body group names the group borders.
            face_tags: The gmsh entity tags in the group.
            dim: Topological dimension of the group.
            on_axis: Whether every entity lies on the symmetry axis of a 2D axisymmetric model.
            tags: The region overrides the group's own entities match; None for an adjacency group.
            measure: The group's measured size; None for an adjacency group.
            named: Whether the group is a tagged face's own group.
            centroid: The centre of mass of the group; None until measured.
            normal: The mean outward normal out of the first bordering body; None until measured or when ill defined.
            bbox: The bounds of the mesh nodes of the group; None until measured.
            length: The summed curve length of a group of a 2D model; None in 3D or until measured.
        """
        self.dim = dim
        """Topological dimension of the group -- one below the bodies it separates (2 = surface in a 3D model, 1 = curve in a 2D one), mirroring ``PhysicalGroup.dim``."""

        self.id = gmsh_id
        """The integer the sif targets this boundary by: the gmsh tag until [`reconcile_ids`][femforge.elmer.mesh_names.reconcile_ids] sets the id ElmerGrid assigned."""

        self.name = name
        """The compound ``BODYA__BODYB``, ``BODY__EXT`` or ``BODY__AXIS`` name, or a named group's face name, shared verbatim with Elmer."""

        self.bodies = bodies
        """The 1 (external or axis) or 2 (interface) body group names this boundary borders."""

        self.entity_tags = face_tags
        """The gmsh entity tags (surfaces in 3D, curves in 2D) that make up this group, mirroring ``PhysicalGroup.entity_tags``."""

        self.on_axis = on_axis
        """True when every entity of this single-body group lies on the x=0 symmetry axis of a 2D axisymmetric model."""

        self.tags: list[EntityTag] = tags or []
        """The region overrides the group's own entities match, whose boundary conditions land here after the bordering bodies' ones."""

        self.measure = measure
        """`A` `[m²]`: Measured size of a named boundary group, area in 3D and length times one metre of depth in 2D; None for adjacency groups."""

        self.named = named
        """True for a tagged face's own group."""

        self.centroid = centroid
        """`[m]`: The centre of mass of the group in the meshed frame; None until measured."""

        self.normal = normal
        """`[-]`: The mean outward normal out of ``bodies[0]``, into ``bodies[1]`` for an interface; None until measured or when the faces cancel."""

        self.bbox = bbox
        """The bounds of the mesh nodes of the group in the meshed frame; None until measured."""

        self.length = length
        """`L` `[m]`: The summed length of the boundary curves of a 2D model; None in 3D or until measured."""

    @property
    def is_external(self) -> bool:
        """True when the group is bordered by exactly one body and not on the symmetry axis: a body's external shell or a named face on it."""
        return len(self.bodies) == 1 and not self.on_axis

    @property
    def is_axis(self) -> bool:
        """True when this is a body's symmetry-axis boundary (the ``<BODY>__AXIS`` group of a 2D axisymmetric model)."""
        return self.on_axis

    def __repr__(self) -> str:
        return f"BoundaryGroup(id={self.id}, name={self.name!r}, bodies={self.bodies!r})"

    def physical_group_name(self) -> str:
        """The stable name the mesher and the sif writer both reference this boundary by, as [`MeshGroupSource`][femforge.common.protocols.MeshGroupSource] requires."""
        return self.name


@dataclass(frozen=True)
class BoundaryPartition:
    """One planned boundary group: the output of the pure adjacency partition, before gmsh assigns it a physical-group id."""

    name: str
    """The group name: ``A__B`` for an interface, ``A__EXT`` for an external shell, ``A__AXIS`` for a symmetry-axis boundary."""

    bodies: tuple[str, ...]
    """The one or two body group names the faces border, sorted."""

    face_tags: tuple[int, ...]
    """The boundary entity tags in the group, sorted."""

    on_axis: bool = False
    """Whether the group is a single body's boundary on the symmetry axis."""

    named: bool = False
    """Whether the group is a tagged face's own group."""


def partition_boundary_faces(
    face_to_bodies: Mapping[int, set[str]],
    *,
    axis_faces: frozenset[int] = frozenset(),
    named_faces: Mapping[int, str] | None = None,
) -> list[BoundaryPartition]:
    """Group every boundary face by the bodies it borders into sorted interface (``A__B``), external (``A__EXT``) and symmetry-axis (``A__AXIS``) partitions, then one partition per named face group.

    Args:
        face_to_bodies: Each boundary face mapped to the bodies it bounds.
        axis_faces: The faces on the symmetry axis; one becomes an axis partition only when it borders a single body.
        named_faces: Each tagged face mapped to its group name; such a face leaves its adjacency group for the named one, sorted after the adjacency groups.
    """
    names: Mapping[int, str] = named_faces or {}

    grouped: dict[tuple[tuple[str, ...], bool], list[int]] = defaultdict(list)
    named_tags: dict[str, list[int]] = defaultdict(list)
    named_bodies: dict[str, set[str]] = defaultdict(set)
    for face_tag, bodies in face_to_bodies.items():
        key = tuple(sorted(bodies))
        if not key:
            continue  # a face bounding no known body can't be named; skip rather than invent a group.

        if face_tag in names:
            named_tags[names[face_tag]].append(face_tag)
            named_bodies[names[face_tag]].update(key)
            continue
        on_axis: bool = len(key) == 1 and face_tag in axis_faces
        grouped[(key, on_axis)].append(face_tag)

    partitions: list[BoundaryPartition] = []
    for bodies, on_axis in sorted(grouped):
        if on_axis:
            name = f"{bodies[0]}{BOUNDARY_SEP}{AXIS_SUFFIX}"
        elif len(bodies) == 1:
            name = f"{bodies[0]}{BOUNDARY_SEP}{EXTERNAL_SUFFIX}"
        else:
            name = BOUNDARY_SEP.join(bodies)

        face_tags = tuple(sorted(grouped[(bodies, on_axis)]))
        partition = BoundaryPartition(
            name=name,
            bodies=bodies,
            face_tags=face_tags,
            on_axis=on_axis,
        )
        partitions.append(partition)

    for name in sorted(named_tags):
        partition = BoundaryPartition(
            name=name,
            bodies=tuple(sorted(named_bodies[name])),
            face_tags=tuple(sorted(named_tags[name])),
            named=True,
        )
        partitions.append(partition)

    return partitions


def read_face_adjacency(
    body_groups: Iterable[tuple[str, Iterable[int]]],
    body_dim: int = 3,
) -> dict[int, set[str]]:  # pragma: no cover - needs a live gmsh model (exercised by the gated integration tests)
    """Map each boundary entity of the ``body_dim`` body groups to the names of the bodies it bounds, read from a synchronized gmsh model."""
    import gmsh

    face_to_bodies: dict[int, set[str]] = defaultdict(set)
    for name, volume_tags in body_groups:
        for volume_tag in volume_tags:
            # combined=False keeps each body's own boundary (so a shared face appears under both bodies); oriented=False yields positive tags.
            for _, face_tag in gmsh.model.getBoundary([(body_dim, volume_tag)], combined=False, oriented=False):
                face_to_bodies[face_tag].add(name)
    return dict(face_to_bodies)


def axis_entity_tags(
    tags: Iterable[int],
    dim: int,
    tol: float = AXIS_TOLERANCE_M,
) -> frozenset[int]:  # pragma: no cover - needs a live gmsh model (exercised by the gated integration tests)
    """The ``tags`` whose bounding box lies on the x=0 symmetry axis, read from a live gmsh model.

    Args:
        tags: The gmsh entity tags to test.
        dim: Topological dimension of the entities.
        tol: The largest distance from the axis, in metres, that still counts as on it.
    """
    on_axis: set[int] = set()
    for tag in tags:
        geometry = extract_entity_geometry(dim, tag)
        if abs(geometry.bbox_min[0]) <= tol and abs(geometry.bbox_max[0]) <= tol:
            on_axis.add(tag)
    return frozenset(on_axis)
