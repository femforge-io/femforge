"""The gmsh-free group records: body groups, their log summaries, the groups.json round trip, and a rebind that never loads gmsh."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

import femforge
from femforge.artifacts.cache import ArtifactCache
from femforge.artifacts.kind import Produced
from femforge.artifacts.local import LocalArtifactStore
from femforge.common.files import BundleFile
from femforge.common.tags import TagKey
from femforge.meshing.boundary import BoundaryGroup
from femforge.meshing.gmsh_options import GmshOptions
from femforge.meshing.groups import (
    BodyGroupRecord,
    BoundaryGroupRecord,
    MeshGroupsFile,
    PhysicalGroup,
    format_boundary_groups,
    format_physical_groups,
    raise_on_duplicate_names,
)
from femforge.meshing.spec import MeshSpec, TagSelectorSpec
from femforge.physical.domain import EntityTag
from femforge.materials.air import Air
from femforge.materials.neodymium import N42, N52
from femforge.materials.properties import MaterialProperties
from femforge.run.kinds import ELMER_MESH

REBIND_CACHED_MESH = """
import json
import sys
from pathlib import Path

from femforge.artifacts.cache import ArtifactCache
from femforge.artifacts.local import LocalArtifactStore
from femforge.meshing.groups import MeshGroupsFile
from femforge.meshing.spec import MeshSpec
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.materials.properties import MaterialProperties
from femforge.run.artifacts import rebind_groups
from femforge.run.kinds import ELMER_MESH

store, spec_file, run_dir = (Path(arg) for arg in sys.argv[1:])
spec = MeshSpec.model_validate_json(spec_file.read_text(encoding="utf-8"))
handle = ArtifactCache(LocalArtifactStore(store)).fetch(ELMER_MESH, spec, into=run_dir)
assert handle is not None, "the cached mesh is missing"

