"""End-to-end (needs gmsh + build123d): tagged faces of a solid model mesh into named boundary groups."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

pytest.importorskip("gmsh")
pytest.importorskip("build123d")

import build123d as b3d  # imported after the gate so the file still collects where the CAD stack is absent

from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.config import SimulationConfig
from femforge.geometry.tags import TaggedCompound, TaggedFace, TaggedPart
from femforge.materials.steel import Steel_316
from femforge.meshing.backend import MeshError
from femforge.meshing.boundary import BoundaryGroup
from femforge.meshing.config import MeshConfig
from femforge.meshing.mesher import Mesher
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import U


def _beam() -> tuple[b3d.Box, TaggedPart]:
    box = b3d.Box(20, 10, 10)
    beam = TaggedPart([box], tags={TagKey.MATERIAL: "Steel316"}, name="Beam")
    beam.color = b3d.Color("gray")
    return box, beam


def _face(face: b3d.Face, name: str, color: str) -> TaggedFace:
    tagged = TaggedFace(face, name=name)
    tagged.color = b3d.Color(color)
    return tagged


def _mesh(path: Path, *leaves: TaggedPart | TaggedFace) -> tuple[Mesher, DomainConfig]:
    TaggedCompound(children=list(leaves)).export_step(str(path))
    domain = DomainConfig(
        materials=[Steel_316()],
        tags=[
            EntityTag(selector={TagKey.NAME: "Beam"}),
            EntityTag(selector={TagKey.NAME: "Root"}),
            EntityTag(selector={TagKey.NAME: "Tip"}),
        ],
    )
    config = SimulationConfig(mesh=MeshConfig(step=str(path), global_mesh_size=2.5 * U.mm), domain=domain)
    mesher = Mesher(config)
    mesher.generate()
    return mesher, domain


@pytest.fixture(scope="module")
def end_faces(tmp_path_factory: pytest.TempPathFactory) -> tuple[Mesher, DomainConfig]:
    box, beam = _beam()
    x_faces = box.faces().filter_by(b3d.Axis.X).sort_by(b3d.Axis.X)
    step: Path = tmp_path_factory.mktemp("named") / "beam.step"
    return _mesh(step, beam, _face(x_faces[0], "Root", "red"), _face(x_faces[-1], "Tip", "blue"))


def test_tagged_faces_carve_named_groups_out_of_the_shell(end_faces: tuple[Mesher, DomainConfig]) -> None:
    mesher, _ = end_faces
    by_name: dict[str, BoundaryGroup] = {group.name: group for group in mesher.boundary_groups}

    assert [group.name for group in mesher.physical_groups] == ["BEAM"]
    assert set(by_name) == {"BEAM__EXT", "ROOT", "TIP"}
    assert len(by_name["BEAM__EXT"].entity_tags) == 4
    assert not by_name["BEAM__EXT"].named and by_name["BEAM__EXT"].measure is None


def test_a_named_group_records_its_area_body_and_tag(end_faces: tuple[Mesher, DomainConfig]) -> None:
    mesher, domain = end_faces
    root: BoundaryGroup = next(group for group in mesher.boundary_groups if group.name == "ROOT")

    assert root.named and root.is_external
    assert root.bodies == ("BEAM",)
    assert root.measure == pytest.approx(1e-4, rel=1e-6)
    assert len(root.tags) == 1 and root.tags[0] is domain.tags[1]
    assert Path(mesher.output_filename).is_file()


def test_a_patch_face_splits_the_body_face(tmp_path: Path) -> None:
    box, beam = _beam()
    tip_face = box.faces().sort_by(b3d.Axis.X)[-1]
    patch = b3d.Pos(tip_face.center()) * b3d.Rot(0, 90, 0) * b3d.Rectangle(4, 4).face()

    mesher, _ = _mesh(tmp_path / "patch.step", beam, _face(patch, "Tip", "blue"))
    by_name: dict[str, BoundaryGroup] = {group.name: group for group in mesher.boundary_groups}

    assert set(by_name) == {"BEAM__EXT", "TIP"}
    assert len(by_name["BEAM__EXT"].entity_tags) == 6
    assert by_name["TIP"].measure == pytest.approx(1.6e-5, rel=1e-6)


def test_a_face_off_the_body_raises(tmp_path: Path) -> None:
    _, beam = _beam()
    floating = b3d.Pos(40, 0, 0) * b3d.Rot(0, 90, 0) * b3d.Rectangle(4, 4).face()

    with pytest.raises(MeshError, match="'TIP' lies on no body boundary"):
        _ = _mesh(tmp_path / "off.step", beam, _face(floating, "Tip", "blue"))


def test_a_patch_on_a_tagged_face_keeps_its_own_name(tmp_path: Path) -> None:
    box, beam = _beam()
    root_face = box.faces().sort_by(b3d.Axis.X)[0]
    patch = b3d.Pos(root_face.center()) * b3d.Rot(0, 90, 0) * b3d.Rectangle(4, 4).face()

    mesher, _ = _mesh(tmp_path / "nested.step", beam, _face(root_face, "Root", "red"), _face(patch, "Tip", "blue"))
    by_name: dict[str, BoundaryGroup] = {group.name: group for group in mesher.boundary_groups}

    assert set(by_name) == {"BEAM__EXT", "ROOT", "TIP"}
    assert by_name["ROOT"].measure == pytest.approx(0.84e-4, rel=1e-6)
    assert by_name["TIP"].measure == pytest.approx(1.6e-5, rel=1e-6)


def test_a_named_face_survives_the_fragment_of_several_bodies(tmp_path: Path) -> None:
    box, beam = _beam()
    block = TaggedPart([b3d.Pos(15, 0, 0) * b3d.Box(10, 10, 10)], tags={TagKey.MATERIAL: "Steel316"}, name="Block")
    block.color = b3d.Color("green")
    root_face = box.faces().sort_by(b3d.Axis.X)[0]

    mesher, _ = _mesh(tmp_path / "pair.step", beam, block, _face(root_face, "Root", "red"))
    by_name: dict[str, BoundaryGroup] = {group.name: group for group in mesher.boundary_groups}

    assert set(by_name) == {"BEAM__BLOCK", "BEAM__EXT", "BLOCK__EXT", "ROOT"}
    assert by_name["ROOT"].bodies == ("BEAM",)
    assert by_name["ROOT"].measure == pytest.approx(1e-4, rel=1e-6)


def test_a_named_group_records_its_centroid_bounds_and_outward_normal(end_faces: tuple[Mesher, DomainConfig]) -> None:
    mesher, _ = end_faces
    by_name: dict[str, BoundaryGroup] = {group.name: group for group in mesher.boundary_groups}
    root: BoundaryGroup = by_name["ROOT"]
    beam = mesher.physical_groups[0]

    assert root.normal is not None and root.normal.isclose(Direction(-1, 0, 0), tolerance=1e-9)
    assert by_name["TIP"].normal is not None and by_name["TIP"].normal.isclose(Direction(1, 0, 0), tolerance=1e-9)
    assert root.centroid is not None and root.centroid.isclose(Vec3(-0.01, 0.0, 0.0), tolerance=1e-9)
    assert root.length is None
    assert by_name["BEAM__EXT"].normal is None
    assert beam.measure == pytest.approx(2e-6, rel=1e-9)
    assert beam.centroid is not None and beam.centroid.isclose(Vec3(0.0, 0.0, 0.0), tolerance=1e-9)
    assert beam.bbox is not None and beam.bbox.diagonal == pytest.approx(math.sqrt(6e-4), rel=1e-9)


def test_an_interface_normal_points_out_of_its_first_body(tmp_path: Path) -> None:
    _, beam = _beam()
    block = TaggedPart([b3d.Pos(15, 0, 0) * b3d.Box(10, 10, 10)], tags={TagKey.MATERIAL: "Steel316"}, name="Block")
    block.color = b3d.Color("green")

    mesher, _ = _mesh(tmp_path / "pair.step", beam, block)
    interface: BoundaryGroup = next(group for group in mesher.boundary_groups if group.name == "BEAM__BLOCK")

    assert interface.bodies == ("BEAM", "BLOCK")
    assert interface.normal is not None and interface.normal.isclose(Direction(1, 0, 0), tolerance=1e-9)


def test_a_curved_body_is_bounded_by_its_mesh_nodes(tmp_path: Path) -> None:
    ring = TaggedPart([b3d.Torus(15, 5)], tags={TagKey.MATERIAL: "Steel316"}, name="Beam")
    ring.color = b3d.Color("gray")

    mesher, _ = _mesh(tmp_path / "ring.step", ring)
    body = mesher.physical_groups[0]
    shell: BoundaryGroup = mesher.boundary_groups[0]

    assert body.bbox is not None
    assert body.bbox.high.x == pytest.approx(0.02, rel=1e-6) and body.bbox.low.y == pytest.approx(-0.02, rel=1e-6)
    assert body.bbox.high.z <= 0.0051 and body.bbox.low.z >= -0.0051
    assert shell.name == "BEAM__EXT" and shell.normal is None
