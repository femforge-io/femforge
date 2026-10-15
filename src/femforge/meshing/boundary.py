"""Boundary physical groups derived from body adjacency: the mesh surfaces (3D) or curves (2D) boundary conditions attach to without hand-labelling."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from femforge.meshing.geometry_utils import extract_entity_geometry


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
    A single gmsh boundary physical group derived from body adjacency — the one-dimension-down sibling of ``PhysicalGroup``, targeted by Elmer as ``Target Boundaries``.

    Names encode the bordering bodies: ``<BODYA>__<BODYB>`` (interface), ``<BODY>__EXT`` (external shell), or ``<BODY>__AXIS`` (symmetry axis).
    """

    def __init__(self, gmsh_id: int, name: str, bodies: tuple[str, ...], face_tags: list[int], dim: int = 2, on_axis: bool = False):
        """Record one boundary group.

        Args:
            gmsh_id: The gmsh physical-group tag.
            name: The compound name shared verbatim with Elmer.
            bodies: The one or two body group names the group borders.
            face_tags: The gmsh entity tags in the group.
            dim: Topological dimension of the group.
            on_axis: Whether every entity lies on the symmetry axis of a 2D axisymmetric model.
        """
        self.dim = dim
        """Topological dimension of the group -- one below the bodies it separates (2 = surface in a 3D model, 1 = curve in a 2D one), mirroring ``PhysicalGroup.dim``."""

        self.id = gmsh_id
        """The integer the sif targets this boundary by: the gmsh tag until [`reconcile_ids`][femforge.elmer.mesh_names.reconcile_ids] sets the id ElmerGrid assigned."""

        self.name = name
        """The compound ``BODYA__BODYB``, ``BODY__EXT`` or ``BODY__AXIS`` name, shared verbatim with Elmer."""

        self.bodies = bodies
        """The 1 (external or axis) or 2 (interface) body group names this boundary borders."""

        self.entity_tags = face_tags
        """The gmsh entity tags (surfaces in 3D, curves in 2D) that make up this group, mirroring ``PhysicalGroup.entity_tags``."""

        self.on_axis = on_axis
        """True when every entity of this single-body group lies on the x=0 symmetry axis of a 2D axisymmetric model."""

    @property
    def is_external(self) -> bool:
        """True when this is a body's true external (far-field) shell: bordered by exactly one body and not on the symmetry axis."""
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


def partition_boundary_faces(face_to_bodies: Mapping[int, set[str]], *, axis_faces: frozenset[int] = frozenset()) -> list[BoundaryPartition]:
    """Group every boundary face by the bodies it borders into sorted interface (``A__B``), external (``A__EXT``) and symmetry-axis (``A__AXIS``) partitions.

    A face in ``axis_faces`` becomes an axis partition only when it borders a single body.
    """
    grouped: dict[tuple[tuple[str, ...], bool], list[int]] = defaultdict(list)
    for face_tag, bodies in face_to_bodies.items():
        key = tuple(sorted(bodies))
        if not key:
            continue  # a face bounding no known body can't be named; skip rather than invent a group.
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
