"""End-to-end (gmsh + Elmer gated): thermal sifs built on adjacency-derived surface groups solve in ElmerSolver."""

from __future__ import annotations

import glob
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from femforge.elmer.runtime.native import NativeElmer
from femforge.elmer.runtime.solve import solve_case
from femforge.elmer.runtime.tools import ElmerToolsLocations
from femforge.physical.domain import EntityTag
from femforge.physical.conditions import Convection, ConditionUnion, FixedTemperature
from femforge.materials.air import Air
from femforge.materials.pcb import FR4
from femforge.materials.properties import MaterialProperties
from femforge.physical.units import U

# A box region: name (for body + boundary group names), material, axis-aligned box (x, y, z, dx, dy, dz), and authored conditions.
Box = tuple[float, float, float, float, float, float]


@dataclass(frozen=True)
class _Region:
    name: str
    material: MaterialProperties
    box: Box
    conditions: list[ConditionUnion]


def _find_elmer() -> dict[str, str] | None:
    """Locate ElmerGrid/ElmerSolver via PATH or a standard Windows install, or None if either is missing."""
    tools: dict[str, str] = {}
    for name in ("ElmerGrid", "ElmerSolver"):
        found: str | None = shutil.which(name)
        if found is None:
            candidates: list[str] = glob.glob(rf"C:\Program Files\Elmer *\bin\{name}.exe")
            found = candidates[0] if candidates else None
        if found is None:
            return None
        tools[name] = found
    return tools


def _native_backend(elmer: dict[str, str]) -> NativeElmer:
    """A native backend over the gated Elmer tools."""
    return NativeElmer(tools=ElmerToolsLocations(ElmerGrid=elmer["ElmerGrid"], ElmerSolver=elmer["ElmerSolver"]))


def _boundary_blocks(sif: str) -> dict[str, str]:
    """Map each emitted boundary's name (its `! <name>` comment) to the block body text, for routing assertions."""
    return {m.group(1): m.group(2) for m in re.finditer(r"! (\S+)\nBoundary Condition \d+\n(.*?)\nEnd", sif, re.DOTALL)}


def _contains(box: Box, point: tuple[float, float, float]) -> bool:
    """Whether an axis-aligned box contains a point (used to re-identify each meshed volume by its centroid)."""
    x, y, z, dx, dy, dz = box
    return x <= point[0] <= x + dx and y <= point[1] <= y + dy and z <= point[2] <= z + dz


def _solve_thermal(tmp_path: Path, elmer: dict[str, str], regions: list[_Region]) -> tuple[dict[str, str], float]:
    """Mesh the regions through the ElmerGrid -> reconcile -> Sim -> ElmerSolver pipeline; return (boundary blocks, final result norm)."""
    import gmsh  # imported here so the file still collects where gmsh is absent

    from femforge.elmer.mesh_names import parse_mesh_names, reconcile_ids
    from femforge.elmer.sim import Sim
    from femforge.meshing.boundary import BoundaryGroup, partition_boundary_faces, read_face_adjacency
    from femforge.meshing.groups import PhysicalGroup

    msh: Path = tmp_path / "model.msh"
    if not gmsh.isInitialized():
        gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        # clear() keeps options earlier tests set; force triangle surfaces so the all-triangle HXT 3D mesher accepts them.
        gmsh.option.setNumber("Mesh.RecombineAll", 0)
        gmsh.option.setNumber("Mesh.Algorithm", 6)  # 2D Frontal-Delaunay: triangles
        gmsh.option.setNumber("Mesh.Algorithm3D", 10)  # HXT: robust all-tet, avoids the pyramid quirk at shared faces
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.5)

        box_tags: list[int] = [gmsh.model.occ.addBox(*region.box) for region in regions]
        first, rest = box_tags[0], box_tags[1:]
        _ = gmsh.model.occ.fragment([(3, first)], [(3, t) for t in rest])
        gmsh.model.occ.synchronize()

        # Re-identify each meshed volume with the region whose box contains its centroid.
        body_volumes: dict[str, list[int]] = {region.name: [] for region in regions}
        for _, tag in gmsh.model.getEntities(3):
            centroid = gmsh.model.occ.getCenterOfMass(3, tag)
            for region in regions:
                if _contains(region.box, centroid):
                    body_volumes[region.name].append(tag)
                    break

        body_groups: list[tuple[str, list[int]]] = [(region.name, body_volumes[region.name]) for region in regions]
        body_ids: dict[str, int] = {name: gmsh.model.addPhysicalGroup(3, vtags, name=name) for name, vtags in body_groups}
        partitions = partition_boundary_faces(read_face_adjacency(body_groups))
        boundary_groups: list[BoundaryGroup] = [
            BoundaryGroup(gmsh.model.addPhysicalGroup(2, list(p.face_tags), name=p.name), p.name, p.bodies, list(p.face_tags)) for p in partitions
        ]

        gmsh.model.mesh.generate(3)
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)  # ElmerGrid reads gmsh format 2.2
        gmsh.write(str(msh))
    finally:
        gmsh.clear()

    groups: list[PhysicalGroup] = [
        PhysicalGroup(3, body_ids[r.name], r.name, r.material, [EntityTag(selector={"Region": r.name}, conditions=r.conditions)], [body_ids[r.name]]) for r in regions
    ]

    backend: NativeElmer = _native_backend(elmer)
    _ = backend.grid_convert(tmp_path, msh.name, out_dir=tmp_path, autoclean=True, keep_mesh_dir=True)
    mesh_dir: Path = tmp_path / "model"
    assert (mesh_dir / "mesh.header").is_file(), "ElmerGrid did not produce an Elmer mesh"

    # The crux: ElmerGrid renumbered the groups, so the sif must target the renumbered ids. Reconcile exactly as the driver does.
    assert reconcile_ids(parse_mesh_names((mesh_dir / "mesh.names").read_text()), groups, boundary_groups) == []

    Sim(groups, physics="thermal", boundary_groups=boundary_groups).generate().write(str(mesh_dir))
    sif: str = (mesh_dir / "case.sif").read_text()

    solve = solve_case(backend, mesh_dir)
    solver_log: str = (mesh_dir / "elmersolver.log").read_text(encoding="utf-8")
    assert solve.result.returncode == 0, f"ElmerSolver failed:\n{solver_log[-2000:]}"
    assert "ELMER SOLVER FINISHED" in solver_log, f"solver did not finish cleanly:\n{solver_log[-2000:]}"

    norms: list[str] = re.findall(r"Result Norm\s*:\s*([0-9.eE+\-]+)", solver_log)
    assert norms, f"no Result Norm in solver output:\n{solver_log[-2000:]}"
    return _boundary_blocks(sif), float(norms[-1])


