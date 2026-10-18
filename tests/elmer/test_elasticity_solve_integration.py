"""End-to-end (gmsh + Elmer gated): a clamped steel cantilever with a tip load matches Timoshenko beam theory and its own Clapeyron energy."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from femforge.common.tags import TagKey
from femforge.common.vector import Vec3
from femforge.elmer.runtime.native import NativeElmer
from femforge.elmer.runtime.solve import solve_case
from femforge.elmer.runtime.tools import ElmerToolsLocations
from femforge.elmer.variables import ElmerVariable
from femforge.materials.steel import Steel_316
from femforge.physical.conditions import FixedDisplacement, SurfaceForce
from femforge.physical.domain import EntityTag
from femforge.physical.units import quantity
from femforge.run.entities import write_entities
from femforge.run.fields import FieldMesh
from femforge.run.mechanical import VOIGT_COMPONENTS, clapeyron_energy, strain_energy

from tests.elmer.test_thermal_solve_integration import _find_elmer

LENGTH = 0.1
"""`L` `[m]`: Beam length along x."""

SIDE = 0.01
"""`b` `[m]`: Width and depth of the square cross-section."""

LOAD = 100.0
"""`F` `[N]`: Tip load along +z."""

YOUNGS = 193e9
"""`E` `[Pa]`: Steel 316 Young's modulus."""

POISSON = 0.27
"""`ν` `[-]`: Steel 316 Poisson's ratio."""

SHEAR_COEFFICIENT = 0.848
"""`κ` `[-]`: Timoshenko shear coefficient of a square section at this Poisson's ratio."""


def _solve_cantilever(tmp_path: Path, elmer: dict[str, str]) -> tuple[FieldMesh, str, str]:
    """Mesh, solve and load the cantilever; return the field mesh, the sif and the solver log."""
    import gmsh

    from femforge.elmer.mesh_names import parse_mesh_names, reconcile_ids
    from femforge.elmer.sim import Sim
    from femforge.meshing.boundary import BoundaryGroup
    from femforge.meshing.groups import PhysicalGroup

    msh: Path = tmp_path / "beam.msh"

    if not gmsh.isInitialized():
        gmsh.initialize(interruptible=False)

    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.option.setNumber("Mesh.RecombineAll", 0)
        gmsh.option.setNumber("Mesh.Algorithm", 6)
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.002)
        gmsh.option.setNumber("Mesh.ElementOrder", 2)

        volume: int = gmsh.model.occ.addBox(0, 0, 0, LENGTH, SIDE, SIDE)
        gmsh.model.occ.synchronize()

        faces: list[int] = [tag for _, tag in gmsh.model.getBoundary([(3, volume)], oriented=False)]
        root: list[int] = [face for face in faces if abs(gmsh.model.occ.getCenterOfMass(2, face)[0]) < 1e-9]
        tip: list[int] = [face for face in faces if abs(gmsh.model.occ.getCenterOfMass(2, face)[0] - LENGTH) < 1e-9]
        shell: list[int] = [face for face in faces if face not in root + tip]

        beam_id: int = gmsh.model.addPhysicalGroup(3, [volume], name="BEAM")
        clamp = EntityTag(selector={TagKey.NAME: "Root"}, conditions=[FixedDisplacement()])
        load = EntityTag(selector={TagKey.NAME: "Tip"}, conditions=[SurfaceForce(force=quantity(Vec3(0, 0, LOAD), "N"))])

        def named(name: str, entities: list[int], tag: EntityTag) -> BoundaryGroup:
            measure: float = sum(gmsh.model.occ.getMass(2, face) for face in entities)

            return BoundaryGroup(gmsh.model.addPhysicalGroup(2, entities, name=name), name, ("BEAM",), entities, tags=[tag], measure=measure, named=True)

        boundary_groups: list[BoundaryGroup] = [
            BoundaryGroup(gmsh.model.addPhysicalGroup(2, shell, name="BEAM__EXT"), "BEAM__EXT", ("BEAM",), shell),
            named("ROOT", root, clamp),
            named("TIP", tip, load),
        ]

        gmsh.model.mesh.generate(3)
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.write(str(msh))
    finally:
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        gmsh.clear()

    groups: list[PhysicalGroup] = [PhysicalGroup(3, beam_id, "BEAM", Steel_316(), [], [volume])]

    backend = NativeElmer(tools=ElmerToolsLocations(ElmerGrid=elmer["ElmerGrid"], ElmerSolver=elmer["ElmerSolver"]))
    _ = backend.grid_convert(tmp_path, msh.name, out_dir=tmp_path, autoclean=True, keep_mesh_dir=True)
    mesh_dir: Path = tmp_path / "beam"

    assert reconcile_ids(parse_mesh_names((mesh_dir / "mesh.names").read_text()), groups, boundary_groups) == []

    Sim(groups, physics="linear_elasticity", boundary_groups=boundary_groups).generate().write(str(mesh_dir))
    sif: str = (mesh_dir / "case.sif").read_text()

    solve = solve_case(backend, mesh_dir)
    solver_log: str = (mesh_dir / "elmersolver.log").read_text(encoding="utf-8")

    assert solve.result.returncode == 0, f"ElmerSolver failed:\n{solver_log[-2000:]}"

    _ = write_entities(mesh_dir, groups, boundary_groups)

    return FieldMesh.load(mesh_dir), sif, solver_log


