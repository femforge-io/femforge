"""End-to-end (gmsh + Elmer gated): planar sections of prisms solved under Cartesian2D match beam and conduction theory and their 3D solve."""

from __future__ import annotations

import math
from pathlib import Path
from typing import cast

import numpy as np
import pytest

pytest.importorskip("gmsh")
pytest.importorskip("pyvista")
pytest.importorskip("build123d")

from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.config import SimulationConfig
from femforge.elmer.config import PhysicsSelection
from femforge.elmer.physics import Physics
from femforge.elmer.variables import ElmerVariable
from femforge.frames import Axis, Cartesian2D, PlaneState, Solid
from femforge.geometry import Build123dGeometry
from femforge.geometry.tags import TaggedCompound, TaggedFace, TaggedPart
from femforge.materials.steel import Steel_316
from femforge.meshing.config import MeshConfig
from femforge.meshing.gmsh_options import GmshOptions
from femforge.physical.conditions import ConditionUnion, FixedDisplacement, FixedTemperature, HeatSource, SurfaceForce
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import U, quantity
from femforge.run.driver import Driver
from femforge.run.fields import FieldMesh
from femforge.run.inspect import Run
from femforge.run.mechanical import strain_energy
from femforge.run.result import Result, RunStatus

from tests.elmer.test_thermal_solve_integration import _find_elmer

LENGTH = 100.0
"""`L` `[mm]`: Prism length along x."""

HEIGHT = 20.0
"""`h` `[mm]`: Prism height along y."""

DEPTH = 10.0
"""`t` `[mm]`: Prism depth along z, the Cartesian 2D thickness."""

YOUNGS = 193e9
"""`E` `[Pa]`: Steel 316 Young's modulus."""

POISSON = 0.27
"""`ν` `[-]`: Steel 316 Poisson's ratio."""

CONDUCTIVITY = 16.3
"""`k` `[W/(m·K)]`: Steel 316 thermal conductivity."""

BODY = "Prism"


class _Prism(Build123dGeometry):
    """A steel prism from the origin with its -X and +X end faces named Left and Right, optionally notched at mid-length."""

    depth: float = DEPTH
    notch: float = 0.0

    def model(self) -> TaggedCompound:
        import build123d as b3d

        box = b3d.Box(LENGTH, HEIGHT, self.depth, align=(b3d.Align.MIN, b3d.Align.MIN, b3d.Align.MIN))
        ends = box.faces().sort_by(b3d.Axis.X)
        shape: b3d.Part = box

        if self.notch > 0.0:
            for y in (0.0, HEIGHT):
                cutter = b3d.Pos(LENGTH / 2, y, self.depth / 2) * b3d.Cylinder(radius=self.notch, height=2 * self.depth)
                shape = cast("b3d.Part", shape - cutter)

        prism = TaggedPart([shape], tags={TagKey.MATERIAL: "Steel316"}, name=BODY)
        prism.color = b3d.Color("gray")
        left = TaggedFace(ends[0], name="Left")
        right = TaggedFace(ends[-1], name="Right")

        return TaggedCompound(children=[prism, left, right])


class _Column(Build123dGeometry):
    """The steel prism stood along y, its depth along x, with its -Y and +Y end faces named Left and Right."""

    def model(self) -> TaggedCompound:
        import build123d as b3d

        box = b3d.Box(DEPTH, LENGTH, HEIGHT, align=(b3d.Align.MIN, b3d.Align.MIN, b3d.Align.MIN))
        ends = box.faces().sort_by(b3d.Axis.Y)
        column = TaggedPart([box], tags={TagKey.MATERIAL: "Steel316"}, name=BODY)
        left = TaggedFace(ends[0], name="Left")
        right = TaggedFace(ends[-1], name="Right")

        return TaggedCompound(children=[column, left, right])


def _planar(depth: float = DEPTH, state: PlaneState = PlaneState.STRESS) -> Cartesian2D:
    """The mid-depth section of the prism, so no named face meets the plane only along an edge."""
    return Cartesian2D(normal=Axis(origin=Vec3(0, 0, depth / 2), direction=Direction(0, 0, 1)), thickness=depth * U.mm, state=state)


