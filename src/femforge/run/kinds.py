"""Run identity and the built-in artifact kinds: the STEP a geometry builds, the Elmer mesh a config meshes, and the bundle a run leaves."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, SerializeAsAny

from femforge.artifacts.keys import artifact_key, sha256_json, spec_payload
from femforge.artifacts.kind import ArtifactKind, BuiltinKind, Produced
from femforge.common.files import BundleFile
from femforge.common.pydantic import HashExclude
from femforge.config import SimulationConfig
from femforge.frames import FrameUnion
from femforge.geometry.config import GeometryArea
from femforge.meshing.gmsh_options import GmshOptions
from femforge.meshing.spec import MeshSpec, TagSelectorSpec
from femforge.physical.units import CANONICAL_UNITS, Quantity, quantity


RUN_PRODUCER_VERSION = 1
"""Bump when the pipeline (emitter, mesher, parser) changes what a solve of the same config yields; invalidates bundle reuse."""

MESH_PRODUCER_VERSION = 1
"""Bump when meshing or ElmerGrid conversion changes what the same mesh spec yields."""

STEP_PRODUCER_VERSION = 2
"""Bump when STEP export or frame conversion changes what the same geometry parameters and frame yield."""

# ISO-10303-21 stamps the export time into the HEADER's FILE_NAME entity; masking it makes a rebuilt STEP digest identically.
_FILE_NAME_HEADER = re.compile(rb"FILE_NAME\s*\(.*?\)\s*;", re.DOTALL)


def step_digest(path: str | Path) -> str:
    """SHA-256 of a STEP with its ``FILE_NAME`` header (the export timestamp) masked; of the path string when the file is absent, so pure-logic keys stay deterministic."""
    file: Path = Path(path)
    if not file.is_file():
        return hashlib.sha256(str(path).encode("utf-8")).hexdigest()

    return hashlib.sha256(_FILE_NAME_HEADER.sub(b"FILE_NAME();", file.read_bytes())).hexdigest()


class AuthoredStep(BaseModel):
    """An authored STEP file: its masked content digest is the identity, its path only where it is read from."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    path: Annotated[str, HashExclude()]
    """Where the file is read from; moving it does not change the geometry."""

    digest: str
    """The file's ``step_digest``."""


class StepSpec(BaseModel):
    """The STEP artifact's spec: the geometry source and the frame its STEP is converted into."""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    source: SerializeAsAny[GeometryArea] | AuthoredStep
    """The parametric geometry or the authored STEP file, in its authored frame."""

    frame: FrameUnion
    """How the authored model maps onto the solve."""

    @property
    def passes_through(self) -> bool:
        """Whether the solve meshes the authored file itself: nothing is built or converted, so nothing is cached."""
        return isinstance(self.source, AuthoredStep) and not self.frame.rewrites_geometry


def _build_step(spec: StepSpec, workdir: Path) -> Produced:
    step_path: Path = workdir / BundleFile.GEOMETRY_STEP

    match spec.source:
        case AuthoredStep(path=path):
            authored: Path = Path(path)
        case GeometryArea():
            authored = spec.source.build_step(step_path)

    _ = spec.frame.prepare_geometry(authored, step_path)
    return Produced(files=(BundleFile.GEOMETRY_STEP,))


STEP: ArtifactKind[StepSpec] = ArtifactKind(
    name=BuiltinKind.STEP,
    spec_type=StepSpec,
    producer_version=STEP_PRODUCER_VERSION,
    required_files=(BundleFile.GEOMETRY_STEP,),
    produce=_build_step,
)
"""The solve-frame STEP of a parametric geometry or an authored file, keyed by its source (``HashExclude`` fields left out) and the frame."""


def step_spec(config: SimulationConfig) -> StepSpec:
    """The STEP spec of ``config``: its parametric geometry, else the authored ``mesh.step`` by content digest."""
    source: GeometryArea | AuthoredStep = config.geometry if config.geometry is not None else AuthoredStep(path=config.mesh.step, digest=step_digest(config.mesh.step))
    return StepSpec(source=source, frame=config.frame)


