"""Driver assembles a run end-to-end with no gmsh/Elmer, via an injected solver that drops the committed fixture into the run dir.

This exercises the real identity to parse to manifest to bundle path; only the solve step is faked.
"""

import os
import shutil
from pathlib import Path
from typing import Callable, cast

import build123d as b3d
import pytest

from femforge.frames import Axisymmetric
from femforge.config import SimulationConfig
from femforge.elmer.config import MagnetoDynamicsCalcFieldsSolver
from femforge.elmer.physics import Physics
from femforge.elmer.runtime.backend import ExecutionBackend
from femforge.elmer.runtime.docker import DockerElmer
from femforge.elmer.runtime.docker_image import ELMER_IMAGE
from femforge.elmer.runtime.native import NativeElmer
from femforge.elmer.sim import Sim
from femforge.execution.docker.image import DockerImage
from femforge.execution.streaming import CancelToken, CommandResult, LineSink
from femforge.geometry.config import GeometryArea
from femforge.geometry.tags import TaggedCompound, TaggedPart
from femforge.meshing.backend import GmshMesher, WorkerMesher
from femforge.meshing.config import MeshConfig
from femforge.run.bundle import load_manifest, write_manifest
from femforge.run.driver import Driver, SolveState, default_backend
from femforge.run.manifest import RunManifest, cache_key
from femforge.common.names import bundle_name
from femforge.run.result import LifecycleStatus, MeshStats, RunStatus
from femforge.run.retention import Cleanup, CleanupTrigger, RetentionPolicy
from femforge.run.stages import Stage, StageEvent

FIXTURES: Path = Path(__file__).parent / "fixtures"
STALL_LOG: Path = Path(__file__).parents[1] / "elmer" / "fixtures" / "stall" / "lucid_cactus.log"


def _fake_solver(config: SimulationConfig, sim_dir: Path) -> MeshStats:
    """Stand in for mesh+grid+ElmerSolver: copy a finished run's outputs into sim_dir and report a mesh."""
    for name in ("case.dat", "case.dat.names", "elmersolver.log"):
        shutil.copyfile(FIXTURES / name, sim_dir / name)
    return MeshStats(element_count=1000, min_element_size=0.05, max_element_size=1.0)


def _run_name(config: SimulationConfig) -> str:
    """The bundle directory the driver targets for a config at code_sha 'sha1'."""
    return bundle_name(cache_key(config))


