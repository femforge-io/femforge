"""A cached mesh's group record rebinds to a config; the bundle file policy picks the right classes; rehydrate/prune follow the manifest."""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from femforge.artifacts.cache import ArtifactCache
from femforge.artifacts.kind import Produced
from femforge.artifacts.store import ArtifactMissing
from femforge.artifacts.local import LocalArtifactStore
from femforge.artifacts.sidecar import ArtifactOrigin, ArtifactRef
from femforge.common.frame import Solid
from femforge.common.tags import TagKey
from femforge.config import SimulationConfig
from femforge.geometry.config import GeometryArea
from femforge.meshing.groups import MeshGroupsFile
from femforge.physical.conditions import Magnetization
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.common.vector import Direction
from femforge.materials.properties import MaterialProperties
from femforge.run.artifacts import BundlePolicy, MeshRebindError, bundle_files, prune_store_sourced, rebind_groups, rehydrate_bundle
from femforge.run.bundle import write_manifest
from femforge.run.kinds import ELMER_MESH, STEP, MeshSpec, StepSpec, mesh_spec
from femforge.run.manifest import RunManifest


@dataclass
class _FakeMaterial:
    tag: str


@dataclass
class _FakeTag:
    selector: dict[str, str]


@dataclass
class _FakeBodyGroup:
    dim: int
    name: str
    material: _FakeMaterial
    tags: list[_FakeTag] = field(default_factory=list)
    entity_tags: list[int] = field(default_factory=list)
    authored: str | None = None


@dataclass
class _FakeBoundaryGroup:
    dim: int
    name: str
    bodies: tuple[str, ...]
    entity_tags: list[int] = field(default_factory=list)
    on_axis: bool = False


def _groups() -> MeshGroupsFile:
    bodies = [
        _FakeBodyGroup(
            dim=2,
            name="AIR",
            material=_FakeMaterial("Air"),
            entity_tags=[1],
        ),
        _FakeBodyGroup(
            dim=2,
            name="N42_MAGNETIZATIONN",
            material=_FakeMaterial("N42"),
            tags=[_FakeTag({"Magnetization": "N"})],
            entity_tags=[2, 3],
        ),
    ]
    boundaries = [
        _FakeBoundaryGroup(
            dim=1,
            name="AIR__EXT",
            bodies=("AIR",),
            entity_tags=[10],
        ),
        _FakeBoundaryGroup(
            dim=1,
            name="AIR__N42_MAGNETIZATIONN",
            bodies=("AIR", "N42_MAGNETIZATIONN"),
            entity_tags=[11, 12],
        ),
        _FakeBoundaryGroup(
            dim=1,
            name="AIR__AXIS",
            bodies=("AIR",),
            entity_tags=[13],
            on_axis=True,
        ),
    ]
    return MeshGroupsFile.capture(bodies, boundaries, element_count=4067)


def _domain() -> DomainConfig:
    return DomainConfig(
        materials=[MaterialProperties(name="Air", tag="Air"), MaterialProperties(name="Neodymium", tag="n42")],
        tags=[EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 1, 0))])],
    )


def test_rebind_groups_attaches_the_current_domain_objects() -> None:
    domain = _domain()
    bodies, boundaries = rebind_groups(_groups(), domain)

    assert [body.name for body in bodies] == ["AIR", "N42_MAGNETIZATIONN"]
    magnet = bodies[1]
    assert magnet.material is domain.materials[1]
    assert magnet.tags == [domain.tags[0]]
    assert magnet.tags[0].conditions[0] is domain.tags[0].conditions[0]
    assert magnet.entity_tags == [2, 3]
    assert magnet.id == 0 and magnet.dim == 2

    assert [boundary.name for boundary in boundaries] == ["AIR__EXT", "AIR__N42_MAGNETIZATIONN", "AIR__AXIS"]
    assert boundaries[1].bodies == ("AIR", "N42_MAGNETIZATIONN")
    assert boundaries[2].is_axis and not boundaries[2].is_external
    assert boundaries[0].entity_tags == [10]


def test_rebind_groups_fails_loudly_on_a_mismatched_domain() -> None:
    missing_material = _domain()
    missing_material.materials.pop()
    with pytest.raises(MeshRebindError, match="material tag"):
        _ = rebind_groups(_groups(), missing_material)

    missing_tag = _domain()
    missing_tag.tags.clear()
    with pytest.raises(MeshRebindError, match="selector"):
        _ = rebind_groups(_groups(), missing_tag)

    renamed = _domain()
    renamed.materials[1].tag = "N52"
    groups = _groups()
    groups.bodies[1].material = "N52"
    with pytest.raises(MeshRebindError, match="would be named"):
        _ = rebind_groups(groups, renamed)


