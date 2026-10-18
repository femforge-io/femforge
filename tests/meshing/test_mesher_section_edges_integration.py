"""End-to-end (needs gmsh + build123d): named faces of a solid model section into named curve groups of a 2D mesh."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("gmsh")
pytest.importorskip("build123d")

import build123d as b3d  # imported after the gate so the file still collects where the CAD stack is absent
import gmsh

from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.config import SimulationConfig
from femforge.frames import Axis, Axisymmetric, Cartesian2D
from femforge.geometry.tags import TaggedCompound, TaggedEdge, TaggedFace, TaggedPart
from femforge.materials.steel import Steel_316
from femforge.meshing.backend import MeshError
from femforge.meshing.boundary import BoundaryGroup
from femforge.meshing.config import MeshConfig
from femforge.meshing.mesher import Mesher
from femforge.meshing.section_edges import edges_path
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import U


def test_gmsh_imports_no_step_names_on_free_edges(tmp_path: Path) -> None:
    face = TaggedFace(b3d.Rectangle(100, 20, align=(b3d.Align.MIN, b3d.Align.MIN)).face(), tags={TagKey.MATERIAL: "Steel316"})
    edge = TaggedEdge(b3d.Line((0, 0), (0, 20)).edge(), name="Left")
    step: Path = tmp_path / "labels.step"
    TaggedCompound(children=[face, edge]).export_step(str(step))

    if not gmsh.isInitialized():
        gmsh.initialize(interruptible=False)

    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.option.setNumber("Geometry.OCCImportLabels", 1)
        gmsh.model.occ.importShapes(str(step), highestDimOnly=False)
        gmsh.model.occ.synchronize()
        surfaces: list[str] = [gmsh.model.getEntityName(2, tag) for _, tag in gmsh.model.getEntities(dim=2)]
        curves: list[str] = [gmsh.model.getEntityName(1, tag) for _, tag in gmsh.model.getEntities(dim=1)]
    finally:
        gmsh.clear()

    assert len(surfaces) == 1 and "Material=Steel316" in surfaces[0]
    assert len(curves) == 5 and not any(curves)


def _prism(*named: str) -> TaggedCompound:
    """A 100 x 20 x 10 mm steel prism from the origin, with the named end faces marked."""
    box = b3d.Box(100, 20, 10, align=(b3d.Align.MIN, b3d.Align.MIN, b3d.Align.MIN))
    prism = TaggedPart([box], tags={TagKey.MATERIAL: "Steel316"}, name="Prism")
    prism.color = b3d.Color("gray")
    ends = box.faces().sort_by(b3d.Axis.X)
    faces = {"Left": ends[0], "Right": ends[-1]}
    markers: list[TaggedFace] = []
    for name in named:
        marker = TaggedFace(faces[name], name=name)
        marker.color = b3d.Color("red")
        markers.append(marker)

    return TaggedCompound(children=[prism, *markers])


def _mesh(tmp_path: Path, frame: Axisymmetric | Cartesian2D, *named: str) -> Mesher:
    authored: Path = tmp_path / "authored.step"
    _prism(*named).export_step(str(authored))
    step: Path = frame.prepare_geometry(authored, tmp_path / "geometry.step")
    domain = DomainConfig(
        materials=[Steel_316()],
        tags=[EntityTag(selector={TagKey.NAME: name}) for name in ("Prism", *named)],
    )
    config = SimulationConfig(frame=frame, mesh=MeshConfig(step=str(step), global_mesh_size=2.5 * U.mm), domain=domain)
    mesher = Mesher(config)
    mesher.generate()

    return mesher


@pytest.fixture(scope="module")
def end_edges(tmp_path_factory: pytest.TempPathFactory) -> Mesher:
    frame = Cartesian2D(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1)), thickness=10 * U.mm)

    return _mesh(tmp_path_factory.mktemp("edges"), frame, "Left", "Right")


def test_named_faces_section_into_named_curve_groups_covering_the_boundary(end_edges: Mesher) -> None:
    by_name: dict[str, BoundaryGroup] = {group.name: group for group in end_edges.boundary_groups}

    assert [group.name for group in end_edges.physical_groups] == ["PRISM"]
    assert set(by_name) == {"PRISM__EXT", "LEFT", "RIGHT"}
    assert all(group.dim == 1 and not group.on_axis for group in by_name.values())
    assert len(by_name["PRISM__EXT"].entity_tags) == 2
    assert not by_name["PRISM__EXT"].named


def test_a_named_curve_group_records_its_length_body_and_tag(end_edges: Mesher) -> None:
    left: BoundaryGroup = next(group for group in end_edges.boundary_groups if group.name == "LEFT")

    assert left.named and left.is_external
    assert left.bodies == ("PRISM",)
    assert left.measure == pytest.approx(0.02, rel=1e-6)
    assert len(left.tags) == 1 and left.tags[0].selector == {TagKey.NAME: "Left"}


def test_an_axisymmetric_section_names_the_edge_of_a_named_face_too(tmp_path: Path) -> None:
    frame = Axisymmetric(axis=Axis(origin=Vec3(0, 10, 0), direction=Direction(1, 0, 0)), radial=Direction(0, 0, 1))
    mesher = _mesh(tmp_path, frame, "Right")
    by_name: dict[str, BoundaryGroup] = {group.name: group for group in mesher.boundary_groups}

    assert set(by_name) == {"PRISM__EXT", "PRISM__AXIS", "RIGHT"}
    assert by_name["RIGHT"].measure == pytest.approx(0.01, rel=1e-6)


def test_the_curves_of_a_colored_section_take_no_body_name(tmp_path: Path) -> None:
    mesher = _mesh(tmp_path, Axisymmetric(axis=Axis(origin=Vec3(0, 10, 0), direction=Direction(1, 0, 0)), radial=Direction(0, 0, 1)))
    by_name: dict[str, BoundaryGroup] = {group.name: group for group in mesher.boundary_groups}

    assert set(by_name) == {"PRISM__EXT", "PRISM__AXIS"}
    assert not any(group.named for group in by_name.values())


def test_an_edge_file_that_names_a_missing_edge_raises(tmp_path: Path) -> None:
    frame = Cartesian2D(normal=Axis(origin=Vec3(0, 0, 5), direction=Direction(0, 0, 1)), thickness=10 * U.mm)
    authored: Path = tmp_path / "authored.step"
    _prism("Left").export_step(str(authored))
    step: Path = frame.prepare_geometry(authored, tmp_path / "geometry.step")
    record: Path = edges_path(step)
    _ = record.write_bytes(record.read_bytes().replace(b'"length":20.0', b'"length":21.0'))
    config = SimulationConfig(
        frame=frame,
        mesh=MeshConfig(step=str(step), global_mesh_size=2.5 * U.mm),
        domain=DomainConfig(materials=[Steel_316()], tags=[EntityTag(selector={TagKey.NAME: "Left"})]),
    )

    with pytest.raises(MeshError, match="'Name=Left' is missing from the imported STEP"):
        Mesher(config).generate()
