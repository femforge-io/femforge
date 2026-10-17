"""End-to-end (needs gmsh + build123d): a planar STEP of tagged faces meshes as a 2D model, with surface bodies and curve boundaries."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("gmsh")
pytest.importorskip("build123d")

import build123d as b3d  # imported after the gate so the file still collects where the CAD stack is absent

from femforge.common.frame import Axisymmetric
from femforge.common.tags import TagKey
from femforge.config import SimulationConfig
from femforge.geometry.tags import TaggedCompound, TaggedFace
from femforge.materials.air import Air
from femforge.materials.copper import Copper
from femforge.materials.steel import Steel_316
from femforge.meshing.boundary import BoundaryGroup
from femforge.meshing.config import MeshConfig
from femforge.meshing.gmsh_options import GmshOptions
from femforge.meshing.groups import PhysicalGroup
from femforge.meshing.mesher import Mesher
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import U


AIR = "AIR_MATERIALAIR"
COPPER = "COPPER_MATERIALCOPPER"
STEEL = "STEEL316_MATERIALSTEEL316"


def _tagged_rect(width: float, height: float, x: float, material_tag: str) -> TaggedFace:
    """A rectangle in the XY plane centred at (x, 0), tagged with the material the mesher matches it by."""
    placed: b3d.Face = b3d.Pos(x, 0) * b3d.Rectangle(width, height).face()
    return TaggedFace(placed, tags={TagKey.MATERIAL: material_tag})


def _write_planar_step(path: Path) -> None:
    """
    Export a 20x20 mm air rectangle spanning x in [0, 20] with two abutting 4x8 mm steel/copper rectangles inside it,
    the steel rod's left edge on the x=0 symmetry axis (dimensions in mm; gmsh converts to metres).
    """
    scene = TaggedCompound(
        children=[
            _tagged_rect(20, 20, 10, "Air"),
            _tagged_rect(4, 8, 2, "Steel316"),
            _tagged_rect(4, 8, 6, "Copper"),
        ]
    )
    scene.export_step(str(path))


def _config(step: Path) -> SimulationConfig:
    """A 2D simulation config over the planar STEP, with a finer size requested on the copper region."""
    return SimulationConfig(
        frame=Axisymmetric(),
        mesh=MeshConfig(
            step=str(step),
            global_mesh_size=2 * U.mm,
            gmsh_options=GmshOptions(mesh_size_max=2 * U.mm),
        ),
        domain=DomainConfig(
            materials=[Air(), Copper(), Steel_316()],
            tags=[
                EntityTag(selector={TagKey.MATERIAL: "Air"}),
                EntityTag(selector={TagKey.MATERIAL: "Copper"}, mesh_size=0.5 * U.mm),
                EntityTag(selector={TagKey.MATERIAL: "Steel316"}),
            ],
        ),
    )


@pytest.fixture(scope="module")
def meshed_2d(tmp_path_factory: pytest.TempPathFactory) -> Mesher:
    """Mesh the planar scene once (gmsh only -- no ElmerGrid/ElmerSolver) and share the result across the assertions."""
    tmp_path: Path = tmp_path_factory.mktemp("planar")
    step: Path = tmp_path / "planar.step"
    _write_planar_step(step)
    mesher: Mesher = Mesher(_config(step))
    mesher.generate()
    return mesher


def test_step_faces_keep_their_labels_and_become_body_groups(meshed_2d: Mesher) -> None:
    # The tagged faces survived export_step's flatten and matched their materials, so each is a dim-2 body group.
    groups: dict[str, PhysicalGroup] = {group.name: group for group in meshed_2d.physical_groups}
    assert set(groups) == {AIR, COPPER, STEEL}
    assert all(group.dim == 2 for group in groups.values())
    assert groups[COPPER].material.tag == "Copper"
    assert groups[STEEL].material.tag == "Steel316"
    # The per-region EntityTag rode through, so region mesh sizing is addressable in 2D too.
    assert groups[COPPER].tags[0].mesh_size == 0.5 * U.mm


def test_boundaries_are_curve_groups_named_by_adjacency(meshed_2d: Mesher) -> None:
    boundaries: dict[str, BoundaryGroup] = {group.name: group for group in meshed_2d.boundary_groups}
    assert set(boundaries) == {
        f"{AIR}__EXT",
        f"{AIR}__AXIS",
        f"{STEEL}__AXIS",
        f"{AIR}__{COPPER}",
        f"{AIR}__{STEEL}",
        f"{COPPER}__{STEEL}",
    }
    # In a 2D model the boundaries are curves, one dimension below the surface bodies.
    assert all(group.dim == 1 for group in boundaries.values())
    # Only the air box's non-axis outer edges are external; the x=0 edges split into per-body __AXIS groups instead.
    assert [name for name, group in boundaries.items() if group.is_external] == [f"{AIR}__EXT"]
    assert {name for name, group in boundaries.items() if group.is_axis} == {f"{AIR}__AXIS", f"{STEEL}__AXIS"}
    assert len(boundaries[f"{AIR}__EXT"].entity_tags) == 3  # right, top, and bottom sides of the air rectangle
    assert len(boundaries[f"{AIR}__AXIS"].entity_tags) == 2  # the air's left side, split where the steel rod meets the axis
    assert len(boundaries[f"{STEEL}__AXIS"].entity_tags) == 1  # the rod's own edge on the axis


def test_mesh_is_written_with_triangles_counted(meshed_2d: Mesher) -> None:
    assert Path(meshed_2d.output_filename).is_file()
    assert meshed_2d.element_count is not None and meshed_2d.element_count > 0
    # element_count_3D is defined only for a 3D model, so a 2D run reports None rather than a misleading zero.
    assert meshed_2d.element_count_3D is None


def test_child_processes_inherit_the_full_path_after_meshing(meshed_2d: Mesher) -> None:
    child = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('PATH', ''))"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert child.stdout.strip() == os.environ.get("PATH", "")