def test_rebind_groups_keeps_an_authored_name() -> None:
    groups = _groups()
    groups.bodies[1].name = "MAGNET"
    groups.bodies[1].authored = "Magnet"

    bodies, _ = rebind_groups(groups, _domain())
    assert bodies[1].name == "MAGNET"
    assert bodies[1].authored == "Magnet"
    assert MeshGroupsFile.model_validate_json(groups.model_dump_json()).bodies[1].authored == "Magnet"


def _fill_bundle(bundle: Path) -> None:
    for name in (
        "manifest.json",
        "result.json",
        "entities.json",
        "boundaries.yml",
        "case.sif",
        "ELMERSOLVER_STARTINFO",
        "scalars.dat",
        "scalars.dat.names",
        "elmersolver.log",
        "run.log",
        "geometry.step",
        "case_t0001.vtu",
        "case_t0001.pvtu",
        "mesh.nodes",
        "mesh.elements",
        "groups.json",
    ):
        _ = (bundle / name).write_text("x", encoding="utf-8")
    (bundle / "partitioning.4").mkdir()
    _ = (bundle / "partitioning.4" / "part.1.nodes").write_text("x", encoding="utf-8")
    _ = (bundle / "manifest.json.tmp").write_text("x", encoding="utf-8")


def test_bundle_files_per_policy(tmp_path: Path) -> None:
    _fill_bundle(tmp_path)

    core = bundle_files(tmp_path, BundlePolicy.CORE)
    assert "manifest.json" in core and "result.json" in core and "scalars.dat.names" in core and "geometry.step" in core
    assert "case_t0001.vtu" not in core and "mesh.nodes" not in core and "manifest.json.tmp" not in core

    fields = bundle_files(tmp_path, BundlePolicy.FIELDS, exclude=["geometry.step", "groups.json"])
    assert "case_t0001.vtu" in fields and "case_t0001.pvtu" in fields
    assert "geometry.step" not in fields and "groups.json" not in fields
    assert "mesh.nodes" not in fields

    full = bundle_files(tmp_path, BundlePolicy.FULL)
    assert "mesh.nodes" in full and "partitioning.4/part.1.nodes" in full
    assert full == sorted(full)


class _StubGeometry(GeometryArea):
    width: float = 1.0

    def build_step(self, path: Path) -> Path:
        _ = path.write_text(f"ISO-10303-21; width={self.width}", encoding="utf-8")
        return path


def _produce_mesh(spec: MeshSpec, workdir: Path) -> Produced:
    _ = spec
    for name in ELMER_MESH.required_files:
        _ = (workdir / name).write_text("m", encoding="utf-8")
    return Produced(files=ELMER_MESH.required_files)


def test_rehydrate_and_prune_follow_the_manifest(tmp_path: Path) -> None:
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"))
    config = SimulationConfig(geometry=_StubGeometry(width=2.0))
    bundle = tmp_path / "run"
    bundle.mkdir()

    step = cache.get_or_produce(STEP, StepSpec(source=_StubGeometry(width=2.0), frame=Solid()), bundle)
    mesh = cache.get_or_produce(
        ELMER_MESH,
        mesh_spec(config),
        bundle,
        produce=_produce_mesh,
    )
    manifest = RunManifest.build(
        config,
        status="ok",
        run_id="run123",
        code_sha="sha1",
    )
    manifest.artifacts.extend([step.ref, mesh.ref])
    _ = write_manifest(bundle, manifest)
    assert manifest.cached_inputs() == {"geometry.step", *ELMER_MESH.required_files}

    removed = prune_store_sourced(bundle)
    assert bundle / "geometry.step" in removed and bundle / "mesh.nodes" in removed
    assert not (bundle / "geometry.step").exists()
    assert prune_store_sourced(bundle) == []

    restored = rehydrate_bundle(bundle, cache)
    assert [h.origin for h in restored] == [ArtifactOrigin.LOCAL, ArtifactOrigin.LOCAL]
    assert (bundle / "geometry.step").read_text(encoding="utf-8").endswith("width=2.0")
    assert (bundle / "groups.json").is_file()
    assert isinstance(restored[0].ref, ArtifactRef)

    manifest.artifacts.append(ArtifactRef(kind="mystery", key="0" * 64, origin=ArtifactOrigin.LOCAL))
    _ = write_manifest(bundle, manifest)
    with pytest.raises(ArtifactMissing, match="not a kind the driver materializes"):
        _ = rehydrate_bundle(bundle, cache)
