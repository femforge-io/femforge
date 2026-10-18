"""Named bodies and boundaries of a run bundle, resolved to the mesh ids the VTU's ``GeometryIds`` cell array uses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Protocol, Sequence

from pydantic import BaseModel, Field

from femforge.common.files import BundleFile
from femforge.common.tags import TagDict, TagKey
from femforge.elmer.mesh_names import parse_mesh_names
from femforge.materials.properties import MaterialProperties
from femforge.meshing.boundary import AXIS_SUFFIX, BOUNDARY_SEP, EXTERNAL_SUFFIX
from femforge.meshing.naming import name_fragment
from femforge.physical.domain import EntityTag
from femforge.run.bundle import write_atomic
from femforge.run.electrical import read_boundary_names
from femforge.run.manifest import RunManifest


# Three id spaces coexist in a bundle and are deliberately never conflated:
#   mesh_id   -- ElmerGrid's mesh.names numbering; a body's is its cells' VTU GeometryIds value, a boundary's cells carry BC_ID_OFFSET + sif_index instead.
#   sif_index -- per-section 1..N block numbers: boundaries.yml for Boundary Condition blocks, components.yml for Component blocks.
# No attribute here is ever a bare ``id``.


class BodyRecord(BaseModel):
    """One body group in ``entities.json``: its Elmer name plus the material tag and merged selector tags it was grouped by."""

    name: str
    material: str | None = None
    tags: TagDict = Field(default_factory=dict)


class BoundaryRecord(BaseModel):
    """One boundary group in ``entities.json``: its Elmer name, the body names it borders, and the symmetry-axis and named-face flags."""

    name: str
    bodies: tuple[str, ...] = ()
    on_axis: bool = False
    named: bool = False


class EntitiesFile(BaseModel):
    """The ``entities.json`` sidecar: mesh id -> record for every body and boundary group, written post-reconcile."""

    bodies: dict[int, BodyRecord] = Field(default_factory=dict)
    boundaries: dict[int, BoundaryRecord] = Field(default_factory=dict)


class _MaterialLike(Protocol):
    """The slice of ``MaterialProperties`` the sidecar writer reads."""

    @property
    def tag(self) -> str: ...


class _TagLike(Protocol):
    """The slice of ``EntityTag`` the sidecar writer reads."""

    @property
    def selector(self) -> TagDict: ...


class _BodyGroupLike(Protocol):
    """The slice of ``meshing.groups.PhysicalGroup`` the sidecar writer reads."""

    @property
    def id(self) -> int: ...
    @property
    def name(self) -> str: ...
    @property
    def material(self) -> _MaterialLike: ...
    @property
    def tags(self) -> Sequence[_TagLike]: ...


class _BoundaryGroupLike(Protocol):
    """The slice of ``meshing.boundary.BoundaryGroup`` the sidecar writer reads."""

    @property
    def id(self) -> int: ...
    @property
    def name(self) -> str: ...
    @property
    def bodies(self) -> tuple[str, ...]: ...
    @property
    def on_axis(self) -> bool: ...
    @property
    def named(self) -> bool: ...


def write_entities(sim_dir: str | Path, physical_groups: Iterable[_BodyGroupLike], boundary_groups: Iterable[_BoundaryGroupLike]) -> Path:
    """Write ``entities.json`` mapping each group's reconciled mesh id to its named record; call after ``reconcile_ids``.

    Args:
        sim_dir: The run bundle directory.
        physical_groups: The body groups, carrying ElmerGrid's ids.
        boundary_groups: The boundary groups, carrying ElmerGrid's ids.
    """
    bodies: dict[int, BodyRecord] = {}
    for group in physical_groups:
        merged: TagDict = {}
        for tag in group.tags:
            merged.update(tag.selector)
        bodies[group.id] = BodyRecord(
            name=group.name,
            material=group.material.tag or None,
            tags=merged,
        )

    boundaries: dict[int, BoundaryRecord] = {
        group.id: BoundaryRecord(
            name=group.name,
            bodies=tuple(group.bodies),
            on_axis=group.on_axis,
            named=group.named,
        )
        for group in boundary_groups
    }

    path: Path = Path(sim_dir) / BundleFile.ENTITIES
    write_atomic(path, EntitiesFile(bodies=bodies, boundaries=boundaries).model_dump_json(indent=2))
    return path


class BoundaryKind(StrEnum):
    """What a boundary group separates: two bodies, a body's external shell, its trace on the 2D symmetry axis, or a tagged face."""

    INTERFACE = "Interface"
    """The interface between two bodies (``A__B``)."""

    EXTERNAL = "External"
    """A body's external shell (``A__EXT``)."""

    AXIS = "Axis"
    """A body's trace on the 2D symmetry axis (``A__AXIS``)."""

    NAMED = "Named"
    """A tagged face's own group, named after the face (``TIP``)."""