domain = DomainConfig(
    materials=[MaterialProperties(name="Air", tag="Air"), MaterialProperties(name="Neodymium", tag="N42")],
    tags=[EntityTag(selector={"Magnetization": "N"})],
)
bodies, boundaries = rebind_groups(MeshGroupsFile.load(run_dir), domain)
print(json.dumps({"bodies": [body.name for body in bodies], "boundaries": [boundary.name for boundary in boundaries], "gmsh": "gmsh" in sys.modules}))
"""


def _magnet(key: str, entity_tags: list[int]) -> PhysicalGroup:
    return PhysicalGroup(
        dim=3,
        gmsh_id=entity_tags[0],
        name=f"N52_MAGNETIZATION{key}",
        material=N52(),
        tags=[EntityTag(selector={TagKey.MAGNETIZATION: key})],
        entity_tags=entity_tags,
    )


def test_physical_group_name_returns_the_shared_name() -> None:
    material: MaterialProperties = N52()
    tags: list[EntityTag] = [EntityTag(selector={TagKey.MAGNETIZATION: "N"})]

    group = PhysicalGroup(
        dim=3,
        gmsh_id=7,
        name="N52_MAG_N",
        material=material,
        tags=tags,
        entity_tags=[7],
    )

    assert group.physical_group_name() == "N52_MAG_N"
    assert group.id == 7
    assert group.dim == 3


def test_physical_group_repr_includes_name_and_material() -> None:
    group = PhysicalGroup(
        dim=3,
        gmsh_id=1,
        name="N52_MAG_N",
        material=N52(),
        tags=[],
        entity_tags=[1],
    )

    text = repr(group)

    assert "N52_MAG_N" in text
    assert "PhysicalGroup" in text


def test_format_physical_groups_renders_the_summary_tree() -> None:
    air = PhysicalGroup(
        dim=3,
        gmsh_id=1,
        name="AIR",
        material=Air(),
        tags=[],
        entity_tags=[1, 2, 3, 4, 5],
    )

    lines = format_physical_groups([air, _magnet("N", [6, 7]), _magnet("S", [8, 9])], total_entities=9).split("\n")

    # The leading blank line starts the tree on its own line inside the log record.
    assert lines[0] == ""
    assert lines[1] == "9 Entities → 3 Physical Groups:"
    assert lines[2].startswith("  Air") and lines[2].endswith("→ AIR") and "   5  " in lines[2]
    assert lines[3].startswith("  Neodymium")
    assert lines[4].startswith("    MagnetizationN") and lines[4].endswith("→ N52_MAGNETIZATIONN") and "   2  " in lines[4]
    assert lines[5].startswith("    MagnetizationS") and lines[5].endswith("→ N52_MAGNETIZATIONS")

    arrow_columns = {line.index("→") for line in lines[2:] if "→" in line}
    assert len(arrow_columns) == 1


def test_format_physical_groups_untagged_groups_of_a_split_material_get_a_label() -> None:
    untagged = PhysicalGroup(
        dim=3,
        gmsh_id=2,
        name="N52",
        material=N52(),
        tags=[],
        entity_tags=[2],
    )

    text = format_physical_groups([_magnet("N", [1]), untagged], total_entities=2)

    assert "(untagged)" in text
    assert "→ N52" in text


def test_format_boundary_groups_aligns_each_group_with_its_id() -> None:
    groups = [
        BoundaryGroup(gmsh_id=3, name="AIR__EXT", bodies=("AIR",), face_tags=[10, 11]),
        BoundaryGroup(gmsh_id=4, name="AIR__N52_MAGNETIZATIONN", bodies=("AIR", "N52_MAGNETIZATIONN"), face_tags=[12]),
    ]

    lines = format_boundary_groups(groups).split("\n")

    assert lines == [
        "",
        "2 Boundary Groups:",
        "  AIR__EXT" + " " * 19 + "2  → id 3",
        "  AIR__N52_MAGNETIZATIONN    1  → id 4",
    ]


def test_format_boundary_groups_shows_the_measure_of_named_groups() -> None:
    groups = [
        BoundaryGroup(gmsh_id=3, name="BEAM__EXT", bodies=("BEAM",), face_tags=[10, 11]),
        BoundaryGroup(gmsh_id=4, name="TIP", bodies=("BEAM",), face_tags=[12], measure=1e-4, named=True),
    ]

    lines = format_boundary_groups(groups).split("\n")

    assert lines[2] == "  BEAM__EXT    2  → id 3"
    assert lines[3] == "  TIP          1  → id 4  (0.0001 m²)"


def test_duplicate_body_names_raise_and_name_the_clash() -> None:
    seat = PhysicalGroup(
        dim=3,
        gmsh_id=1,
        name="SEAT",
        material=N52(),
        tags=[],
        entity_tags=[1],
        authored="SEAT",
    )
    air = PhysicalGroup(
        dim=3,
        gmsh_id=2,
        name="AIR",
        material=Air(),
        tags=[],
        entity_tags=[2],
    )
    clash = PhysicalGroup(
        dim=3,
        gmsh_id=3,
        name="SEAT",
        material=Air(),
        tags=[],
        entity_tags=[3],
        authored="Seat",
    )

    raise_on_duplicate_names([seat, air])

    with pytest.raises(ValueError, match="'SEAT' on materials"):
        raise_on_duplicate_names([seat, air, clash])


def test_groups_file_captures_the_mesher_groups_and_round_trips(tmp_path: Path) -> None:
    air = PhysicalGroup(
        dim=2,
        gmsh_id=1,
        name="AIR",
        material=Air(),
        tags=[],
        entity_tags=[1],
    )
    magnet = PhysicalGroup(
        dim=2,
        gmsh_id=2,
        name="MAGNET",
        material=N42(),
        tags=[EntityTag(selector={TagKey.MAGNETIZATION: "N"})],
        entity_tags=[2, 3],
        authored="Magnet",
    )
    external = BoundaryGroup(gmsh_id=3, name="AIR__EXT", bodies=("AIR",), face_tags=[10], dim=1)
    axis = BoundaryGroup(
        gmsh_id=4,
        name="AIR__AXIS",
        bodies=("AIR",),
        face_tags=[13],
        dim=1,
        on_axis=True,
    )
    tip = BoundaryGroup(
        gmsh_id=5,
        name="TIP",
        bodies=("MAGNET",),
        face_tags=[14, 15],
        dim=1,
        tags=[EntityTag(selector={TagKey.NAME: "Tip"})],
        measure=0.004,
        named=True,
    )

    groups = MeshGroupsFile.capture([air, magnet], [external, axis, tip], element_count=4067)
    path = groups.write(tmp_path)

    assert groups.bodies[1] == BodyGroupRecord(
        dim=2,
        name="MAGNET",
        material=N42().tag,
        selectors=[{"Magnetization": "N"}],
        entity_tags=[2, 3],
        authored="Magnet",
    )
    assert groups.boundaries == [
        BoundaryGroupRecord(dim=1, name="AIR__EXT", bodies=("AIR",), face_tags=[10]),
        BoundaryGroupRecord(
            dim=1,
            name="AIR__AXIS",
            bodies=("AIR",),
            face_tags=[13],
            on_axis=True,
        ),
        BoundaryGroupRecord(
            dim=1,
            name="TIP",
            bodies=("MAGNET",),
            face_tags=[14, 15],
            named=True,
            selectors=[{"Name": "Tip"}],
            measure=0.004,
        ),
    ]
    assert groups.element_count == 4067
    assert path == tmp_path / BundleFile.GROUPS
    assert MeshGroupsFile.load(tmp_path) == groups


def test_rebinding_a_cached_mesh_never_loads_gmsh(tmp_path: Path) -> None:
    def produce(spec: MeshSpec, workdir: Path) -> Produced:
        _ = spec
        for name in (BundleFile.MESH_HEADER, BundleFile.MESH_NODES, BundleFile.MESH_ELEMENTS, BundleFile.MESH_BOUNDARY, BundleFile.MESH_NAMES):
            _ = (workdir / name).write_text("stub", encoding="utf-8")

        groups = MeshGroupsFile(
            bodies=[
                BodyGroupRecord(dim=3, name="AIR", material="Air", entity_tags=[1]),
                BodyGroupRecord(
                    dim=3,
                    name="N42_MAGNETIZATIONN",
                    material="N42",
                    selectors=[{"Magnetization": "N"}],
                    entity_tags=[2],
                ),
            ],
            boundaries=[
                BoundaryGroupRecord(dim=2, name="AIR__EXT", bodies=("AIR",), face_tags=[10]),
                BoundaryGroupRecord(
                    dim=2,
                    name="AIR__N42_MAGNETIZATIONN",
                    bodies=("AIR", "N42_MAGNETIZATIONN"),
                    face_tags=[11],
                ),
            ],
            element_count=42,
        )
        _ = groups.write(workdir)
        return Produced(files=ELMER_MESH.required_files)

    spec = MeshSpec(
        step_key="0" * 64,
        model_dim=3,
        global_mesh_size=0.001,
        gmsh_options=GmshOptions(),
        deterministic=False,
        tags=[TagSelectorSpec(selector={"Magnetization": "N"})],
        materials=["Air", "N42"],
    )
    store = tmp_path / "store"
    _ = ArtifactCache(LocalArtifactStore(store)).get_or_produce(ELMER_MESH, spec, into=None, produce=produce)
    spec_file = tmp_path / "spec.json"
    _ = spec_file.write_text(spec.model_dump_json(), encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    import_paths = [str(Path(femforge.__file__).resolve().parents[1]), os.environ.get("PYTHONPATH", "")]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(path for path in import_paths if path)}

    completed = subprocess.run(
        [sys.executable, "-c", REBIND_CACHED_MESH, str(store), str(spec_file), str(run_dir)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    outcome = cast("dict[str, object]", json.loads(completed.stdout.splitlines()[-1]))
    assert outcome == {"bodies": ["AIR", "N42_MAGNETIZATIONN"], "boundaries": ["AIR__EXT", "AIR__N42_MAGNETIZATIONN"], "gmsh": False}


def test_the_group_names_keep_their_import_paths() -> None:
    import femforge.meshing
    import femforge.run
    from femforge.run import artifacts

    assert artifacts.MeshGroupsFile is MeshGroupsFile and femforge.run.MeshGroupsFile is MeshGroupsFile
    assert artifacts.BodyGroupRecord is BodyGroupRecord and artifacts.BoundaryGroupRecord is BoundaryGroupRecord
    assert femforge.meshing.PhysicalGroup is PhysicalGroup and femforge.meshing.MeshGroupsFile is MeshGroupsFile
    assert femforge.meshing.BodyGroupRecord is BodyGroupRecord and femforge.meshing.BoundaryGroupRecord is BoundaryGroupRecord
    assert femforge.meshing.raise_on_duplicate_names is raise_on_duplicate_names
    assert femforge.meshing.format_physical_groups is format_physical_groups and femforge.meshing.format_boundary_groups is format_boundary_groups