def _solve(
    tmp_path: Path,
    frame: Cartesian2D | Solid,
    physics: Physics,
    body: list[ConditionUnion],
    left: list[ConditionUnion],
    right: list[ConditionUnion],
    *,
    geometry: Build123dGeometry | None = None,
    size: float = 1.0,
) -> tuple[Result, FieldMesh]:
    """Solve the prism under ``frame`` with quadratic elements and load its fields."""
    elmer: dict[str, str] | None = _find_elmer()

    if elmer is None:
        pytest.skip("Elmer (ElmerGrid/ElmerSolver) not found on this machine")

    config = SimulationConfig(
        frame=frame,
        geometry=geometry if geometry is not None else _Prism(),
        mesh=MeshConfig(global_mesh_size=size * U.mm, gmsh_options=GmshOptions(element_order=2)),
        physics=PhysicsSelection(selected=physics),
        domain=DomainConfig(
            materials=[Steel_316()],
            tags=[
                EntityTag(selector={TagKey.NAME: BODY}, conditions=body),
                EntityTag(selector={TagKey.NAME: "Left"}, conditions=left),
                EntityTag(selector={TagKey.NAME: "Right"}, conditions=right),
            ],
        ),
    )
    result: Result = Driver(runs_dir=tmp_path / "runs", docker=False, code_sha="test").evaluate(config)

    assert result.status == RunStatus.OK, result
    assert result.run_dir is not None

    return result, FieldMesh.load(result.run_dir)


def _tip(mesh: FieldMesh) -> list[float]:
    """The mean displacement of the Right end."""
    values = mesh.get(ElmerVariable.DISPLACEMENT).where_boundary("RIGHT").values

    return cast("list[float]", np.asarray(values.mean(axis=0)).tolist())


def _bar(tmp_path: Path, frame: Cartesian2D | Solid, force: Vec3, *, size: float = 1.0, geometry: Build123dGeometry | None = None) -> list[float]:
    """The tip displacement of the prism clamped at Left and loaded by ``force`` on Right."""
    load = SurfaceForce(force=quantity(force, "N"))
    _, mesh = _solve(tmp_path, frame, Physics.LINEAR_ELASTICITY, [], [FixedDisplacement()], [load], geometry=geometry, size=size)

    return _tip(mesh)


@pytest.mark.parametrize("depth", [DEPTH, 2 * DEPTH])
def test_a_heated_slab_peaks_at_the_conduction_midpoint_whatever_its_depth(tmp_path: Path, depth: float) -> None:
    """Observed on Elmer 26.2: 376.687 K at both depths against 376.687 K."""
    power = 1e6
    ambient = 300.0
    source = HeatSource(power_density=power * U.W / U.m**3)
    held = FixedTemperature(value=ambient * U.K)

    result, _ = _solve(tmp_path, _planar(depth), Physics.THERMAL, [source], [held], [held], geometry=_Prism(depth=depth), size=2.0)
    expected: float = ambient + power * (LENGTH * 1e-3) ** 2 / (8 * CONDUCTIVITY)

    assert result.temperature_max is not None
    assert result.temperature_max.m_as("kelvin") == pytest.approx(expected, rel=0.01)


@pytest.mark.parametrize(
    ("frame", "geometry", "force"),
    [
        pytest.param(_planar(), _Prism(), Vec3(1000, 0, 0), id="normal_z"),
        pytest.param(
            Cartesian2D(normal=Axis(origin=Vec3(DEPTH / 2, 0, 0), direction=Direction(1, 0, 0)), thickness=DEPTH * U.mm),
            _Column(),
            Vec3(0, 1000, 0),
            id="normal_x",
        ),
    ],
)
def test_a_plane_stress_bar_stretches_by_fl_over_ea(tmp_path: Path, frame: Cartesian2D, geometry: Build123dGeometry, force: Vec3) -> None:
    """Observed on Elmer 26.2: 2.5855e-6 m for normal_z and 2.5855e-6 m for normal_x against 2.5907e-6 m."""
    expected: float = force.magnitude() * LENGTH * 1e-3 / (YOUNGS * HEIGHT * DEPTH * 1e-6)

    tip = _bar(tmp_path, frame, force, geometry=geometry)

    assert tip[0] == pytest.approx(expected, rel=0.03)


def test_plane_strain_stiffens_the_bar_by_one_minus_nu_squared(tmp_path: Path) -> None:
    """Observed on Elmer 26.2: a ratio of 0.9256 against 0.9271."""
    stress = _bar(tmp_path / "stress", _planar(), Vec3(1000, 0, 0))
    strain = _bar(tmp_path / "strain", _planar(state=PlaneState.STRAIN), Vec3(1000, 0, 0))

    assert strain[0] / stress[0] == pytest.approx(1 - POISSON**2, rel=0.02)


def test_doubling_the_thickness_halves_the_stretch_under_the_same_total_force(tmp_path: Path) -> None:
    """Observed on Elmer 26.2: a ratio of 0.49999999."""
    thin = _bar(tmp_path / "thin", _planar(), Vec3(1000, 0, 0))
    thick = _bar(tmp_path / "thick", _planar(2 * DEPTH), Vec3(1000, 0, 0), geometry=_Prism(depth=2 * DEPTH))

    assert thick[0] / thin[0] == pytest.approx(0.5, rel=1e-6)


def test_a_tip_loaded_cantilever_matches_timoshenko(tmp_path: Path) -> None:
    """Observed on Elmer 26.2: 2.6617e-5 m against 2.6696e-5 m."""
    force = 100.0
    length = LENGTH * 1e-3
    area = HEIGHT * DEPTH * 1e-6
    inertia = DEPTH * HEIGHT**3 * 1e-12 / 12
    shear_modulus = YOUNGS / (2 * (1 + POISSON))
    expected: float = force * length**3 / (3 * YOUNGS * inertia) + force * length / (5 / 6 * shear_modulus * area)

    tip = _bar(tmp_path, _planar(), Vec3(0, force, 0))

    assert tip[1] == pytest.approx(expected, rel=0.05)


def test_the_planar_strain_energy_is_per_metre_and_balances_the_clapeyron_work_of_the_face_force(tmp_path: Path) -> None:
    """Observed on Elmer 26.2: 0.13297 J/m stored against 0.13308 J/m of work, 1.3297e-3 J over the prism against 1.3348e-3 J."""
    force = Vec3(0, 100, 0)
    frame: Cartesian2D = _planar()
    load = SurfaceForce(force=quantity(force, "N"))
    result, mesh = _solve(tmp_path, frame, Physics.LINEAR_ELASTICITY, [], [FixedDisplacement()], [load])

    stored: float = strain_energy(mesh, BODY.upper())
    work: float = Run.from_result(result).clapeyron_energy("Right", quantity(force, "N")).m_as("J")
    length = LENGTH * 1e-3
    inertia = DEPTH * HEIGHT**3 * 1e-12 / 12
    shear_modulus = YOUNGS / (2 * (1 + POISSON))
    deflection: float = force.y * length**3 / (3 * YOUNGS * inertia) + force.y * length / (5 / 6 * shear_modulus * HEIGHT * DEPTH * 1e-6)

    assert stored == pytest.approx(work, rel=0.03)
    assert stored * frame.thickness_m == pytest.approx(0.5 * force.y * deflection, rel=0.05)


def test_the_planar_cantilever_matches_its_solid_solve(tmp_path: Path) -> None:
    """Observed on Elmer 26.2 at 2.5 mm: 2.6614e-5 m planar against 2.6500e-5 m solid."""
    planar = _bar(tmp_path / "planar", _planar(), Vec3(0, 100, 0), size=2.5)
    solid = _bar(tmp_path / "solid", Solid(), Vec3(0, 100, 0), size=2.5)

    assert planar[1] == pytest.approx(solid[1], rel=0.03)


def test_a_notch_flexure_stretches_and_peaks_its_stress_in_the_neck(tmp_path: Path) -> None:
    """Observed on Elmer 26.2: 3.90e-6 m stretch, a 27.3 MPa peak at the notch root against 25 MPa nominal in the neck."""
    load = SurfaceForce(force=quantity(Vec3(1000, 0, 0), "N"))
    _, mesh = _solve(tmp_path, _planar(), Physics.LINEAR_ELASTICITY, [], [FixedDisplacement()], [load], geometry=_Prism(notch=8.0))
    stress = mesh.get(ElmerVariable.VON_MISES).where_body(BODY.upper())
    centers = mesh.centers

    assert centers is not None
    assert _tip(mesh)[0] > 0.0

    values: list[float] = cast("list[float]", stress.values.tolist())
    peak: int = values.index(max(values))
    cell: int = cast("list[int]", stress.cell_indices.tolist())[peak]
    peak_center: list[float] = cast("list[list[float]]", centers.tolist())[cell]

    assert math.isfinite(values[peak])
    assert abs(peak_center[0] - LENGTH * 1e-3 / 2) < 0.01