@dataclass(frozen=True)
class Body:
    """One named body group of a run."""

    name: str
    """The body group's name, shared by the mesh, the sif and ``entities.json``."""

    mesh_id: int
    """The body's ``GeometryIds`` value in the VTU."""

    material: str | None = None
    """The material tag the body was grouped by; None when unknown."""

    tags: Mapping[str, str] = field(default_factory=dict[str, str])
    """The selector tags the body was grouped by, merged into one mapping."""


@dataclass(frozen=True)
class Boundary:
    """One named boundary group of a run."""

    name: str
    """The boundary group's name, such as ``A__B`` or ``A__EXT``."""

    mesh_id: int
    """The boundary's id in ``mesh.names``; its VTU cells carry its ``sif_index`` plus an offset instead."""

    bodies: tuple[str, ...]
    """The names of the bodies the boundary borders."""

    kind: BoundaryKind
    """What the boundary separates."""

    sif_index: int | None = None
    """The boundary's Boundary Condition block number in the sif; None when no condition produced a block."""


def _name_of(body: Body | str) -> str:
    return body.name if isinstance(body, Body) else body


class Bodies:
    """Name-keyed collection of a run's bodies."""

    def __init__(self, bodies: Iterable[Body]) -> None:
        """Collect ``bodies`` by name."""
        self._by_name: dict[str, Body] = {body.name: body for body in bodies}

    def __iter__(self) -> Iterator[Body]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, body: object) -> bool:
        return (body.name if isinstance(body, Body) else body) in self._by_name

    def __getitem__(self, name: str) -> Body:
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"no body named {name!r}; available: {sorted(self._by_name)}") from None

    def get(self, name: str) -> Body | None:
        """The body by exact name, or None."""
        return self._by_name.get(name)

    def with_material(self, tag: str) -> list[Body]:
        """Every body whose material tag matches (case-insensitive)."""
        want: str = tag.casefold()
        return [body for body in self if body.material is not None and body.material.casefold() == want]

    def with_tag(self, key: TagKey | str, value: str) -> list[Body]:
        """Every body whose selector tags carry ``key=value``."""
        return [body for body in self if body.tags.get(str(key)) == value]


class Boundaries:
    """Name-keyed collection of a run's boundaries, with adjacency lookups powered by the ``A__B`` name convention."""

    def __init__(self, boundaries: Iterable[Boundary]) -> None:
        """Collect ``boundaries`` by name."""
        self._by_name: dict[str, Boundary] = {boundary.name: boundary for boundary in boundaries}

    def __iter__(self) -> Iterator[Boundary]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, boundary: object) -> bool:
        return (boundary.name if isinstance(boundary, Boundary) else boundary) in self._by_name

    def __getitem__(self, name: str) -> Boundary:
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"no boundary named {name!r}; available: {sorted(self._by_name)}") from None

    def get(self, name: str) -> Boundary | None:
        """The boundary by exact name, or None."""
        return self._by_name.get(name)

    def between(self, a: Body | str, b: Body | str) -> Boundary:
        """The interface boundary between two bodies, whichever order the mesher named it."""
        first, second = _name_of(a), _name_of(b)
        for candidate in (f"{first}{BOUNDARY_SEP}{second}", f"{second}{BOUNDARY_SEP}{first}"):
            found: Boundary | None = self._by_name.get(candidate)
            if found is not None:
                return found

        near: list[str] = [boundary.name for boundary in self.of_body(first) + self.of_body(second)]
        raise KeyError(f"no boundary between {first!r} and {second!r}; their boundaries: {sorted(set(near))}")

    def external(self, body: Body | str) -> Boundary:
        """The body's external shell boundary (``<BODY>__EXT``)."""
        return self[f"{_name_of(body)}{BOUNDARY_SEP}{EXTERNAL_SUFFIX}"]

    def face(self, name: str) -> Boundary:
        """The named group of the tagged face authored as ``name``, such as ``"RightFace"`` for ``RIGHTFACE``."""
        return self[name_fragment(name)]

    def axis(self, body: Body | str) -> Boundary | None:
        """The body's symmetry-axis boundary (``<BODY>__AXIS``), or None when it has none."""
        return self.get(f"{_name_of(body)}{BOUNDARY_SEP}{AXIS_SUFFIX}")

    def of_body(self, body: Body | str) -> list[Boundary]:
        """Every boundary bordering the body."""
        name: str = _name_of(body)
        return [boundary for boundary in self if name in boundary.bodies]


@dataclass(frozen=True)
class Entities:
    """A bundle's named bodies and boundaries."""

    bodies: Bodies
    """The named bodies."""

    boundaries: Boundaries
    """The named boundaries."""


def _boundary_kind(name: str, on_axis: bool, named: bool = False) -> BoundaryKind:
    """A boundary's kind from the recorded flags, else its name: an adjacency name always carries the separator."""
    if named or BOUNDARY_SEP not in name:
        return BoundaryKind.NAMED

    if on_axis or name.endswith(BOUNDARY_SEP + AXIS_SUFFIX):
        return BoundaryKind.AXIS

    if name.endswith(BOUNDARY_SEP + EXTERNAL_SUFFIX):
        return BoundaryKind.EXTERNAL

    return BoundaryKind.INTERFACE


