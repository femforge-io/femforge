"""Run(sim_dir) opens a bundle end-to-end: typed result, entities, components, currents, and the lazy field mesh."""

import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from femforge.frames import Axis, Axisymmetric, Cartesian2D, FrameUnion, Solid
from femforge.common.tags import TagKey
from femforge.common.vector import Vec3
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.materials.properties import MaterialProperties
from femforge.physical.units import U, quantity
from femforge.run.bundle import write_bundle, write_manifest
from femforge.run.entities import Bodies, Body, Boundaries, Boundary, BoundaryKind
from femforge.run.fields import FieldMesh
from femforge.run.inspect import Run
from femforge.run.manifest import RunManifest
from femforge.run.result import Result, RunStatus

FIXED_DT = datetime(2026, 6, 14, 10, 0, 0, tzinfo=timezone.utc)

_MESH_NAMES = """\
! ----- names for bodies -----
$ AIR = 1
$ N42_MAGNETIZATIONN = 2
! ----- names for boundaries -----
$ AIR__EXT = 3
$ AIR__N42_MAGNETIZATIONN = 4
"""


def _config(frame: FrameUnion = Solid()) -> SimulationConfig:
    return SimulationConfig(
        frame=frame,
        mesh=MeshConfig(step="data/does-not-exist.step"),
        domain=DomainConfig(
            materials=[MaterialProperties(name="Air", tag="Air"), MaterialProperties(name="Neodymium", tag="N42")],
            tags=[EntityTag(selector={TagKey.MAGNETIZATION: "N"})],
        ),
    )


def _bundle(tmp_path: Path, result: Result | None = None, frame: FrameUnion = Solid()) -> Path:
    manifest = RunManifest.build(
        _config(frame),
        status=RunStatus.OK,
        run_id="run123",
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    bundle = write_bundle(
        tmp_path / "run",
        manifest,
        result if result is not None else Result(frame=Solid(), status=RunStatus.OK),
    )
    (bundle / "mesh.names").write_text(_MESH_NAMES, encoding="utf-8")
    return bundle


def test_run_requires_an_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no run bundle"):
        Run(tmp_path / "nope")
    assert "run" in repr(Run(_bundle(tmp_path)))


def test_from_result_uses_the_stamped_run_dir(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    result = Result.load(bundle)
    assert Run.from_result(result).path == bundle
    with pytest.raises(ValueError, match="run_dir is None"):
        Run.from_result(Result(frame=Solid(), status=RunStatus.OK))


def test_latest_picks_the_newest_manifest(tmp_path: Path) -> None:
    manifest = RunManifest.build(
        _config(),
        status=RunStatus.OK,
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    write_manifest(tmp_path / "old", manifest)
    write_manifest(tmp_path / "new", manifest)
    os.utime(tmp_path / "old" / "manifest.json", (1_000_000_000, 1_000_000_000))
    os.utime(tmp_path / "new" / "manifest.json", (2_000_000_000, 2_000_000_000))

    assert Run.latest(tmp_path).path == tmp_path / "new"
    with pytest.raises(FileNotFoundError, match="no run bundles"):
        Run.latest(tmp_path / "empty")


def test_latest_orders_by_created_at_before_mtime(tmp_path: Path) -> None:
    # A purge rewrites a manifest without re-running anything, so mtime alone would promote a purged old run.
    from datetime import timedelta

    older = RunManifest.build(
        _config(),
        status=RunStatus.OK,
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    newer = RunManifest.build(
        _config(),
        status=RunStatus.OK,
        code_sha="sha1",
        created_at=FIXED_DT + timedelta(days=1),
    )

    write_manifest(tmp_path / "older", older)
    write_manifest(tmp_path / "newer", newer)

    os.utime(tmp_path / "older" / "manifest.json", (2_000_000_000, 2_000_000_000))
    os.utime(tmp_path / "newer" / "manifest.json", (1_000_000_000, 1_000_000_000))
    (tmp_path / "broken").mkdir()
    _ = (tmp_path / "broken" / "manifest.json").write_text("not json", encoding="utf-8")
    os.utime(tmp_path / "broken" / "manifest.json", (1_500_000_000, 1_500_000_000))

    assert Run.latest(tmp_path).path == tmp_path / "newer"


def test_cleanups_and_purged_read_the_manifest(tmp_path: Path) -> None:
    from femforge.common.files import RunFile
    from femforge.run.retention import Cleanup, CleanupRecord, CleanupTrigger, RemovedFile, RetentionPolicy

    bundle = _bundle(tmp_path)
    run = Run(bundle)
    assert run.cleanups == []
    assert run.purged(RunFile.FIELDS) is None

    manifest = run.manifest
    manifest.cleanups.append(
        CleanupRecord(
            applied_at=FIXED_DT,
            trigger=CleanupTrigger.PURGE,
            status=RunStatus.OK,
            policy=RetentionPolicy.minimal(),
            removed=[
                RemovedFile(
                    path="case_t0001.vtu",
                    size=10,
                    file_class=RunFile.FIELDS,
                    reason=Cleanup.ON_SUCCESS,
                )
            ],
        )
    )
    _ = write_manifest(bundle, manifest)

    purged = Run(bundle)
    assert purged.purged(RunFile.FIELDS) is not None
    assert purged.purged(RunFile.SIF) is None
    with pytest.raises(FileNotFoundError, match="purged per retention on 2026-06-14 by purge"):
        _ = purged.mesh()

    (bundle / "manifest.json").write_text("not json", encoding="utf-8")
    assert Run(bundle).cleanups == []


def test_result_prefers_result_json(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, Result(frame=Solid(), status=RunStatus.OK, run_id="run123", wall_time=5.0))
    run = Run(bundle)
    assert run.result.wall_time == 5.0
    assert run.result.run_dir == bundle
    assert run.result is run.result


def test_result_falls_back_to_a_fresh_parse(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "result.json").unlink()
    (bundle / "elmersolver.log").write_text("*** Elmer Solver: ALL DONE ***\n", encoding="utf-8")

    result = Run(bundle).result
    assert result.status == RunStatus.OK
    assert result.run_dir == bundle
    # The manifest supplies the identity a bare parse cannot know.
    assert result.run_id == "run123"


def test_result_parse_fallback_without_a_manifest(tmp_path: Path) -> None:
    (tmp_path / "elmersolver.log").write_text("*** Elmer Solver: ALL DONE ***\n", encoding="utf-8")
    result = Run(tmp_path).result
    assert result.status == RunStatus.OK
    assert result.run_id is None


def test_manifest_and_entities_enrichment(tmp_path: Path) -> None:
    run = Run(_bundle(tmp_path))
    assert run.manifest.run_id == "run123"

    magnet = run.bodies["N42_MAGNETIZATIONN"]
    assert magnet.mesh_id == 2
    assert magnet.material == "N42"
    assert magnet.tags == {"Magnetization": "N"}
    assert run.boundaries.between("AIR", "N42_MAGNETIZATIONN").mesh_id == 4


def test_entities_survive_a_corrupt_manifest(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "manifest.json").write_text("not json", encoding="utf-8")
    run = Run(bundle)
    body = run.bodies["N42_MAGNETIZATIONN"]
    assert body.mesh_id == 2
    assert body.material is None


def test_components_join_names_with_typed_forces(tmp_path: Path) -> None:
    result = Result(
        frame=Solid(),
        status=RunStatus.OK,
        component_forces={"N42_MAGNETIZATIONN": quantity(Vec3(0.0, 0.0, 4.2), U.N)},
        component_torques={"N42_MAGNETIZATIONN": quantity(Vec3(0.0, 0.1, 0.0), U.N * U.m)},
    )
    bundle = _bundle(tmp_path, result)
    (bundle / "components.yml").write_text("1: N42_MAGNETIZATIONN\n2: PHASE0_coil\n", encoding="utf-8")

    run = Run(bundle)
    assert [component.name for component in run.components] == ["N42_MAGNETIZATIONN", "PHASE0_coil"]

    magnet = run.components[0]
    assert magnet.sif_index == 1
    assert magnet.force is not None
    assert magnet.force.magnitude.z == pytest.approx(4.2)
    assert magnet.torque is not None
    assert magnet.torque.magnitude.y == pytest.approx(0.1)

    coil = run.components[1]
    assert coil.force is None


def test_components_empty_without_the_sidecar(tmp_path: Path) -> None:
    run = Run(_bundle(tmp_path))
    assert run.components == []
    assert run.component_forces == {}
    assert run.component_torques == {}


def test_electrode_and_phase_currents(tmp_path: Path) -> None:
    raw = {
        "diffusive flux: potential over bc 1": 2.5,
        "diffusive flux: potential over bc 2": -0.1,
    }
    bundle = _bundle(tmp_path, Result(frame=Solid(), status=RunStatus.OK, raw=raw))
    run = Run(bundle)
    assert run.electrode_currents == {}

    (bundle / "boundaries.yml").write_text("1: COPPER_PHASE0__EXT_fixed_potential\n2: COPPER_PHASE0_PAD__EXT\n", encoding="utf-8")
    assert run.electrode_currents == {"COPPER_PHASE0__EXT_fixed_potential": 2.5, "COPPER_PHASE0_PAD__EXT": -0.1}
    # The dominant |flux| among a phase's electrodes is its terminal current.
    assert run.phase_currents == {"0": 2.5}


def test_vtu_path_and_cached_mesh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(tmp_path)
    run = Run(bundle)
    assert run.vtu_path is None

    (bundle / "case_t0001.vtu").touch()
    assert run.vtu_path == bundle / "case_t0001.vtu"

    import femforge.run.fields as fields

    calls: list[object] = []

    def fake_load(sim_dir: object, bodies: object = None, boundaries: object = None) -> object:
        calls.append(sim_dir)
        return object()

    monkeypatch.setattr(fields.FieldMesh, "load", staticmethod(fake_load))
    first = run.mesh()
    assert run.mesh() is first
    assert len(calls) == 1


def test_strain_energy_integrates_the_mesh_density_in_joules(tmp_path: Path) -> None:
    run = Run(_bundle(tmp_path))
    run._mesh = FieldMesh(
        arrays={"strain energy density": np.array([2.0, 6.0])},
        measure=np.array([0.5, 0.25]),
        geometry_ids=np.array([1, 2], dtype=np.int64),
        bodies=Bodies([Body(name="AIR", mesh_id=1), Body(name="N42_MAGNETIZATIONN", mesh_id=2)]),
    )

    assert run.strain_energy().to(U.J).magnitude == pytest.approx(2.5)
    assert run.strain_energy("AIR").to(U.J).magnitude == pytest.approx(1.0)


def _loaded_tip() -> FieldMesh:
    return FieldMesh(
        arrays={"displacement": np.array([[0.0, 0, 0], [0.001, 0, 0], [0.003, 0, 0]], dtype=np.float64)},
        measure=np.array([1.0, 0.0, 0.0]),
        geometry_ids=np.array([1, 101, 101], dtype=np.int64),
        bodies=Bodies([Body(name="AIR", mesh_id=1)]),
        area=np.array([0.0, 1.0, 1.0]),
        boundaries=Boundaries([Boundary(name="TIP", mesh_id=3, bodies=("AIR",), kind=BoundaryKind.EXTERNAL, sif_index=1)]),
    )


@pytest.mark.parametrize(
    ("frame", "force", "expected"),
    [
        (Solid(), Vec3(100, 0, 0), 0.1),
        (Cartesian2D(normal=Axis.X, thickness=10 * U.mm), Vec3(0, 100, 0), 10.0),
    ],
)
def test_clapeyron_energy_takes_the_solve_share_of_the_authored_face_force(tmp_path: Path, frame: FrameUnion, force: Vec3, expected: float) -> None:
    run = Run(_bundle(tmp_path, Result(frame=frame, status="ok"), frame=frame))
    run._mesh = _loaded_tip()

    assert run.clapeyron_energy("Tip", quantity(force, "N")).to(U.J).magnitude == pytest.approx(expected)


def test_clapeyron_energy_raises_for_a_force_the_frame_cannot_carry(tmp_path: Path) -> None:
    frame = Cartesian2D(normal=Axis.X, thickness=10 * U.mm)
    run = Run(_bundle(tmp_path, Result(frame=frame, status="ok"), frame=frame))
    run._mesh = _loaded_tip()

    with pytest.raises(ValueError, match="plane normal"):
        _ = run.clapeyron_energy("Tip", quantity(Vec3(100, 0, 0), "N"))
