"""The run side of the built-in artifacts: rebinding a cached mesh's groups, the bundle file policy, and rehydration."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Iterable

from femforge.artifacts.cache import ArtifactCache, ArtifactHandle
from femforge.artifacts.kind import BuiltinKind
from femforge.artifacts.store import ArtifactError, ArtifactMissing
from femforge.common.files import RunFile
from femforge.common.tags import TagDict
from femforge.materials.properties import MaterialProperties
from femforge.meshing.boundary import BoundaryGroup
from femforge.meshing.groups import BodyGroupRecord, BoundaryGroupRecord, MeshGroupsFile, PhysicalGroup
from femforge.meshing.naming import compose_group_name
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.run.kinds import ELMER_MESH, STEP, MeshSpec, StepSpec
from femforge.run.manifest import RunManifest


__all__ = [
    "BodyGroupRecord",
    "BoundaryGroupRecord",
    "BundlePolicy",
    "MeshGroupsFile",
    "MeshRebindError",
    "bundle_files",
    "prune_store_sourced",
    "rebind_groups",
    "rehydrate_bundle",
]


class MeshRebindError(ArtifactError):
    """A cached mesh cannot be bound to the current config's domain."""


def _material_for(record: BodyGroupRecord, domain: DomainConfig) -> MaterialProperties:
    wanted: str = record.material.casefold()
    for material in domain.materials:
        if material.tag.casefold() == wanted:
            return material

    raise MeshRebindError(f"cached mesh body {record.name!r} needs material tag {record.material!r}, which the domain lacks")


def _tags_for(group: str, selectors: list[TagDict], domain: DomainConfig) -> list[EntityTag]:
    tags: list[EntityTag] = []
    for selector in selectors:
        match: EntityTag | None = next((tag for tag in domain.tags if dict(tag.selector) == selector), None)
        if match is None:
            raise MeshRebindError(f"cached mesh {group} was grouped by selector {selector}, which the domain lacks")

        tags.append(match)
    return tags


def rebind_groups(groups: MeshGroupsFile, domain: DomainConfig) -> tuple[list[PhysicalGroup], list[BoundaryGroup]]:
    """Rebuild a cached mesh's groups against this config's domain, matching materials by tag and ``EntityTag`` objects by selector; ids stay placeholders until ``reconcile_ids``."""
    bodies: list[PhysicalGroup] = []
    for record in groups.bodies:
        material: MaterialProperties = _material_for(record, domain)
        tags: list[EntityTag] = _tags_for(f"body {record.name!r}", record.selectors, domain)
        expected: str = compose_group_name(material, tags, warn=lambda _message: None, authored=record.authored)
        if expected != record.name:
            raise MeshRebindError(f"cached mesh body {record.name!r} would be named {expected!r} by the current domain")

        bodies.append(
            PhysicalGroup(
                dim=record.dim,
                gmsh_id=0,
                name=record.name,
                material=material,
                tags=tags,
                entity_tags=list(record.entity_tags),
                authored=record.authored,
                centroid=record.centroid,
                bbox=record.bbox,
                measure=record.measure,
            )
        )

    boundaries: list[BoundaryGroup] = []
    for record in groups.boundaries:
        boundaries.append(
            BoundaryGroup(
                gmsh_id=0,
                name=record.name,
                bodies=tuple(record.bodies),
                face_tags=list(record.face_tags),
                dim=record.dim,
                on_axis=record.on_axis,
                tags=_tags_for(f"boundary {record.name!r}", record.selectors, domain),
                measure=record.measure,
                named=record.named,
                centroid=record.centroid,
                normal=record.normal,
                bbox=record.bbox,
                length=record.length,
            )
        )

    return bodies, boundaries


class BundlePolicy(StrEnum):
    """Which bundle files a published run bundle carries."""

    CORE = "core"
    """Manifest, result, sidecars, sif, scalars, logs, geometry: everything but fields and meshes."""

    FIELDS = "fields"
    """Core plus the VTU output."""

    FULL = "full"
    """Every bundle file, meshes and partitioning included."""

    @property
    def classes(self) -> tuple[RunFile, ...]:
        """The retention classes this policy publishes."""
        core: tuple[RunFile, ...] = (RunFile.BUNDLE, RunFile.SIDECARS, RunFile.SIF, RunFile.SCALARS, RunFile.LOGS, RunFile.GEOMETRY)
        match self:
            case BundlePolicy.CORE:
                return core
            case BundlePolicy.FIELDS:
                return (*core, RunFile.FIELDS)
            case BundlePolicy.FULL:
                return tuple(file_class for file_class in RunFile if file_class is not RunFile.RUN_DIR)


def bundle_files(sim_dir: str | Path, policy: BundlePolicy, *, exclude: Iterable[str] = ()) -> list[str]:
    """The bundle-relative posix paths ``policy`` publishes from ``sim_dir``.

    Args:
        sim_dir: The run bundle directory.
        policy: Which bundle files to publish.
        exclude: Bundle-relative paths to leave out, such as the store-sourced inputs.
    """
    bundle: Path = Path(sim_dir)
    skip: frozenset[str] = frozenset(exclude)
    names: set[str] = set()
    for file_class in policy.classes:
        for pattern in file_class.patterns:
            for match in bundle.glob(pattern):
                candidates: Iterable[Path] = (match,)
                if match.is_dir():
                    candidates = (child for child in match.rglob("*") if child.is_file())

                for path in candidates:
                    relative: str = path.relative_to(bundle).as_posix()
                    if relative not in skip and not relative.endswith(".tmp"):
                        names.add(relative)
    return sorted(names)


def rehydrate_bundle(sim_dir: str | Path, cache: ArtifactCache) -> list[ArtifactHandle[StepSpec] | ArtifactHandle[MeshSpec]]:
    """Fetch every store-sourced input the manifest names back into the bundle."""
    bundle: Path = Path(sim_dir)
    handles: list[ArtifactHandle[StepSpec] | ArtifactHandle[MeshSpec]] = []
    for ref in RunManifest.load(bundle).artifacts:
        match ref.kind:
            case BuiltinKind.STEP:
                handles.append(cache.rehydrate(ref, bundle, STEP))
            case BuiltinKind.ELMER_MESH:
                handles.append(cache.rehydrate(ref, bundle, ELMER_MESH))
            case _:
                raise ArtifactMissing(f"{ref.kind}/{ref.key[:12]}: not a kind the driver materializes")
    return handles


def prune_store_sourced(sim_dir: str | Path) -> list[Path]:
    """Remove the files the manifest says came from the store; returns what was removed."""
    bundle: Path = Path(sim_dir)
    removed: list[Path] = []
    for relative in sorted(RunManifest.load(bundle).cached_inputs()):
        path: Path = bundle / relative
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed
