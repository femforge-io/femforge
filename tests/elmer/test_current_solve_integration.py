"""End-to-end (gmsh + Elmer gated): the current-conduction sif solves in ElmerSolver and reproduces an analytic resistance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from femforge.elmer.runtime.native import NativeElmer
from femforge.elmer.runtime.solve import solve_case
from femforge.physical.conditions import ConditionUnion, FixedVoltage
from femforge.physical.domain import EntityTag
from femforge.materials.copper import Copper
from femforge.materials.properties import MaterialProperties
from femforge.physical.units import U
from femforge.run.electrical import electrode_currents, read_boundary_names, resistance
from femforge.run.parse import _parse_scalars
from tests.elmer.test_thermal_solve_integration import _contains, _gated_elmer, _native_backend, Box


@dataclass(frozen=True)
class _Region:
    name: str
    material: MaterialProperties
    box: Box
    conditions: list[ConditionUnion]


# Bar: 1 m long, 0.1 x 0.1 m cross-section. Pads: 0.02 m thick slabs on each end.
_L, _A_SIDE = 1.0, 0.1
_PAD = 0.02
_SIGMA = 5.96e7  # the copper 20 degC reference; the material is pinned to it below so the analytic check is exact
_VOLTS = 1e-3

# FixedVoltage clamps each pad's whole surface (equipotential electrode), so only the bar contributes resistance.
R_ANALYTIC = _L / (_SIGMA * _A_SIDE * _A_SIDE)  # rho * L / A ~ 1.678e-6 ohm


def _static_copper() -> MaterialProperties:
    """Copper with sigma pinned constant at the 20 degC reference so the analytic check is exact."""
    from femforge.physical.property_functions import Static

    copper = Copper()
    copper.electrical.conductivity = Static(value=_SIGMA * U.S / U.m)
    return copper


def _regions() -> list[_Region]:
    entry: list[ConditionUnion] = [FixedVoltage(value=_VOLTS * U.V)]
    exit_: list[ConditionUnion] = [FixedVoltage(value=0 * U.V)]
    copper = _static_copper()
    return [
        _Region("PAD_A", copper, (-_PAD, 0, 0, _PAD, _A_SIDE, _A_SIDE), entry),
        _Region("BAR", copper, (0, 0, 0, _L, _A_SIDE, _A_SIDE), []),
        _Region("PAD_B", copper, (_L, 0, 0, _PAD, _A_SIDE, _A_SIDE), exit_),
    ]


def _solve_current(tmp_path: Path, elmer: dict[str, str], regions: list[_Region], physics: str = "current_conduction") -> Path:
    """Mesh + reconcile + Sim + ElmerSolver for the current-conduction (or coupled joule-heating) physics; returns the Elmer mesh/run dir."""
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
        gmsh.option.setNumber("Mesh.RecombineAll", 0)
        gmsh.option.setNumber("Mesh.Algorithm", 6)
        gmsh.option.setNumber("Mesh.Algorithm3D", 10)
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.025)

        box_tags: list[int] = [gmsh.model.occ.addBox(*region.box) for region in regions]
        _ = gmsh.model.occ.fragment([(3, box_tags[0])], [(3, t) for t in box_tags[1:]])
        gmsh.model.occ.synchronize()

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
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.write(str(msh))
    finally:
        gmsh.clear()

    groups: list[PhysicalGroup] = [
        PhysicalGroup(3, body_ids[r.name], r.name, r.material, [EntityTag(selector={"Region": r.name}, conditions=r.conditions)], [body_ids[r.name]]) for r in regions
    ]

    backend: NativeElmer = _native_backend(elmer)
    _ = backend.grid_convert(tmp_path, msh.name, out_dir=tmp_path, autoclean=True, keep_mesh_dir=True)
    mesh_dir: Path = tmp_path / "model"

    assert reconcile_ids(parse_mesh_names((mesh_dir / "mesh.names").read_text()), groups, boundary_groups) == []

    Sim(groups, physics=physics, boundary_groups=boundary_groups).generate().write(str(mesh_dir))

    solve = solve_case(backend, mesh_dir)
    solver_log: str = (mesh_dir / "elmersolver.log").read_text(encoding="utf-8")
    assert solve.result.returncode == 0, f"ElmerSolver failed:\n{solver_log[-3000:]}"
    assert "ELMER SOLVER FINISHED" in solver_log, f"solver did not finish cleanly:\n{solver_log[-3000:]}"
    return mesh_dir


def test_bar_resistance_matches_analytic(tmp_path: Path) -> None:
    elmer = _gated_elmer()
    mesh_dir = _solve_current(tmp_path, elmer, _regions())

    raw: dict[str, float] = _parse_scalars(mesh_dir)
    assert raw, f"no SaveScalars output found in {mesh_dir}"

    # --- R from total Joule power: R = V^2 / P (volume integral; independent of boundary-flux quirks).
    power = next((value for name, value in raw.items() if "joule" in name.lower()), None)
    assert power is not None, f"no Joule Heating column in {sorted(raw)}"
    r_from_power = _VOLTS**2 / power
    assert r_from_power == pytest.approx(R_ANALYTIC, rel=0.05), f"R from Joule power {r_from_power} vs analytic {R_ANALYTIC}"

    # --- R from the electrode current: R = V / I over the pad electrode boundaries.
    currents = electrode_currents(raw, read_boundary_names(mesh_dir / "boundaries.yml"))
    assert currents, f"no diffusive-flux electrode columns in {sorted(raw)}"
    # The pad<->bar interface carries the full terminal current; pick the largest-magnitude electrode flux.
    i_terminal = max(currents.values(), key=abs)
    r_from_flux = resistance(_VOLTS, i_terminal)
    assert r_from_flux == pytest.approx(R_ANALYTIC, rel=0.05), f"R from electrode flux {r_from_flux} vs analytic {R_ANALYTIC} (currents: {currents})"

    # Internal electrode boundaries integrate one-sided (measured): the conductor side reports the terminal
    # current, the equipotential-pad sides ~0. Exactly one dominant flux must exist, and the pads' external
    # shells must carry (numerically) nothing — the pad interiors are equipotential.
    dominant = [name for name, current in currents.items() if abs(current) > 0.5 * abs(i_terminal)]
    assert len(dominant) == 1, f"expected one dominant terminal flux, got {currents}"
    for name, current in currents.items():
        if "__EXT" in name:
            assert abs(current) < 1e-6, f"pad shell {name} unexpectedly carries current: {current}"