def _bodies_from_name(name: str) -> tuple[str, ...]:
    """The bordering body names encoded in an ``A__B`` / ``A__EXT`` / ``A__AXIS`` boundary name; a named group's name encodes none."""
    if BOUNDARY_SEP not in name:
        return ()

    parts: list[str] = name.split(BOUNDARY_SEP)
    if parts[-1] in (EXTERNAL_SUFFIX, AXIS_SUFFIX):
        return tuple(parts[:-1])

    return tuple(parts)


def _sif_index_by_group(names_by_bc: Mapping[int, str], group_names: Iterable[str]) -> dict[str, int]:
    """Boundary group name -> BC block index; block names append condition kinds, so match exact first, then longest ``name_`` prefix."""
    ordered: list[str] = sorted(group_names, key=len, reverse=True)

    out: dict[str, int] = {}
    for index, block in names_by_bc.items():
        for name in ordered:
            if block == name or block.startswith(name + "_"):
                out.setdefault(name, index)
                break

    return out


def _has_segment(text: str, fragment: str) -> bool:
    """Whether ``fragment`` appears in ``text`` as whole ``_``-separated segments."""
    return text == fragment or text.startswith(fragment + "_") or text.endswith("_" + fragment) or f"_{fragment}_" in text


def _match_body(name: str, materials: Sequence[MaterialProperties], entity_tags: Sequence[EntityTag]) -> tuple[str | None, TagDict]:
    """Best-effort recovery of a body's material tag and selector tags from its lossy composed name, matched against the config vocabulary."""
    material: str | None = None
    remainder: str = name
    for candidate in sorted(materials, key=lambda m: len(name_fragment(m.tag)), reverse=True):
        fragment: str = name_fragment(candidate.tag)
        if not fragment:
            continue

        if name == fragment or name.startswith(fragment + "_"):
            material = candidate.tag
            remainder = name[len(fragment) + 1 :]
            break

    tags: TagDict = {}
    for tag in entity_tags:
        if tag.selector and _has_segment(remainder, name_fragment(tag.name)):
            tags.update(tag.selector)
    return material, tags


def _config_vocabulary(sim_dir: Path, manifest: RunManifest | None) -> tuple[list[MaterialProperties], list[EntityTag]]:
    """The material/tag vocabulary for best-effort name enrichment, from the given or on-disk manifest (empty when unavailable)."""
    if manifest is None:
        try:
            manifest = RunManifest.load(sim_dir)
        except Exception:
            return [], []

    return list(manifest.config.domain.materials), list(manifest.config.domain.tags)


def load_entities(sim_dir: str | Path, manifest: RunManifest | None = None) -> Entities:
    """Read a bundle's bodies/boundaries: ``entities.json`` when present, else ``mesh.names`` enriched from the manifest config, else empty."""
    path: Path = Path(sim_dir)

    boundaries_file: Path = path / BundleFile.BOUNDARIES
    names_by_bc: dict[int, str] = read_boundary_names(boundaries_file) if boundaries_file.is_file() else {}

    entities_file: Path = path / BundleFile.ENTITIES
    if entities_file.is_file():
        data: EntitiesFile = EntitiesFile.model_validate_json(entities_file.read_text(encoding="utf-8"))
        sif_by_group: dict[str, int] = _sif_index_by_group(names_by_bc, (record.name for record in data.boundaries.values()))
        bodies = Bodies(
            Body(
                name=record.name,
                mesh_id=mesh_id,
                material=record.material,
                tags=dict(record.tags),
            )
            for mesh_id, record in data.bodies.items()
        )
        boundaries = Boundaries(
            Boundary(
                name=record.name,
                mesh_id=mesh_id,
                bodies=tuple(record.bodies),
                kind=_boundary_kind(record.name, record.on_axis, record.named),
                sif_index=sif_by_group.get(record.name),
            )
            for mesh_id, record in data.boundaries.items()
        )
        return Entities(bodies=bodies, boundaries=boundaries)

    names_file: Path = path / BundleFile.MESH_NAMES
    if not names_file.is_file():
        return Entities(bodies=Bodies(()), boundaries=Boundaries(()))

    names = parse_mesh_names(names_file.read_text(encoding="utf-8"))
    materials, entity_tags = _config_vocabulary(path, manifest)

    body_list: list[Body] = []
    for name, mesh_id in names.bodies.items():
        material, tags = _match_body(name, materials, entity_tags)
        body_list.append(
            Body(
                name=name,
                mesh_id=mesh_id,
                material=material,
                tags=tags,
            )
        )

    sif_by_group = _sif_index_by_group(names_by_bc, names.boundaries)
    boundary_list: list[Boundary] = [
        Boundary(
            name=name,
            mesh_id=mesh_id,
            bodies=_bodies_from_name(name),
            kind=_boundary_kind(name, False),
            sif_index=sif_by_group.get(name),
        )
        for name, mesh_id in names.boundaries.items()
    ]
    return Entities(bodies=Bodies(body_list), boundaries=Boundaries(boundary_list))