def test_driver_evaluate_parses_and_writes_bundle(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    driver = Driver(runs_dir=tmp_path, code_sha="sha1", solver=_fake_solver)

    result = driver.evaluate(config)

    # The parsed Result comes from the fixture.
    assert result.status == RunStatus.OK
    assert result.force is not None
    assert result.force.magnitude.x == pytest.approx(12.5)
    assert result.wall_time == pytest.approx(5.12)

    # The bundle lands under runs_dir/<run_id>, where run_id is the memorable name derived from the cache_key.
    run_id = _run_name(config)
    bundle = tmp_path / run_id
    assert (bundle / "manifest.json").is_file()
    assert (bundle / "result.json").is_file()

    manifest = load_manifest(bundle)

    assert manifest.run_id == run_id
    assert manifest.status == RunStatus.OK
    assert manifest.git_sha == "sha1"
    assert manifest.mesh_stats is not None
    assert manifest.mesh_stats.element_count == 1000
    # The parsed solver version lands on the final manifest; an injected solver drives no solve stages, only the cleanup.
    assert manifest.elmer_version == "9.0"
    assert [event.stage for event in manifest.stage_history] == [Stage.CLEANUP]
    assert manifest.cleanups == []


def test_evaluate_runs_under_the_run_log_context(tmp_path: Path) -> None:
    # Records emitted during the solve carry the run_id and land in the bundle's run.log.
    from femforge.common.log import log

    def logging_solver(config: SimulationConfig, sim_dir: Path) -> MeshStats:
        log.info("solver says hello")
        return _fake_solver(config, sim_dir)

    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    _ = Driver(runs_dir=tmp_path, code_sha="sha1", solver=logging_solver).evaluate(config)

    run_log = tmp_path / _run_name(config) / "run.log"
    assert "solver says hello" in run_log.read_text(encoding="utf-8")


def test_driver_evaluates_a_config_with_nonlinear_steel(tmp_path: Path) -> None:
    # The Calibration-carrying steel must survive the identity + manifest path end-to-end.
    from femforge.materials.steel import Steel_1080
    from femforge.physical.domain import DomainConfig

    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"), domain=DomainConfig(materials=[Steel_1080()]))
    driver = Driver(runs_dir=tmp_path, code_sha="sha1", solver=_fake_solver)

    result = driver.evaluate(config)

    assert result.status == RunStatus.OK
    bundle = tmp_path / _run_name(config)
    assert (bundle / "manifest.json").is_file()
    assert load_manifest(bundle).run_id == _run_name(config)


def test_driver_rejects_nonpositive_mpi_processes() -> None:
    with pytest.raises(ValueError, match="mpi_processes"):
        _ = Driver(mpi_processes=0)


def test_default_backend_picks_docker_when_asked_given_an_image_partitioned_or_without_native_elmer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("femforge.run.driver.native_elmer_installed", lambda: True)

    assert isinstance(default_backend(), NativeElmer)
    assert isinstance(default_backend(docker=False, mpi_processes=4), NativeElmer)
    assert isinstance(default_backend(docker=True), DockerElmer)
    assert isinstance(default_backend(mpi_processes=4), DockerElmer)

    given = default_backend(docker_image="mine:1")
    assert isinstance(given, DockerElmer) and given.image == DockerImage("mine:1")

    monkeypatch.setattr("femforge.run.driver.native_elmer_installed", lambda: False)

    assert isinstance(default_backend(), DockerElmer)
    assert isinstance(default_backend(docker=False), NativeElmer)


def test_default_backend_refuses_an_image_with_docker_off() -> None:
    with pytest.raises(ValueError, match="docker_image"):
        _ = default_backend(docker=False, docker_image="mine:1")


def test_driver_takes_or_builds_its_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("femforge.run.driver.native_elmer_installed", lambda: True)
    native = NativeElmer()

    assert Driver(backend=native).backend is native
    assert isinstance(Driver().backend, NativeElmer)
    assert isinstance(Driver(mpi_processes=4).backend, DockerElmer)
    assert isinstance(Driver(docker_image="mine:1").backend, DockerElmer)

    with pytest.raises(ValueError, match="not both"):
        _ = Driver(backend=native, docker=True)


def test_driver_meshes_in_process_unless_given_a_mesher() -> None:
    worker = WorkerMesher()

    assert isinstance(Driver().mesher, GmshMesher)
    assert Driver(mesher=worker).mesher is worker


def test_a_partitioned_driver_needs_a_partitioned_backend() -> None:
    with pytest.raises(ValueError, match="partitioned backend"):
        _ = Driver(mpi_processes=4, docker=False)

    with pytest.raises(ValueError, match="NativeElmer is not one"):
        _ = Driver(backend=NativeElmer(), mpi_processes=4)


def test_driver_mpi_processes_is_not_part_of_the_run_identity(tmp_path: Path) -> None:
    # An execution detail: the same config must hit the same bundle whether solved serially or partitioned.
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    serial = Driver(runs_dir=tmp_path, code_sha="sha1", solver=_fake_solver)
    parallel = Driver(
        runs_dir=tmp_path,
        code_sha="sha1",
        solver=_fake_solver,
        mpi_processes=4,
    )
    assert parallel.mpi_processes == 4

    serial.evaluate(config)
    parallel.evaluate(config)

    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 1


def test_driver_is_content_addressed(tmp_path: Path) -> None:
    # Identical inputs target the same bundle directory (dedup).
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    driver = Driver(runs_dir=tmp_path, code_sha="sha1", solver=_fake_solver)
    driver.evaluate(config)
    driver.evaluate(config)
    run_dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1


def test_materialize_geometry_passes_through_without_a_geometry_area(tmp_path: Path) -> None:
    from femforge.run.driver import materialize_geometry

    config = SimulationConfig()
    assert materialize_geometry(config, tmp_path) is config


class _StubGeometry(GeometryArea):
    """A parametric geometry whose build_step writes a placeholder STEP."""

    width: float = 1.0

    def build_step(self, path: Path) -> Path:
        _ = path.write_text(f"ISO-10303-21; width={self.width}", encoding="utf-8")
        return path


def test_materialize_geometry_builds_the_step_into_the_run_dir(tmp_path: Path) -> None:
    from femforge.run.driver import materialize_geometry

    config = SimulationConfig(geometry=_StubGeometry(width=2.0))

    materialized = materialize_geometry(config, tmp_path)
    assert (tmp_path / "geometry.step").read_text(encoding="utf-8").endswith("width=2.0")
    assert materialized.mesh.step.endswith("geometry.step")
    # The original config (the run identity) is untouched.
    assert config.mesh.step == SimulationConfig().mesh.step


def test_materialize_geometry_sections_an_authored_step_into_the_run_dir(tmp_path: Path) -> None:
    from femforge.run.driver import materialize_geometry

    rod = TaggedPart(b3d.Cylinder(radius=2.0, height=10.0), tags={"Material": "Steel316"})
    authored = tmp_path / "authored.step"
    TaggedCompound(children=[rod]).export_step(str(authored))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    config = SimulationConfig(mesh=MeshConfig(step=str(authored)), frame=Axisymmetric())

    materialized = materialize_geometry(config, run_dir)
    assert Path(materialized.mesh.step) == run_dir / "geometry.step"
    assert config.mesh.step == str(authored)

    read_step = cast("Callable[[Path], b3d.Compound]", b3d.import_step)
    bodies = cast("tuple[b3d.Shell, ...]", read_step(run_dir / "geometry.step").children)
    assert [body.label for body in bodies] == ["Material=Steel316"]
    box = bodies[0].bounding_box()
    assert (bodies[0].area, box.min.X, box.max.X, box.min.Y, box.max.Y, box.max.Z) == pytest.approx((20.0, 0.0, 2.0, -5.0, 5.0, 0.0), abs=1e-6)


def test_materialize_geometry_never_writes_through_a_store_hardlink(tmp_path: Path) -> None:
    from femforge.run.driver import materialize_geometry

    stored = tmp_path / "store.step"
    _ = stored.write_text("ISO-10303-21; stored", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    os.link(stored, run_dir / "geometry.step")

    _ = materialize_geometry(SimulationConfig(geometry=_StubGeometry(width=3.0)), run_dir)
    assert stored.read_text(encoding="utf-8") == "ISO-10303-21; stored"
    assert (run_dir / "geometry.step").read_text(encoding="utf-8").endswith("width=3.0")


def test_geometry_area_base_has_no_cad() -> None:
    with pytest.raises(NotImplementedError):
        _ = GeometryArea().build_step(Path("geometry.step"))


class _StageRecordingDriver(Driver):
    """The staged pipeline with the tool-touching stages faked; records the order stages ran in."""

    def __init__(self, runs_dir: Path) -> None:
        super().__init__(runs_dir=runs_dir, code_sha="sha1")
        self.calls: list[str] = []

    def prepare_geometry(self, state: SolveState) -> None:
        self.calls.append("prepare_geometry")
        super().prepare_geometry(state)

    def pre_mesh(self, state: SolveState) -> None:
        self.calls.append("pre_mesh")

    def mesh(self, state: SolveState) -> None:
        self.calls.append("mesh")
        state.element_count = 42

    def post_mesh(self, state: SolveState) -> None:
        self.calls.append("post_mesh")

    def build_sim(self, state: SolveState) -> None:
        self.calls.append("build_sim")
        super().build_sim(state)

    def configure_sim(self, state: SolveState) -> None:
        self.calls.append("configure_sim")
        assert state.sim is not None
        state.sim.config.simulation.max_output_level = 3

    def write_sif(self, state: SolveState) -> None:
        self.calls.append("write_sif")
        super().write_sif(state)

    def run_solver(self, state: SolveState) -> None:
        self.calls.append("run_solver")
        for name in ("case.dat", "case.dat.names", "elmersolver.log"):
            shutil.copyfile(FIXTURES / name, state.sim_dir / name)


class _MinimalStagedDriver(Driver):
    """Overrides only the tool-touching stages; every default hook and stage runs for real."""

    def mesh(self, state: SolveState) -> None:
        state.element_count = 7

    def run_solver(self, state: SolveState) -> None:
        for name in ("case.dat", "case.dat.names", "elmersolver.log"):
            shutil.copyfile(FIXTURES / name, state.sim_dir / name)


def test_staged_default_solve_runs_stages_in_order(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    driver = _StageRecordingDriver(tmp_path)

    result = driver.evaluate(config)

    assert driver.calls == ["prepare_geometry", "pre_mesh", "mesh", "post_mesh", "build_sim", "configure_sim", "write_sif", "run_solver"]
    assert result.status == RunStatus.OK

    bundle = tmp_path / _run_name(config)
    # write_sif generated a real sif carrying the configure_sim edit; mesh_stats reported the faked mesh.
    assert "Max Output Level = 3" in (bundle / "case.sif").read_text()

    manifest = load_manifest(bundle)
    assert manifest.mesh_stats is not None
    assert manifest.mesh_stats.element_count == 42


def test_staged_default_solve_with_default_hooks(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    driver = _MinimalStagedDriver(runs_dir=tmp_path, code_sha="sha1")
    result = driver.evaluate(config)
    assert result.status == RunStatus.OK
    assert (tmp_path / _run_name(config) / "case.sif").is_file()


def test_authored_overrides_reach_the_generated_sif(tmp_path: Path) -> None:
    # The front-end seam: author the solver stack on the config; build_sim resolves it into the Sim.
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    config.customize_physics().simulation.steady_state_max_iterations = 7
    driver = _MinimalStagedDriver(runs_dir=tmp_path, code_sha="sha1")

    _ = driver.evaluate(config)

    sif = (tmp_path / _run_name(config) / "case.sif").read_text()
    assert "Steady State Max Iterations = 7" in sif


def test_write_sif_requires_build_sim_first(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="build_sim"):
        Driver().write_sif(SolveState(config=SimulationConfig(), sim_dir=tmp_path))


def test_build_sim_applies_the_mpi_adjustment(tmp_path: Path) -> None:
    # A partitioned driver's build_sim flips CoilSolver's Direct method to MUMPS (umfpack is serial-only).
    from femforge.elmer.config import CoilCurrentSolver, PhysicsSelection
    from femforge.elmer.physics import Physics

    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"), physics=PhysicsSelection(selected=Physics.COIL_MAGNETOSTATICS))
    driver = Driver(runs_dir=tmp_path, mpi_processes=4)
    state = SolveState(config=config, sim_dir=tmp_path)
    driver.build_sim(state)
    assert state.sim is not None
    assert state.sim.config.solver(CoilCurrentSolver).linear_system_direct_method == "MUMPS"


def test_evaluate_stamps_run_identity_on_the_result(tmp_path: Path) -> None:
    import json

    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    driver = Driver(runs_dir=tmp_path, code_sha="sha1", solver=_fake_solver)

    result = driver.evaluate(config)

    run_id = _run_name(config)
    assert result.run_id == run_id
    assert result.run_dir == tmp_path / run_id
    # run_id is serialized provenance; run_dir stays in-process only.
    written = cast("dict[str, object]", json.loads((tmp_path / run_id / "result.json").read_text(encoding="utf-8")))
    assert written["run_id"] == run_id
    assert "run_dir" not in written


def test_manifest_is_written_running_before_the_solver_runs(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    observed: list[str] = []

    def observing_solver(cfg: SimulationConfig, sim_dir: Path) -> MeshStats:
        observed.append(load_manifest(sim_dir).status)
        return _fake_solver(cfg, sim_dir)

    driver = Driver(runs_dir=tmp_path, code_sha="sha1", solver=observing_solver)
    result = driver.evaluate(config)

    assert observed == ["running"]
    assert result.status == RunStatus.OK
    assert load_manifest(tmp_path / _run_name(config)).status == "ok"


def test_evaluate_clears_stale_outputs_from_a_reused_run_dir(tmp_path: Path) -> None:
    # A previous run's result.json and SaveScalars exports must not leak into this run's parse.
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    run_dir = tmp_path / _run_name(config)
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text("{}", encoding="utf-8")
    (run_dir / "stale.dat").write_text("99.0\n", encoding="utf-8")
    (run_dir / "stale.dat.names").write_text("Variables in columns of matrix:\n1: stale column\n", encoding="utf-8")

    def asserting_solver(cfg: SimulationConfig, sim_dir: Path) -> MeshStats:
        assert not (sim_dir / "result.json").exists()
        assert not list(sim_dir.glob("*.dat"))
        assert not list(sim_dir.glob("*.dat.names"))
        return _fake_solver(cfg, sim_dir)

    result = Driver(runs_dir=tmp_path, code_sha="sha1", solver=asserting_solver).evaluate(config)
    assert "stale column" not in result.raw
    assert result.status == RunStatus.OK


def test_a_raising_solver_persists_error_status_and_propagates(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))

    def exploding_solver(cfg: SimulationConfig, sim_dir: Path) -> MeshStats:
        raise RuntimeError("mesh exploded")

    driver = Driver(runs_dir=tmp_path, code_sha="sha1", solver=exploding_solver)
    with pytest.raises(RuntimeError, match="mesh exploded"):
        _ = driver.evaluate(config)

    bundle = tmp_path / _run_name(config)
    assert load_manifest(bundle).status == "error"
    # No result.json: the crashed dir self-describes as manifest-only.
    assert not (bundle / "result.json").exists()
    assert driver._active is None


def test_staged_solve_records_nine_finished_stage_events(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    result = _MinimalStagedDriver(runs_dir=tmp_path, code_sha="sha1").evaluate(config)

    manifest = load_manifest(tmp_path / _run_name(config))
    assert result.status == RunStatus.OK
    assert manifest.status == RunStatus.OK
    assert [event.stage for event in manifest.stage_history] == [stage for stage in Stage if stage is not Stage.PUBLISH]
    assert all(event.finished_at is not None and event.finished_at >= event.started_at for event in manifest.stage_history)
    assert manifest.current_stage is None


def test_stage_events_are_persisted_as_each_stage_starts(tmp_path: Path) -> None:
    # A stage reading the on-disk manifest mid-run sees itself as current_stage: the start event was already written.
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    seen: list[Stage | None] = []

    class _ProbingDriver(_MinimalStagedDriver):
        def post_mesh(self, state: SolveState) -> None:
            seen.append(load_manifest(state.sim_dir).current_stage)

    _ = _ProbingDriver(runs_dir=tmp_path, code_sha="sha1").evaluate(config)
    assert seen == [Stage.POST_MESH]


def test_a_raising_stage_leaves_the_failed_stage_unfinished(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))

    class _ExplodingMeshDriver(_MinimalStagedDriver):
        def mesh(self, state: SolveState) -> None:
            raise RuntimeError("gmsh crashed")

    with pytest.raises(RuntimeError, match="gmsh crashed"):
        _ = _ExplodingMeshDriver(runs_dir=tmp_path, code_sha="sha1").evaluate(config)

    manifest = load_manifest(tmp_path / _run_name(config))
    assert manifest.status == RunStatus.ERROR
    # Cleanup still runs (and finishes) after the crash; the crashed stage stays the current one.
    stages = [event.stage for event in manifest.stage_history]
    assert stages == [Stage.PREPARE_GEOMETRY, Stage.PRE_MESH, Stage.MESH, Stage.CLEANUP]
    assert manifest.stage_history[-1].finished_at is not None
    assert manifest.current_stage is Stage.MESH


def test_evaluate_refuses_a_bundle_name_collision(tmp_path: Path) -> None:
    # Astronomically unlikely, but never silent: a dir already claimed by a DIFFERENT cache_key must not be reused.
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    run_dir = tmp_path / _run_name(config)
    run_dir.mkdir(parents=True)
    foreign = RunManifest.build(
        SimulationConfig(mesh=MeshConfig(step="data/other.step")),
        status=RunStatus.OK,
        code_sha="other",
    )
    _ = write_manifest(run_dir, foreign)

    with pytest.raises(RuntimeError, match="collision"):
        _ = Driver(runs_dir=tmp_path, code_sha="sha1", solver=_fake_solver).evaluate(config)


def test_on_stage_event_without_an_active_run_is_a_no_op(tmp_path: Path) -> None:
    # The default sink only records into an in-flight evaluate; outside one it must not write anywhere.
    from datetime import datetime, timezone

    driver = Driver(runs_dir=tmp_path, code_sha="sha1")
    driver.on_stage_event(StageEvent(stage=Stage.MESH, started_at=datetime.now(timezone.utc)))
    assert not list(tmp_path.iterdir())


# --- retention ---------------------------------------------------------------


class _StagedMeshOnlyDriver(Driver):
    """Fakes only the mesh; the sif and the solve on the driver's backend run for real."""

    def mesh(self, state: SolveState) -> None:
        state.element_count = 7


class _MeshDroppingDriver(_MinimalStagedDriver):
    """The minimal staged driver whose solve also leaves mesh files and a VTU behind, like ElmerGrid and ElmerSolver do."""

    def run_solver(self, state: SolveState) -> None:
        super().run_solver(state)
        _ = (state.sim_dir / "mesh.nodes").write_bytes(b"n" * 100)
        _ = (state.sim_dir / "mesh.elements").write_bytes(b"e" * 200)
        _ = (state.sim_dir / "case_t0001.vtu").write_bytes(b"v" * 300)


def test_default_retention_keeps_everything(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    driver = _MeshDroppingDriver(runs_dir=tmp_path, code_sha="sha1")
    assert driver.retention == RetentionPolicy.default()

    _ = driver.evaluate(config)

    bundle = tmp_path / _run_name(config)
    assert (bundle / "mesh.nodes").is_file()
    assert (bundle / "case_t0001.vtu").is_file()

    manifest = load_manifest(bundle)
    assert manifest.cleanups == []
    assert manifest.stage_history[-1].stage is Stage.CLEANUP
    assert manifest.stage_history[-1].finished_at is not None


def test_lean_retention_drops_the_mesh_after_a_successful_run(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    driver = _MeshDroppingDriver(
        runs_dir=tmp_path,
        code_sha="sha1",
        retention=RetentionPolicy.lean(),
    )

    result = driver.evaluate(config)

    bundle = tmp_path / _run_name(config)
    assert result.status == RunStatus.OK
    assert not (bundle / "mesh.nodes").exists()
    assert not (bundle / "mesh.elements").exists()
    assert (bundle / "case_t0001.vtu").is_file()
    assert (bundle / "case.sif").is_file()

    manifest = load_manifest(bundle)
    assert [event.stage for event in manifest.stage_history] == [stage for stage in Stage if stage is not Stage.PUBLISH]
    assert len(manifest.cleanups) == 1

    record = manifest.cleanups[0]
    assert record.trigger is CleanupTrigger.DRIVER
    assert record.status == RunStatus.OK
    assert record.policy == RetentionPolicy.lean()
    removed = sorted((entry.path, entry.size, entry.reason) for entry in record.removed)
    assert removed == [("mesh.elements", 200, Cleanup.ON_SUCCESS), ("mesh.nodes", 100, Cleanup.ON_SUCCESS)]


def test_per_call_retention_overrides_the_driver_policy(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    driver = _MeshDroppingDriver(
        runs_dir=tmp_path,
        code_sha="sha1",
        retention=RetentionPolicy.lean(),
    )

    _ = driver.evaluate(config, retention=RetentionPolicy.keep_all())

    assert (tmp_path / _run_name(config) / "mesh.nodes").is_file()


def test_on_success_rules_keep_files_of_a_failed_solve_but_always_removes_them(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))

    class _FailingSolveDriver(_MeshDroppingDriver):
        def run_solver(self, state: SolveState) -> None:
            super().run_solver(state)
            # Without "ALL DONE" the parse grades the run as an error.
            _ = (state.sim_dir / "elmersolver.log").write_text("ERROR:: MagnetoDynamics: no convergence\n", encoding="utf-8")

    lean = _FailingSolveDriver(runs_dir=tmp_path, code_sha="sha1", retention=RetentionPolicy.lean())
    result = lean.evaluate(config)
    bundle = tmp_path / _run_name(config)
    assert result.status == RunStatus.ERROR
    assert (bundle / "mesh.nodes").is_file()
    assert load_manifest(bundle).cleanups == []

    always = _FailingSolveDriver(
        runs_dir=tmp_path,
        code_sha="sha1",
        retention=RetentionPolicy.lean().model_copy(update={"elmer_mesh": Cleanup.ALWAYS}),
    )
    _ = always.evaluate(config)
    assert not (bundle / "mesh.nodes").exists()
    assert load_manifest(bundle).cleanups[0].status == "error"


def test_always_rules_apply_when_the_solver_raises(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))

    def exploding_after_writing(cfg: SimulationConfig, sim_dir: Path) -> MeshStats:
        _ = _fake_solver(cfg, sim_dir)
        raise RuntimeError("solver crashed after writing its log")

    policy = RetentionPolicy(logs=Cleanup.ALWAYS)
    driver = Driver(
        runs_dir=tmp_path,
        code_sha="sha1",
        solver=exploding_after_writing,
        retention=policy,
    )
    with pytest.raises(RuntimeError, match="crashed"):
        _ = driver.evaluate(config)

    bundle = tmp_path / _run_name(config)
    manifest = load_manifest(bundle)

    assert manifest.status == RunStatus.ERROR
    assert not (bundle / "elmersolver.log").exists()
    assert not (bundle / "run.log").exists()
    assert (bundle / "case.dat").is_file()
    assert [event.stage for event in manifest.stage_history] == [Stage.CLEANUP]
    assert manifest.cleanups[0].status == "error"
    assert driver._active is None


def test_run_log_can_be_removed_because_the_sink_is_closed_first(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    bundle = tmp_path / _run_name(config)

    _ = Driver(
        runs_dir=tmp_path,
        code_sha="sha1",
        solver=_fake_solver,
        retention=RetentionPolicy(logs=Cleanup.NEVER),
    ).evaluate(config)
    assert "Beginning evaluation." in (bundle / "run.log").read_text(encoding="utf-8")

    _ = Driver(
        runs_dir=tmp_path,
        code_sha="sha1",
        solver=_fake_solver,
        retention=RetentionPolicy(logs=Cleanup.ALWAYS),
        reuse=False,
    ).evaluate(config)
    assert not (bundle / "run.log").exists()
    assert not (bundle / "elmersolver.log").exists()
    assert (bundle / "result.json").is_file()


def test_nuke_removes_the_run_directory_and_unstamps_run_dir(tmp_path: Path) -> None:
    from femforge.run.inspect import Run

    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    result = Driver(
        runs_dir=tmp_path,
        code_sha="sha1",
        solver=_fake_solver,
        retention=RetentionPolicy.nuke(),
    ).evaluate(config)

    assert result.status == RunStatus.OK
    assert result.run_id == _run_name(config)
    assert result.run_dir is None
    assert not (tmp_path / _run_name(config)).exists()
    with pytest.raises(ValueError, match="retention"):
        _ = Run.from_result(result)


def test_an_interrupt_skips_cleanup_and_leaves_the_bundle_running(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))

    def interrupted(cfg: SimulationConfig, sim_dir: Path) -> MeshStats:
        _ = _fake_solver(cfg, sim_dir)
        raise KeyboardInterrupt

    driver = Driver(
        runs_dir=tmp_path,
        code_sha="sha1",
        solver=interrupted,
        retention=RetentionPolicy.nuke(),
    )
    with pytest.raises(KeyboardInterrupt):
        _ = driver.evaluate(config)

    bundle = tmp_path / _run_name(config)
    manifest = load_manifest(bundle)
    assert manifest.status == LifecycleStatus.RUNNING
    assert manifest.stage_history == []
    assert (bundle / "elmersolver.log").is_file()
    assert driver._active is None


def test_cleanup_failures_are_logged_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from femforge.run import driver as driver_module
    from femforge.run import retention as retention_module

    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))

    def held_open(_path: Path) -> None:
        raise PermissionError("held open")

    monkeypatch.setattr(retention_module, "remove_path", held_open)
    result = _MeshDroppingDriver(runs_dir=tmp_path, code_sha="sha1", retention=RetentionPolicy.lean()).evaluate(config)
    bundle = tmp_path / _run_name(config)
    assert result.status == RunStatus.OK
    assert (bundle / "mesh.nodes").is_file()
    assert load_manifest(bundle).cleanups == []

    def cannot_remove(_sim_dir: Path) -> int:
        raise OSError("directory in use")

    monkeypatch.setattr(driver_module, "remove_run_dir", cannot_remove)
    result = Driver(
        runs_dir=tmp_path,
        code_sha="sha1",
        solver=_fake_solver,
        retention=RetentionPolicy.nuke(),
        reuse=False,
    ).evaluate(config)
    assert result.run_dir is None
    assert (bundle / "manifest.json").is_file()


# --- telemetry ----------------------------------------------------------------


def test_evaluate_records_the_run_in_the_telemetry_index(tmp_path: Path) -> None:
    from femforge.telemetry.paths import Telemetry
    from femforge.telemetry.runs import RunIndex
    from femforge.telemetry.schemas import RunColumn, RunSource

    # The driver appends a row at the first manifest write and again once the run has settled; the reader keeps the last.
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    runs = tmp_path / "runs"
    telemetry = Telemetry(tmp_path / "telemetry")

    driver = Driver(
        runs_dir=runs,
        code_sha="sha1",
        solver=_fake_solver,
        telemetry=telemetry.root,
    )
    _ = driver.evaluate(config)

    index = RunIndex(telemetry, runs)
    assert telemetry.runs.scan().collect().height == 2

    frame = index.query().frame()
    assert frame.height == 1
    assert frame.get_column(RunColumn.RUN_ID).to_list() == [_run_name(config)]
    assert frame.get_column(RunColumn.STATUS).to_list() == ["ok"]
    assert frame.get_column(RunColumn.SOURCE).to_list() == [RunSource.DRIVER]
    assert frame.get_column(RunColumn.HAS_RESULT).to_list() == [True]

    # A scan afterwards has nothing left to read: the driver's rows already carry the manifest mtime.
    assert index.update() == 0


def test_a_run_solved_under_a_sweep_point_is_attributable_to_it(tmp_path: Path) -> None:
    from femforge.telemetry.context import sweep_point
    from femforge.telemetry.paths import Telemetry
    from femforge.telemetry.runs import RunIndex
    from femforge.telemetry.schemas import RunColumn

    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    telemetry = Telemetry(tmp_path / "telemetry")
    driver = Driver(
        runs_dir=tmp_path / "runs",
        code_sha="sha1",
        solver=_fake_solver,
        telemetry=telemetry.root,
    )

    with sweep_point("sweep-a", "point-3"):
        _ = driver.evaluate(config)

    frame = RunIndex(telemetry, driver.runs_dir).query().frame()

    assert frame.get_column(RunColumn.SWEEP_ID).to_list() == ["sweep-a"]
    assert frame.get_column(RunColumn.POINT_KEY).to_list() == ["point-3"]


def test_the_telemetry_root_falls_back_to_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from femforge.common.log import TELEMETRY_ENV

    # Telemetry off is the default, and it is what keeps polars out of a solve process.
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)
    assert Driver(runs_dir=tmp_path / "runs", code_sha="sha1", solver=_fake_solver).run_index() is None

    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    index = Driver(runs_dir=tmp_path / "runs", code_sha="sha1", solver=_fake_solver).run_index()
    assert index is not None
    assert index.telemetry.root == tmp_path / "telemetry"


def test_a_telemetry_write_that_fails_only_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from femforge.run.manifest import RunManifest as _Manifest
    from femforge.telemetry.runs import RunIndex

    # Telemetry must never fail a solve: an unwritable store costs a warning, not the run.
    def unwritable(_self: RunIndex, _manifest: _Manifest, _sim_dir: Path, _source: object = None) -> None:
        raise OSError("store is read-only")

    monkeypatch.setattr(RunIndex, "record", unwritable)

    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    driver = Driver(
        runs_dir=tmp_path / "runs",
        code_sha="sha1",
        solver=_fake_solver,
        telemetry=tmp_path / "telemetry",
    )
    result = driver.evaluate(config)

    assert result.status == RunStatus.OK


def test_a_driver_with_its_own_telemetry_root_keeps_its_run_logs_there(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from femforge.common.log import TELEMETRY_ENV, _reset_for_tests, log, setup_logging, telemetry_sink
    from femforge.telemetry.paths import Telemetry
    from femforge.telemetry.schemas import LogColumn
    from femforge.telemetry.sink import scan_logs

    env_root = tmp_path / "env-telemetry"
    own_root = tmp_path / "own-telemetry"
    monkeypatch.setenv(TELEMETRY_ENV, str(env_root))
    _reset_for_tests()

    try:
        setup_logging()
        driver = Driver(runs_dir=tmp_path / "runs", code_sha="sha1", solver=_fake_solver, telemetry=own_root)
        result = driver.evaluate(SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step")))
        log.info("Outside any run.")

        default = telemetry_sink()
        assert default is not None
        default.flush()

        own = scan_logs(Telemetry(own_root)).collect()
        env = scan_logs(Telemetry(env_root)).collect()
    finally:
        _reset_for_tests()

    assert result.run_id in own.get_column(LogColumn.RUN_ID).to_list()
    assert result.run_id not in env.get_column(LogColumn.RUN_ID).to_list()
    assert env.get_column(LogColumn.MESSAGE).to_list() == ["Outside any run."]


def test_driver_runs_femforge_image_unless_given_its_own() -> None:
    own = Driver(docker=True).backend
    given = Driver(docker=True, docker_image="mine:1").backend

    assert isinstance(own, DockerElmer) and own.image == ELMER_IMAGE.image()
    assert isinstance(given, DockerElmer) and given.image == DockerImage("mine:1")


class _RecordingBackend(ExecutionBackend):
    """Records the partition and solve calls a driver makes and plays one converged solver iteration."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    @property
    def partitioned(self) -> bool:
        return True

    def grid_convert(
        self,
        mesh_dir: Path,
        msh_name: str,
        *,
        out_dir: Path,
        scale: float | None = None,
        autoclean: bool = False,
        keep_mesh_dir: bool = False,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        raise AssertionError("run_solver never converts")

    def grid_partition(
        self,
        mesh_dir: Path,
        n_parts: int,
        *,
        halobc: bool,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        self.calls.append(("partition", mesh_dir, n_parts, halobc))

        return CommandResult(returncode=0, duration_s=0.0, log_path=mesh_dir / "elmergrid_partition.log", timed_out=False)

    def solve(
        self,
        sim_dir: Path,
        *,
        n_procs: int = 1,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        self.calls.append(("solve", sim_dir, n_procs))
        if on_line is not None:
            on_line("ComputeChange: NS (ITER=1) (NRM,RELC): ( 1.23E+00 4.56E-03 ) :: mgdyn\n")

        return CommandResult(returncode=0, duration_s=0.0, log_path=sim_dir / "elmersolver.log", timed_out=False)


def test_run_solver_requires_build_sim_first(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="build_sim"):
        Driver(backend=_RecordingBackend()).run_solver(SolveState(config=SimulationConfig(), sim_dir=tmp_path))


def test_run_solver_solves_a_serial_case_on_its_backend(tmp_path: Path) -> None:
    backend = _RecordingBackend()
    state = SolveState(config=SimulationConfig(), sim_dir=tmp_path, sim=Sim(physics=Physics.MAGNETOSTATICS))

    Driver(backend=backend).run_solver(state)

    assert backend.calls == [("solve", tmp_path, 1)]
    assert (tmp_path / "convergence_targets.json").is_file()
    assert (tmp_path / "convergence.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_run_solver_partitions_the_bundle_before_a_parallel_solve(tmp_path: Path) -> None:
    from femforge.common.log import log

    backend = _RecordingBackend()
    sim = Sim(physics=Physics.COIL_MAGNETOSTATICS)
    sim.config.solver(MagnetoDynamicsCalcFieldsSolver).calculate_nodal_forces = True
    warnings: list[str] = []
    sink_id: int = log.add(warnings.append, level="WARNING", format="{message}")

    try:
        Driver(backend=backend, mpi_processes=2).run_solver(SolveState(config=SimulationConfig(), sim_dir=tmp_path, sim=sim))
    finally:
        log.remove(sink_id)

    assert backend.calls == [("partition", tmp_path, 2, True), ("solve", tmp_path, 2)]
    assert any("nodal forces" in warning for warning in warnings)


class _StallingBackend(_RecordingBackend):
    """Relays a CG solve whose residual froze and stops relaying once the driver cancels it."""

    def solve(
        self,
        sim_dir: Path,
        *,
        n_procs: int = 1,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        self.calls.append(("solve", sim_dir, n_procs))
        log_path = sim_dir / "elmersolver.log"
        with log_path.open("w", encoding="utf-8") as stream:
            for line in STALL_LOG.read_text(encoding="utf-8").splitlines():
                if cancel is not None and cancel.is_set():
                    break

                _ = stream.write(line + "\n")
                if on_line is not None:
                    on_line(line + "\n")

        return CommandResult(returncode=1, duration_s=0.0, log_path=log_path, timed_out=False)


def test_a_stalled_solve_is_marked_stalled_and_reused(tmp_path: Path) -> None:
    from femforge.elmer.config import PhysicsSelection

    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"), physics=PhysicsSelection(selected=Physics.LINEAR_ELASTICITY))
    backend = _StallingBackend()
    driver = _StagedMeshOnlyDriver(runs_dir=tmp_path, code_sha="sha1", backend=backend)

    result = driver.evaluate(config)

    assert result.status is RunStatus.STALLED
    manifest = load_manifest(tmp_path / _run_name(config))
    assert manifest.status is RunStatus.STALLED
    assert manifest.stall is not None
    assert manifest.stall.iteration == 1339
    assert manifest.stall.solver == "linear elasticity"
    assert len(manifest.stall.tail) == 20

    again = driver.evaluate(config)

    assert again.status is RunStatus.STALLED
    assert [call for call in backend.calls if call[0] == "solve"] == [("solve", tmp_path / _run_name(config), 1)]