def _gated_elmer() -> dict[str, str]:
    """Skip unless gmsh, pyvista and a full Elmer build are present."""
    pytest.importorskip("gmsh")
    pytest.importorskip("pyvista")
    elmer: dict[str, str] | None = _find_elmer()

    if elmer is None:
        pytest.skip("Elmer (ElmerGrid/ElmerSolver) not found on this machine")

    return elmer


def test_a_tip_loaded_cantilever_matches_timoshenko_and_clapeyron(tmp_path: Path) -> None:
    """Observed on Elmer 26.2: tip deflection 2.076e-4 m against 2.088e-4 m, strain energy 1.024e-2 J against Clapeyron 1.038e-2 J."""
    mesh, sif, solver_log = _solve_cantilever(tmp_path, _gated_elmer())

    assert "Displace Mesh = False" in sif and "Poisson Ratio = 0.27" in sif
    assert "Force 3 = 1000000.0" in sif
    assert "BEAM__EXT" not in sif

    matches: list[str] = re.findall(r"\(NRM,RELC\): \(\s*([0-9.eE+\-]+)", solver_log)
    norms: list[float] = [float(value) for value in matches]

    assert norms and all(math.isfinite(norm) for norm in norms)

    names: list[str] = mesh.array_names

    for variable in (ElmerVariable.DISPLACEMENT, ElmerVariable.VON_MISES, ElmerVariable.PRINCIPAL_STRESS, ElmerVariable.STRAIN_ENERGY_DENSITY):
        assert variable.lower() in names, names

    for variable in (ElmerVariable.STRESS, ElmerVariable.STRAIN):
        assert [f"{variable.lower()}_{component}" for component in VOIGT_COMPONENTS] == [name for name in names if name.startswith(f"{variable.lower()}_")]

    stress = mesh.get(ElmerVariable.STRESS).where_body("BEAM")
    strain = mesh.get(ElmerVariable.STRAIN).where_body("BEAM")

    assert stress.name == "stress" and strain.name == "strain"
    assert stress.values.shape[1] == 6 and strain.values.shape[1] == 6

    area: float = SIDE * SIDE
    mid_stress: list[float] = cast("list[float]", np.asarray(stress.where_pos(x=(0.04, 0.06)).mean()).tolist())
    mid_strain: list[float] = cast("list[float]", np.asarray(strain.where_pos(x=(0.04, 0.06)).mean()).tolist())
    shear_modulus: float = YOUNGS / (2 * (1 + POISSON))

    assert mid_stress[5] == pytest.approx(LOAD / area, rel=0.05)
    assert abs(mid_stress[3]) < 0.05 * LOAD / area and abs(mid_stress[4]) < 0.05 * LOAD / area
    assert mid_strain[5] / mid_stress[5] == pytest.approx(1 / (2 * shear_modulus), rel=0.05)

    inertia: float = SIDE**4 / 12
    deflection: float = LOAD * LENGTH**3 / (3 * YOUNGS * inertia) + LOAD * LENGTH / (SHEAR_COEFFICIENT * shear_modulus * area)
    tip_displacement: list[float] = cast("list[float]", np.asarray(mesh.get(ElmerVariable.DISPLACEMENT).where_boundary("TIP").surface_mean()).tolist())

    assert tip_displacement[2] == pytest.approx(deflection, rel=0.05)

    stored: float = strain_energy(mesh, "BEAM")
    work: float = clapeyron_energy(mesh, "TIP", Vec3(0, 0, LOAD))

    assert work == pytest.approx(0.5 * LOAD * tip_displacement[2])
    assert stored == pytest.approx(work, rel=0.03)
