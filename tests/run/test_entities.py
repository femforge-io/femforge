"""entities.json round-trips and the bodies/boundaries collections resolve names, tags, and the three id spaces."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from femforge.common.tags import TagKey
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.materials.properties import MaterialProperties
from femforge.run.entities import (
    Bodies,
    Body,
    Boundaries,
    Boundary,
    BoundaryKind,
    EntitiesFile,
    load_entities,
    write_entities,
)
from femforge.run.manifest import RunManifest

FIXED_DT = datetime(2026, 6, 14, 10, 0, 0, tzinfo=timezone.utc)


@dataclass
class _FakeMaterial:
    tag: str


@dataclass
class _FakeTag:
    selector: dict[str, str]


@dataclass
class _FakeBodyGroup:
    id: int
    name: str
    material: _FakeMaterial
    tags: list[_FakeTag] = field(default_factory=list)


@dataclass
class _FakeBoundaryGroup:
    id: int
    name: str
    bodies: tuple[str, ...]
    on_axis: bool = False


def _write_sample_entities(tmp_path: Path) -> None:
    body_groups = [
        _FakeBodyGroup(id=1, name="AIR", material=_FakeMaterial("Air")),
        _FakeBodyGroup(id=2, name="N42_MAGNETIZATIONN", material=_FakeMaterial("N42"), tags=[_FakeTag({"Magnetization": "N"})]),
        _FakeBodyGroup(id=3, name="UNTAGGED", material=_FakeMaterial("")),
    ]
    boundary_groups = [
        _FakeBoundaryGroup(id=4, name="AIR__EXT", bodies=("AIR",)),
        _FakeBoundaryGroup(id=5, name="AIR__N42_MAGNETIZATIONN", bodies=("AIR", "N42_MAGNETIZATIONN")),
        _FakeBoundaryGroup(id=6, name="AIR__AXIS", bodies=("AIR",), on_axis=True),
    ]
    write_entities(tmp_path, body_groups, boundary_groups)


def test_write_entities_round_trips_ids_materials_and_tags(tmp_path: Path) -> None:
    _write_sample_entities(tmp_path)
    assert not list(tmp_path.glob("*.tmp"))

    data = EntitiesFile.model_validate_json((tmp_path / "entities.json").read_text(encoding="utf-8"))
    assert data.bodies[2].name == "N42_MAGNETIZATIONN"
    assert data.bodies[2].material == "N42"
    assert data.bodies[2].tags == {"Magnetization": "N"}
    # An empty material tag is recorded as absent, not as "".
    assert data.bodies[3].material is None
    assert data.boundaries[6].on_axis is True


def test_load_entities_from_the_sidecar_resolves_kinds_and_sif_indices(tmp_path: Path) -> None:
    _write_sample_entities(tmp_path)
    # BC block names append condition kinds; the interface got no condition, so no block and no sif_index.
    (tmp_path / "boundaries.yml").write_text("1: AIR__EXT_far_field\n", encoding="utf-8")

    entities = load_entities(tmp_path)

    magnet = entities.bodies["N42_MAGNETIZATIONN"]
    assert magnet.mesh_id == 2
    assert magnet.material == "N42"
    assert magnet.tags == {"Magnetization": "N"}

    external = entities.boundaries["AIR__EXT"]
    assert external.kind is BoundaryKind.EXTERNAL
    assert external.mesh_id == 4
    assert external.sif_index == 1

    interface = entities.boundaries["AIR__N42_MAGNETIZATIONN"]
    assert interface.kind is BoundaryKind.INTERFACE
    assert interface.bodies == ("AIR", "N42_MAGNETIZATIONN")
    assert interface.sif_index is None

    assert entities.boundaries["AIR__AXIS"].kind is BoundaryKind.AXIS


def _manifest(tmp_path: Path) -> RunManifest:
    config = SimulationConfig(
        mesh=MeshConfig(step=str(tmp_path / "does-not-exist.step")),
        domain=DomainConfig(
            materials=[
                MaterialProperties(name="Air", tag="Air"),
                MaterialProperties(name="Neodymium", tag="N42"),
                MaterialProperties(name="Anonymous", tag=""),
            ],
            tags=[
                EntityTag(selector={TagKey.MAGNETIZATION: "N"}),
                EntityTag(selector={TagKey.CURRENT_DIR: "Entry", TagKey.PHASE: "0"}),
                EntityTag(selector={}),
            ],
        ),
    )
    return RunManifest.build(config, status="ok", code_sha="sha1", created_at=FIXED_DT)


_MESH_NAMES = """\
! ----- names for bodies -----
$ AIR = 1
$ N42_MAGNETIZATIONN = 2
$ COPPER_CURRENTDIRENTRY_PHASE0 = 3
$ MYSTERY_MAGNETIZATIONN = 4
! ----- names for boundaries -----
$ AIR__EXT = 5
$ AIR__N42_MAGNETIZATIONN = 6
$ AIR__AXIS = 7
"""


def test_load_entities_enriches_mesh_names_from_the_manifest(tmp_path: Path) -> None:
    (tmp_path / "mesh.names").write_text(_MESH_NAMES, encoding="utf-8")
    (tmp_path / "boundaries.yml").write_text("1: AIR__EXT_far_field\n2: AIR__N42_MAGNETIZATIONN\n", encoding="utf-8")

    entities = load_entities(tmp_path, manifest=_manifest(tmp_path))

    assert entities.bodies["AIR"].material == "Air"
    assert entities.bodies["AIR"].tags == {}

    magnet = entities.bodies["N42_MAGNETIZATIONN"]
    assert magnet.material == "N42"
    assert magnet.mesh_id == 2
    assert magnet.tags == {"Magnetization": "N"}

    # A multi-key selector recovers as whole _-separated segments of the composed name.
    coil = entities.bodies["COPPER_CURRENTDIRENTRY_PHASE0"]
    assert coil.material is None
    assert coil.tags == {"CurrentDir": "Entry", "Phase": "0"}

    # No material prefix matched, but the tag segment still recovers.
    mystery = entities.bodies["MYSTERY_MAGNETIZATIONN"]
    assert mystery.material is None
    assert mystery.tags == {"Magnetization": "N"}

    # Boundary names decode to kind + bodies; the sif join matches exact and prefixed block names.
    assert entities.boundaries["AIR__EXT"].kind is BoundaryKind.EXTERNAL
    assert entities.boundaries["AIR__EXT"].bodies == ("AIR",)
    assert entities.boundaries["AIR__EXT"].sif_index == 1
    assert entities.boundaries["AIR__EXT"].mesh_id == 5
    assert entities.boundaries["AIR__N42_MAGNETIZATIONN"].sif_index == 2
    assert entities.boundaries["AIR__AXIS"].kind is BoundaryKind.AXIS
    assert entities.boundaries["AIR__AXIS"].bodies == ("AIR",)


def test_load_entities_reads_the_manifest_from_disk_when_not_given(tmp_path: Path) -> None:
    (tmp_path / "mesh.names").write_text(_MESH_NAMES, encoding="utf-8")
    (tmp_path / "manifest.json").write_text(_manifest(tmp_path).model_dump_json(), encoding="utf-8")
    entities = load_entities(tmp_path)
    assert entities.bodies["N42_MAGNETIZATIONN"].material == "N42"


def test_load_entities_bare_mesh_names_when_no_manifest(tmp_path: Path) -> None:
    (tmp_path / "mesh.names").write_text(_MESH_NAMES, encoding="utf-8")
    entities = load_entities(tmp_path)
    body = entities.bodies["N42_MAGNETIZATIONN"]
    assert body.mesh_id == 2
    assert body.material is None
    assert body.tags == {}


def test_load_entities_empty_when_no_files(tmp_path: Path) -> None:
    entities = load_entities(tmp_path)
    assert len(entities.bodies) == 0
    assert len(entities.boundaries) == 0


def test_sif_join_prefers_the_longest_group_prefix(tmp_path: Path) -> None:
    # "A__B_C_fixed_potential" must join to group "A__B_C", never to the shorter "A__B".
    (tmp_path / "mesh.names").write_text(
        "! names for bodies\n$ A = 1\n! names for boundaries\n$ A__B = 2\n$ A__B_C = 3\n",
        encoding="utf-8",
    )
    (tmp_path / "boundaries.yml").write_text("1: A__B_C_fixed_potential\n2: A__B\n", encoding="utf-8")
    entities = load_entities(tmp_path)
    assert entities.boundaries["A__B_C"].sif_index == 1
    assert entities.boundaries["A__B"].sif_index == 2


def _bodies() -> Bodies:
    return Bodies(
        [
            Body(name="AIR", mesh_id=1, material="Air"),
            Body(name="N42_MAGNETIZATIONN", mesh_id=2, material="N42", tags={"Magnetization": "N"}),
            Body(name="N42_MAGNETIZATIONS", mesh_id=3, material="N42", tags={"Magnetization": "S"}),
        ]
    )


def test_bodies_lookup_iteration_and_filters() -> None:
    bodies = _bodies()
    assert len(bodies) == 3
    assert "AIR" in bodies
    assert bodies["AIR"] in bodies
    assert "COPPER" not in bodies
    assert bodies.get("COPPER") is None
    assert {body.name for body in bodies} == {"AIR", "N42_MAGNETIZATIONN", "N42_MAGNETIZATIONS"}

    assert [body.mesh_id for body in bodies.with_material("n42")] == [2, 3]
    assert [body.name for body in bodies.with_tag(TagKey.MAGNETIZATION, "S")] == ["N42_MAGNETIZATIONS"]
    assert bodies.with_tag("Magnetization", "N")[0].mesh_id == 2


def test_bodies_getitem_error_lists_available_names() -> None:
    with pytest.raises(KeyError, match="AIR"):
        _ = _bodies()["COPPER"]


def _boundaries() -> Boundaries:
    return Boundaries(
        [
            Boundary(name="AIR__EXT", mesh_id=4, bodies=("AIR",), kind=BoundaryKind.EXTERNAL, sif_index=1),
            Boundary(name="AIR__N42_MAGNETIZATIONN", mesh_id=5, bodies=("AIR", "N42_MAGNETIZATIONN"), kind=BoundaryKind.INTERFACE),
            Boundary(name="AIR__AXIS", mesh_id=6, bodies=("AIR",), kind=BoundaryKind.AXIS),
        ]
    )


def test_boundaries_between_matches_either_name_order() -> None:
    boundaries = _boundaries()
    found = boundaries.between("AIR", "N42_MAGNETIZATIONN")
    assert found.mesh_id == 5
    assert boundaries.between("N42_MAGNETIZATIONN", "AIR") is found
    # Body objects are accepted wherever a name is.
    assert boundaries.between(_bodies()["AIR"], "N42_MAGNETIZATIONN") is found


def test_boundaries_between_error_lists_the_bodies_boundaries() -> None:
    with pytest.raises(KeyError, match="AIR__EXT"):
        _boundaries().between("AIR", "COPPER")


def test_boundaries_external_axis_and_of_body() -> None:
    boundaries = _boundaries()
    assert boundaries.external("AIR").sif_index == 1
    assert boundaries.axis("AIR") is not None
    assert boundaries.axis("N42_MAGNETIZATIONN") is None
    assert {boundary.name for boundary in boundaries.of_body("AIR")} == {"AIR__EXT", "AIR__N42_MAGNETIZATIONN", "AIR__AXIS"}
    assert [boundary.name for boundary in boundaries.of_body("N42_MAGNETIZATIONN")] == ["AIR__N42_MAGNETIZATIONN"]


def test_boundaries_lookup_and_error_message() -> None:
    boundaries = _boundaries()
    assert len(boundaries) == 3
    assert "AIR__EXT" in boundaries
    assert boundaries["AIR__EXT"] in boundaries
    assert boundaries.get("NOPE") is None
    with pytest.raises(KeyError, match="available"):
        _ = boundaries["NOPE"]
