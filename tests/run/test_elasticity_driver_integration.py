"""End-to-end (gmsh + Elmer gated): a cantilever with tagged end faces solves through the Driver and matches Timoshenko beam theory."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import numpy as np
import pytest

pytest.importorskip("gmsh")
pytest.importorskip("pyvista")
pytest.importorskip("build123d")

import build123d as b3d  # imported after the gate so the file still collects where the CAD stack is absent

from femforge.common.tags import TagKey
from femforge.common.vector import Vec3
from femforge.config import SimulationConfig
from femforge.elmer.config import PhysicsSelection
from femforge.elmer.physics import Physics
from femforge.elmer.variables import ElmerVariable
from femforge.geometry import Build123dGeometry
from femforge.geometry.tags import TaggedCompound, TaggedFace, TaggedPart
from femforge.materials.steel import Steel_316
from femforge.meshing.config import MeshConfig
from femforge.meshing.gmsh_options import GmshOptions
from femforge.physical.conditions import FixedDisplacement, SurfaceForce
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import U, quantity
from femforge.run.driver import Driver
from femforge.run.inspect import Run
from femforge.run.retention import RetentionPolicy

from tests.elmer.test_thermal_solve_integration import _gated_elmer, _native_backend

LENGTH = 100.0
"""`L` `[mm]`: Beam length along x."""

SIDE = 10.0
"""`b` `[mm]`: Width and depth of the square cross-section."""

LOAD = 100.0
"""`F` `[N]`: Tip load along +z."""

YOUNGS = 193e9
"""`E` `[Pa]`: Steel 316 Young's modulus."""

POISSON = 0.27
"""`ν` `[-]`: Steel 316 Poisson's ratio."""

SHEAR_COEFFICIENT = 0.848
"""`κ` `[-]`: Timoshenko shear coefficient of a square section at this Poisson's ratio."""


class _Cantilever(Build123dGeometry):
    """A steel bar along x with its two end faces tagged Root and Tip."""

    def model(self) -> TaggedCompound:
        """The bar and its two tagged end faces."""
        box = b3d.Box(LENGTH, SIDE, SIDE)
        x_faces = box.faces().filter_by(b3d.Axis.X).sort_by(b3d.Axis.X)
        beam = TaggedPart([box], tags={TagKey.MATERIAL: Steel_316().tag}, name="Beam")
        root = TaggedFace(x_faces[0], name="Root")
        tip = TaggedFace(x_faces[-1], name="Tip")
        beam.color = b3d.Color("gray")
        root.color = b3d.Color("red")
        tip.color = b3d.Color("blue")

        return TaggedCompound(children=[beam, root, tip])


def test_a_tagged_cantilever_solves_through_the_driver_to_timoshenko(tmp_path: Path) -> None:
    """A tip-loaded cantilever deflects as Timoshenko predicts and stores the work its load does."""
    elmer: dict[str, str] = _gated_elmer()
    force = Vec3(0, 0, LOAD)
    config = SimulationConfig(
        geometry=_Cantilever(),
        mesh=MeshConfig(
            global_mesh_size=2 * U.mm,
            gmsh_options=GmshOptions(element_order=2),
            deterministic=True,
        ),
        physics=PhysicsSelection(selected=Physics.LINEAR_ELASTICITY),
        domain=DomainConfig(
            materials=[Steel_316()],
            tags=[
                EntityTag(selector={TagKey.NAME: "Beam"}),
                EntityTag(selector={TagKey.NAME: "Root"}, conditions=[FixedDisplacement()]),
                EntityTag(selector={TagKey.NAME: "Tip"}, conditions=[SurfaceForce(force=quantity(force, "N"))]),
            ],
        ),
    )

    driver = Driver(
        runs_dir=tmp_path / "runs",
        code_sha="elasticity-e2e",
        backend=_native_backend(elmer),
        retention=RetentionPolicy.keep_all(),
        reuse=False,
    )

    result = driver.evaluate(config)
    run = Run.from_result(result)

    assert result.status == "ok", (run.path / "elmersolver.log").read_text(encoding="utf-8")[-3000:]

    tip = run.boundaries.face("TIP")
    tip_displacement: list[float] = cast("list[float]", np.asarray(run.mesh().get(ElmerVariable.DISPLACEMENT).where_boundary(tip).surface_mean()).tolist())

    length: float = LENGTH * 1e-3
    area: float = (SIDE * 1e-3) ** 2
    inertia: float = (SIDE * 1e-3) ** 4 / 12
    shear_modulus: float = YOUNGS / (2 * (1 + POISSON))
    deflection: float = LOAD * length**3 / (3 * YOUNGS * inertia) + LOAD * length / (SHEAR_COEFFICIENT * shear_modulus * area)

    assert tip_displacement[2] == pytest.approx(deflection, rel=0.05)
    assert run.strain_energy("BEAM").m_as("J") == pytest.approx(run.clapeyron_energy("Tip", quantity(force, "N")).m_as("J"), rel=0.03)