def step_key(config: SimulationConfig) -> str:
    """The solve-frame geometry identity a run and its mesh key on: the STEP artifact key of ``step_spec(config)``."""
    return artifact_key(STEP, step_spec(config))


def mesh_spec(config: SimulationConfig, step_key_override: str | None = None) -> MeshSpec:
    """The mesh artifact spec of ``config`` (``step_key_override`` when the driver already resolved the geometry), every length in metres."""

    def metres(length: Quantity) -> float:
        # Twelve significant digits absorb the conversion's last-bit noise, so 2.5 um and 2.5e-6 m key alike.
        return float(f"{length.m_as('m'):.12g}")

    def clamp(length: Quantity | None) -> Quantity | None:
        return None if length is None else quantity(metres(length), "m")

    tags: list[TagSelectorSpec] = []
    for tag in config.domain.tags:
        size: float | None = None if tag.mesh_size is None else metres(tag.mesh_size)
        tags.append(TagSelectorSpec(selector=dict(tag.selector), mesh_size=size))

    options: GmshOptions = config.mesh.gmsh_options
    canonical_options: GmshOptions = options.model_copy(
        update={"mesh_size_min": clamp(options.mesh_size_min), "mesh_size_max": clamp(options.mesh_size_max)},
    )

    return MeshSpec(
        step_key=step_key_override if step_key_override is not None else step_key(config),
        model_dim=config.frame.model_dim,
        global_mesh_size=metres(config.mesh.global_mesh_size),
        gmsh_options=canonical_options,
        deterministic=config.mesh.deterministic,
        tags=tags,
        materials=sorted(material.tag for material in config.domain.materials),
    )


ELMER_MESH: ArtifactKind[MeshSpec] = ArtifactKind(
    name=BuiltinKind.ELMER_MESH,
    spec_type=MeshSpec,
    producer_version=MESH_PRODUCER_VERSION,
    required_files=(BundleFile.MESH_HEADER, BundleFile.MESH_NODES, BundleFile.MESH_ELEMENTS, BundleFile.MESH_BOUNDARY, BundleFile.MESH_NAMES, BundleFile.GROUPS),
)
"""ElmerGrid's mesh files plus ``groups.json``; produced by the driver, which owns gmsh and ElmerGrid."""


def mesh_key(config: SimulationConfig) -> str:
    """The mesh artifact key of ``config``: invariant to materials' properties, conditions, physics, and metadata."""
    return artifact_key(ELMER_MESH, mesh_spec(config))


def run_spec_payload(config: SimulationConfig) -> dict[str, object]:
    """The hashed identity of a run: the config minus provenance and the STEP path, the geometry key, and the resolved solver stack, every quantity in base units."""
    context: dict[str, object] = {CANONICAL_UNITS: True}
    return {
        # `physics.overrides` is carried by the resolved stack, so overrides identical to the preset do not move the key.
        "config": spec_payload(
            config,
            exclude={"physics": {"overrides"}, "meta": True, "mesh": {"step"}},
            context=context,
        ),
        "step_key": step_key(config),
        "physics_config": config.resolved_stack().model_dump(mode="json", context=context),
    }


def cache_key(config: SimulationConfig) -> str:
    """Full-run identity: every result-affecting input plus ``RUN_PRODUCER_VERSION``; never the code commit, the machine, or the run metadata."""
    return sha256_json({"kind": BuiltinKind.RUN_BUNDLE.value, "producer_version": RUN_PRODUCER_VERSION, "spec": run_spec_payload(config)})


class RunSpec(BaseModel):
    """The bundle kind's spec: the config a run solved."""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    config: SimulationConfig
    """The full typed input."""


RUN_BUNDLE: ArtifactKind[RunSpec] = ArtifactKind(
    name=BuiltinKind.RUN_BUNDLE,
    spec_type=RunSpec,
    producer_version=RUN_PRODUCER_VERSION,
    required_files=(BundleFile.MANIFEST, BundleFile.RESULT),
    key=lambda spec: cache_key(spec.config),
    in_place=True,
)
"""A finished run bundle, keyed by ``cache_key`` so its store key and its ``run_id`` agree."""