def _gated_elmer() -> dict[str, str]:
    """Skip unless both gmsh and a full Elmer build are present."""
    pytest.importorskip("gmsh")  # gate: skip where the CAD stack is absent (sandbox/CI)
    elmer: dict[str, str] | None = _find_elmer()
    if elmer is None:
        pytest.skip("Elmer (ElmerGrid/ElmerSolver) not found on this machine")
    return elmer


def test_thermal_sif_solves_end_to_end(tmp_path: Path) -> None:
    elmer = _gated_elmer()
    # FR4 board (fixed temperature) beside an AIR block (convective exchange), sharing the x=1 face.
    board_bc: list[ConditionUnion] = [FixedTemperature(value=350 * U.K)]
    air_bc: list[ConditionUnion] = [Convection(coefficient=15 * U.W / (U.m**2 * U.K), ext_temperature=300 * U.K)]
    regions = [
        _Region("FR4", FR4(), (0, 0, 0, 1, 1, 1), board_bc),
        _Region("AIR", Air(), (1, 0, 0, 1, 1, 1), air_bc),
    ]
    blocks, norm = _solve_thermal(tmp_path, elmer, regions)

    # The board's fixed temperature reaches every FR4-bordering surface (its shell + the interface); the air's
    # convection is external-only, so the interface block carries the Dirichlet alone.
    assert set(blocks) == {"FR4__EXT_fixed_temperature", "AIR__FR4_fixed_temperature", "AIR__EXT_convection"}
    assert "Target Boundaries(1) = 3" in blocks["FR4__EXT_fixed_temperature"]  # FR4__EXT -> reconciled Elmer id 3
    assert "Temperature = 350.0" in blocks["FR4__EXT_fixed_temperature"]
    assert "Target Boundaries(1) = 1" in blocks["AIR__EXT_convection"]  # AIR__EXT -> reconciled Elmer id 1
    assert "Heat Transfer Coefficient = 15.0" in blocks["AIR__EXT_convection"]
    assert "Heat Transfer Coefficient" not in blocks["AIR__FR4_fixed_temperature"]

    # With the 350 K Dirichlet on real elements the field blends 300 K (convection) and 350 K (board) — norm ~324 K.
    # A BC landing on no surface would collapse to uniform 300 K (norm ~300), which this interval excludes.
    assert 305.0 < norm < 350.0, f"temperature norm {norm} K is not a 300-350 K blend; the fixed-temperature BC likely didn't apply"


def test_conduction_gradient_through_board(tmp_path: Path) -> None:
    elmer = _gated_elmer()
    # heater [0,1] | board [1,3] | air [3,4] in a line: the conditionless board is pinned hot on one face, cooled (via the air)
    # on the other, so the temperature gradient runs *through the solid board* rather than only through the air.
    heater_bc: list[ConditionUnion] = [FixedTemperature(value=350 * U.K)]
    air_bc: list[ConditionUnion] = [Convection(coefficient=15 * U.W / (U.m**2 * U.K), ext_temperature=300 * U.K)]
    regions = [
        _Region("HEATER", FR4(), (0, 0, 0, 1, 1, 1), heater_bc),
        _Region("BOARD", FR4(), (1, 0, 0, 2, 1, 1), []),  # no condition: it conducts
        _Region("AIR", Air(), (3, 0, 0, 1, 1, 1), air_bc),
    ]
    blocks, norm = _solve_thermal(tmp_path, elmer, regions)

    # The heater's fixed temperature reaches the board's hot face (the BOARD__HEATER interface)...
    assert "BOARD__HEATER_fixed_temperature" in blocks
    # ...while the board's own side faces carry no condition (adiabatic), so heat must conduct across the board to the air.
    assert "BOARD__EXT_fixed_temperature" not in blocks
    assert "BOARD__EXT_convection" not in blocks
    # The film is external-only: the air↔board interface conducts, and the air sheds heat through its own shell.
    assert "AIR__BOARD_convection" not in blocks
    assert "AIR__EXT_convection" in blocks

    # A real blended field (heater 350 K -> air 300 K conducting through the board), not a collapse to a single temperature.
    assert 305.0 < norm < 350.0, f"temperature norm {norm} K is not a 300-350 K gradient through the board"
