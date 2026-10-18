"""Driver reuse, run-directory claims, bundle publish, and the cache-served STEP and mesh stages — all without gmsh or Elmer."""

import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, cast

import build123d as b3d
import pytest

from femforge.artifacts.cache import ArtifactCache
from femforge.artifacts.keys import ArtifactKey
from femforge.artifacts.kind import Produced
from femforge.artifacts.local import LocalArtifactStore
from femforge.artifacts.lock import LOCK_SUFFIX, OWNER_FILE, DirLock, LockOwner
from femforge.artifacts.sidecar import ArtifactOrigin
from femforge.frames import Axis, Axisymmetric, FrameUnion, Solid
from femforge.common.names import bundle_name
from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.config import SimulationConfig
from femforge.elmer.config import PhysicsSelection
from femforge.elmer.physics import Physics
from femforge.elmer.runtime.backend import ExecutionBackend
from femforge.execution.streaming import CancelToken, CommandResult, LineSink
from femforge.geometry import Build123dGeometry
from femforge.geometry.config import GeometryArea
from femforge.geometry.tags import TaggedCompound, TaggedPart
from femforge.meshing.backend import MeshBackend, MeshJob, MeshReport
from femforge.meshing.config import MeshConfig
from femforge.physical.conditions import Magnetization
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.materials.air import Air
from femforge.materials.neodymium import N42
from femforge.run import driver as driver_module
from femforge.run.artifacts import BodyGroupRecord, BoundaryGroupRecord, BundlePolicy, MeshGroupsFile, MeshRebindError
from femforge.run.bundle import load_manifest, write_manifest
from femforge.run.driver import Driver, SolveState
from femforge.run.kinds import ELMER_MESH, RUN_BUNDLE, MeshSpec, RunSpec, StepSpec, cache_key
from femforge.run.manifest import RunManifest
from femforge.run.result import MeshStats, Result
from femforge.run.retention import RetentionPolicy
from femforge.run.stages import Stage

FIXTURES: Path = Path(__file__).parent / "fixtures"


def _config() -> SimulationConfig:
    return SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))


def _run_name(config: SimulationConfig) -> str:
    return bundle_name(cache_key(config))


class _CountingSolver:
    """The fixture solver, counting its calls."""

    def __init__(self) -> None:
        self.calls: int = 0

    def __call__(self, config: SimulationConfig, sim_dir: Path) -> MeshStats:
        _ = config
        self.calls += 1
        for name in ("case.dat", "case.dat.names", "elmersolver.log"):
            shutil.copyfile(FIXTURES / name, sim_dir / name)
        return MeshStats(element_count=1000, min_element_size=0.05, max_element_size=1.0)


def _graded_solver(log_text: str) -> driver_module.SolverFn:
    """A solver whose Elmer log is ``log_text`` (so the parse grades it as the test wants)."""

    def solve(config: SimulationConfig, sim_dir: Path) -> MeshStats:
        _ = config
        for name in ("case.dat", "case.dat.names"):
            shutil.copyfile(FIXTURES / name, sim_dir / name)
        _ = (sim_dir / "elmersolver.log").write_text(log_text, encoding="utf-8")
        return MeshStats(element_count=1, min_element_size=1.0, max_element_size=1.0)

    return solve


# --- reuse --------------------------------------------------------------------


def test_reuse_returns_the_finished_bundle_without_solving(tmp_path: Path) -> None:
    config = _config()
    solver = _CountingSolver()
    driver = Driver(runs_dir=tmp_path, code_sha="sha1", solver=solver)

    first = driver.evaluate(config)
    second = driver.evaluate(config)

    assert solver.calls == 1
    assert second.status == "ok"
    assert second.run_id == first.run_id
    assert second.run_dir == tmp_path / _run_name(config)
    assert not list(tmp_path.glob("*" + LOCK_SUFFIX))

    _ = Driver(
        runs_dir=tmp_path,
        code_sha="sha1",
        solver=solver,
        reuse=False,
    ).evaluate(config)
    assert solver.calls == 2


def test_reuse_survives_a_re_export_of_the_authored_step(tmp_path: Path) -> None:
    def model() -> TaggedCompound:
        bar = TaggedPart([b3d.Box(20, 4, 6)], tags={TagKey.MATERIAL: "Steel316"}, name="Bar")
        bar.color = b3d.Color("gray")
        return TaggedCompound(children=[bar])

    step = tmp_path / "authored.step"
    config = SimulationConfig(mesh=MeshConfig(step=str(step)))
    solver = _CountingSolver()
    driver = Driver(runs_dir=tmp_path / "runs", code_sha="sha1", solver=solver)

    model().export_step(str(step))
    first = driver.evaluate(config)
    model().export_step(str(step))
    second = driver.evaluate(config)

    assert solver.calls == 1
    assert second.status == "ok"
    assert second.run_id == first.run_id


def test_reuse_skips_error_bundles_and_accepts_diverged_ones(tmp_path: Path) -> None:
    config = _config()
    fixture = (FIXTURES / "elmersolver.log").read_text(encoding="utf-8")

    errored = Driver(
        runs_dir=tmp_path,
        code_sha="sha1",
        solver=_graded_solver("ERROR:: it broke\n"),
    )
    assert errored.evaluate(config).status == "error"
    solver = _CountingSolver()
    assert Driver(runs_dir=tmp_path, code_sha="sha1", solver=solver).evaluate(config).status == "ok"
    assert solver.calls == 1

    diverged_dir = tmp_path / "diverged"
    diverged = Driver(
        runs_dir=diverged_dir,
        code_sha="sha1",
        solver=_graded_solver(fixture.replace("MAIN:\n", "MAIN: FAILED to converge\n", 1)),
    )
    assert diverged.evaluate(config).status == "diverged"
    again = Driver(runs_dir=diverged_dir, code_sha="sha1", solver=solver).evaluate(config)
    assert again.status == "diverged"
    assert solver.calls == 1


class _VtuDroppingDriver(Driver):
    """Fakes the tool stages and leaves a VTU behind; counts its solves."""

    solves: int = 0

    def mesh(self, state: SolveState) -> None:
        state.element_count = 7

    def run_solver(self, state: SolveState) -> None:
        type(self).solves += 1
        for name in ("case.dat", "case.dat.names", "elmersolver.log"):
            shutil.copyfile(FIXTURES / name, state.sim_dir / name)
        _ = (state.sim_dir / "case_t0001.vtu").write_bytes(b"v" * 100)


def test_reuse_needs_the_vtu_unless_the_policy_is_core(tmp_path: Path) -> None:
    config = _config()
    _VtuDroppingDriver.solves = 0

    minimal = _VtuDroppingDriver(
        runs_dir=tmp_path,
        code_sha="sha1",
        retention=RetentionPolicy.minimal(),
    )
    _ = minimal.evaluate(config)
    bundle = tmp_path / _run_name(config)
    assert not (bundle / "case_t0001.vtu").exists()
    assert _VtuDroppingDriver.solves == 1

    core = _VtuDroppingDriver(runs_dir=tmp_path, code_sha="sha1", bundle_policy=BundlePolicy.CORE)
    assert core.evaluate(config).status == "ok"
    assert _VtuDroppingDriver.solves == 1

    fields = _VtuDroppingDriver(runs_dir=tmp_path, code_sha="sha1")
    assert fields.evaluate(config).status == "ok"
    assert _VtuDroppingDriver.solves == 2
    assert (bundle / "case_t0001.vtu").is_file()


# --- claims -------------------------------------------------------------------


def test_evaluate_waits_for_a_claim_held_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver_module, "CLAIM_POLL_S", 0.01)
    config = _config()
    lock = DirLock(tmp_path / f"{_run_name(config)}{LOCK_SUFFIX}")
    assert lock.try_acquire()
    solver = _CountingSolver()

    def release_later() -> None:
        time.sleep(0.05)
        lock.release()

    thread = threading.Thread(target=release_later)
    thread.start()
    result = Driver(runs_dir=tmp_path, code_sha="sha1", solver=solver).evaluate(config)
    thread.join()

    assert result.status == "ok"
    assert solver.calls == 1
    assert not lock.path.exists()


def test_a_waiting_driver_reuses_what_the_holder_produced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver_module, "CLAIM_POLL_S", 0.01)
    config = _config()
    run_id = _run_name(config)
    lock = DirLock(tmp_path / f"{run_id}{LOCK_SUFFIX}")
    assert lock.try_acquire()

    holder_solver = _CountingSolver()
    waiter_solver = _CountingSolver()

    holder = Driver(runs_dir=tmp_path, code_sha="sha1", solver=holder_solver)

    def produce_then_release() -> None:
        time.sleep(0.05)
        _ = holder._solve_bundle(
            config,
            ArtifactKey[RunSpec](cache_key(config)),
            run_id,
            tmp_path / run_id,
            RetentionPolicy.default(),
        )
        lock.release()

    thread = threading.Thread(target=produce_then_release)
    thread.start()
    result = Driver(runs_dir=tmp_path, code_sha="sha1", solver=waiter_solver).evaluate(config)
    thread.join()

    assert result.status == "ok"
    assert holder_solver.calls == 1
    assert waiter_solver.calls == 0


def test_a_stale_claim_on_the_run_dir_is_broken(tmp_path: Path) -> None:
    config = _config()
    lock_dir = tmp_path / f"{_run_name(config)}{LOCK_SUFFIX}"
    lock_dir.mkdir(parents=True)
    then = datetime.now(timezone.utc) - timedelta(hours=2)
    _ = (lock_dir / OWNER_FILE).write_text(
        LockOwner(
            host="gone",
            pid=1,
            started_at=then,
            heartbeat_at=then,
        ).model_dump_json(),
        encoding="utf-8",
    )

    solver = _CountingSolver()
    assert Driver(runs_dir=tmp_path, code_sha="sha1", solver=solver).evaluate(config).status == "ok"
    assert solver.calls == 1
    assert not lock_dir.exists()


# --- publish ------------------------------------------------------------------


def test_a_cache_publishes_the_bundle_and_serves_it_to_another_runs_dir(tmp_path: Path) -> None:
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"))
    config = _config()
    solver = _CountingSolver()

    first = Driver(
        runs_dir=tmp_path / "a",
        code_sha="sha1",
        solver=solver,
        artifacts=cache,
    ).evaluate(config)

    manifest = load_manifest(tmp_path / "a" / _run_name(config))
    assert manifest.published is not None
    assert manifest.published.kind == RUN_BUNDLE.name
    assert manifest.published.key == manifest.cache_key
    assert manifest.published.origin is ArtifactOrigin.PRODUCED
    assert [event.stage for event in manifest.stage_history] == [Stage.PUBLISH, Stage.CLEANUP]

    stored = cache.local.record(RUN_BUNDLE, ArtifactKey[RunSpec](manifest.cache_key))
    assert stored is not None
    assert {"manifest.json", "result.json", "case.dat", "case.dat.names", "elmersolver.log", "run.log"} <= set(stored.file_names)
    assert stored.tools.code_sha == "sha1"
    assert stored.tools.elmer == "9.0"
    assert cache.local.path(RUN_BUNDLE, ArtifactKey[RunSpec](manifest.cache_key)).name == _run_name(config)

    second = Driver(
        runs_dir=tmp_path / "b",
        code_sha="sha1",
        solver=solver,
        artifacts=cache,
    ).evaluate(config)
    assert solver.calls == 1
    assert second.run_id == first.run_id
    assert second.run_dir == tmp_path / "b" / _run_name(config)
    assert (tmp_path / "b" / _run_name(config) / "result.json").is_file()
    assert (tmp_path / "b" / _run_name(config) / "case.dat").is_file()


def test_an_error_run_is_not_published(tmp_path: Path) -> None:
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"))
    config = _config()
    driver = Driver(
        runs_dir=tmp_path / "runs",
        code_sha="sha1",
        solver=_graded_solver("ERROR:: it broke\n"),
        artifacts=cache,
    )
    assert driver.evaluate(config).status == "error"

    manifest = load_manifest(tmp_path / "runs" / _run_name(config))
    assert manifest.published is None
    assert [event.stage for event in manifest.stage_history] == [Stage.CLEANUP]
    assert list(cache.local.keys(RUN_BUNDLE)) == []


def test_publish_requires_a_cache(tmp_path: Path) -> None:
    config = _config()
    manifest = RunManifest.build(config, status="ok", code_sha="sha1")
    with pytest.raises(RuntimeError, match="artifact cache"):
        _ = Driver(runs_dir=tmp_path).publish(
            manifest,
            tmp_path,
            config,
            Result(frame=Solid(), status="ok"),
        )


# --- cached STEP ----------------------------------------------------------------


BUILDS: list[float] = []


class _CountingGeometry(GeometryArea):
    width: float = 1.0

    def build_step(self, path: Path) -> Path:
        BUILDS.append(self.width)
        _ = path.write_text(f"ISO-10303-21; width={self.width}", encoding="utf-8")
        return path


class _FakedToolsDriver(Driver):
    """Real geometry stage; faked mesh and solve."""

    def mesh(self, state: SolveState) -> None:
        state.element_count = 7
        assert state.config.mesh.step.endswith("geometry.step")
        assert Path(state.config.mesh.step).is_file()

    def run_solver(self, state: SolveState) -> None:
        for name in ("case.dat", "case.dat.names", "elmersolver.log"):
            shutil.copyfile(FIXTURES / name, state.sim_dir / name)


def test_a_cached_geometry_is_built_once_across_physics_variants(tmp_path: Path) -> None:
    BUILDS.clear()
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"))
    magnetic = SimulationConfig(geometry=_CountingGeometry(width=2.0))
    thermal = SimulationConfig(geometry=_CountingGeometry(width=2.0), physics=PhysicsSelection(selected=Physics.THERMAL))
    driver = _FakedToolsDriver(runs_dir=tmp_path / "runs", code_sha="sha1", artifacts=cache)

    _ = driver.evaluate(magnetic)
    _ = driver.evaluate(thermal)
    assert BUILDS == [2.0]

    first = load_manifest(tmp_path / "runs" / _run_name(magnetic))
    second = load_manifest(tmp_path / "runs" / _run_name(thermal))

    assert [ref.kind for ref in first.artifacts] == ["step"]
    assert first.artifacts[0].origin is ArtifactOrigin.PRODUCED
    assert second.artifacts[0].origin is ArtifactOrigin.LOCAL
    assert first.artifacts[0].key == second.artifacts[0].key
    assert first.artifacts[0].files == ["geometry.step"]

    # Store-sourced, so the default policy dropped it after the run and recorded that.
    assert not (tmp_path / "runs" / _run_name(magnetic) / "geometry.step").exists()
    assert [(entry.path, entry.cached) for entry in first.cleanups[0].removed] == [("geometry.step", True)]

    # Recording outside an evaluation is a no-op.
    handle = cache.get_or_produce(driver_module.STEP, StepSpec(source=_CountingGeometry(width=2.0), frame=Solid()), None)
    driver._record_artifact(handle)


class _RodGeometry(Build123dGeometry):
    """A tagged rod on the Z axis."""

    length: float = 10.0

    def model(self) -> TaggedCompound:
        rod = TaggedPart(b3d.Cylinder(radius=2.0, height=self.length), tags={"Material": "Steel316"})
        return TaggedCompound(children=[rod])


def test_each_frame_of_one_geometry_caches_its_own_step(tmp_path: Path) -> None:
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"))
    driver = Driver(runs_dir=tmp_path / "runs", code_sha="sha1", artifacts=cache)
    states = [
        SolveState(config=SimulationConfig(geometry=_RodGeometry(), frame=frame), sim_dir=tmp_path / frame.kind)
        for frame in (Solid(), Axisymmetric())
    ]
    for state in states:
        state.sim_dir.mkdir()
        driver.prepare_geometry(state)

    solid, axisymmetric = states
    assert solid.step_key != axisymmetric.step_key
    assert Path(axisymmetric.config.mesh.step) == axisymmetric.sim_dir / "geometry.step"

    read_step = cast("Callable[[Path], b3d.Compound]", b3d.import_step)
    shapes = [read_step(Path(state.config.mesh.step)) for state in states]
    assert [len(shape.solids()) for shape in shapes] == [1, 0]
    assert shapes[1].area == pytest.approx(20.0)


def test_an_authored_step_is_sectioned_once_per_file_and_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sections: list[Path] = []
    section = Axisymmetric.prepare_geometry

    def counting_prepare_geometry(frame: Axisymmetric, step_in: Path, step_out: Path) -> Path:
        sections.append(step_in)
        return section(frame, step_in, step_out)

    monkeypatch.setattr(Axisymmetric, "prepare_geometry", counting_prepare_geometry)
    authored = _RodGeometry().build_step(tmp_path / "authored.step")
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"))
    driver = Driver(runs_dir=tmp_path / "runs", code_sha="sha1", artifacts=cache)

    def prepared(name: str, frame: FrameUnion, step: Path = authored) -> SolveState:
        state = SolveState(config=SimulationConfig(mesh=MeshConfig(step=str(step)), frame=frame), sim_dir=tmp_path / name)
        state.sim_dir.mkdir()
        driver.prepare_geometry(state)
        return state

    first = prepared("first", Axisymmetric())
    second = prepared("second", Axisymmetric())
    assert sections == [authored]
    assert first.step_key == second.step_key
    assert Path(second.config.mesh.step) == second.sim_dir / "geometry.step"

    _ = prepared("shifted", Axisymmetric(axis=Axis(origin=Vec3(0.5, 0, 0), direction=Direction(0, 0, 1))))
    longer = _RodGeometry(length=12.0).build_step(tmp_path / "longer.step")
    _ = prepared("longer", Axisymmetric(), longer)
    assert sections == [authored, authored, longer]

    # A solid frame meshes the authored file itself: nothing is copied or cached.
    solid = prepared("solid", Solid())
    assert solid.config.mesh.step == str(authored)
    assert sections == [authored, authored, longer]


# --- cached mesh ----------------------------------------------------------------


PRODUCTIONS: list[str] = []


def _magnet_groups() -> MeshGroupsFile:
    """The group records of a magnet in an air box, named as the magnet config's domain names them."""
    return MeshGroupsFile(
        bodies=[
            BodyGroupRecord(
                dim=3,
                name="AIR",
                material="Air",
                entity_tags=[1],
            ),
            BodyGroupRecord(
                dim=3,
                name="N42_MAGNETIZATIONN",
                material="N42",
                selectors=[{"Magnetization": "N"}],
                entity_tags=[2],
            ),
        ],
        boundaries=[
            BoundaryGroupRecord(
                dim=2,
                name="AIR__EXT",
                bodies=("AIR",),
                face_tags=[10],
            ),
            BoundaryGroupRecord(
                dim=2,
                name="AIR__N42_MAGNETIZATIONN",
                bodies=("AIR", "N42_MAGNETIZATIONN"),
                face_tags=[11],
            ),
        ],
        element_count=42,
    )


def _write_elmer_mesh(out_dir: Path) -> None:
    for name in ("mesh.header", "mesh.nodes", "mesh.elements", "mesh.boundary"):
        _ = (out_dir / name).write_text("stub", encoding="utf-8")

    shutil.copyfile(FIXTURES / "mesh.names", out_dir / "mesh.names")


class _MeshCachingDriver(Driver):
    """Real geometry and mesh stages against the cache; the mesh producer and the solve are faked."""

    def _produce_mesh(self, state: SolveState, spec: MeshSpec, workdir: Path) -> Produced:
        PRODUCTIONS.append(spec.step_key)
        _write_elmer_mesh(workdir)
        _ = _magnet_groups().write(workdir)
        return Produced(files=ELMER_MESH.required_files)

    def run_solver(self, state: SolveState) -> None:
        for name in ("case.dat", "case.dat.names", "elmersolver.log"):
            shutil.copyfile(FIXTURES / name, state.sim_dir / name)


def _magnet_config(direction: Direction) -> SimulationConfig:
    return SimulationConfig(
        mesh=MeshConfig(step="data/does-not-exist.step"),
        domain=DomainConfig(materials=[Air(), N42()], tags=[EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=direction)])]),
        physics=PhysicsSelection(selected=Physics.MAGNETOSTATICS),
    )


def test_a_cached_mesh_is_produced_once_and_rebound_per_config(tmp_path: Path) -> None:
    PRODUCTIONS.clear()
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"))
    up = _magnet_config(Direction(0, 0, 1))
    down = _magnet_config(Direction(0, 0, -1))
    driver = _MeshCachingDriver(runs_dir=tmp_path / "runs", code_sha="sha1", artifacts=cache)

    first = driver.evaluate(up)
    second = driver.evaluate(down)
    assert first.status == "ok" and second.status == "ok"
    assert len(PRODUCTIONS) == 1

    for config, expected_origin in ((up, ArtifactOrigin.PRODUCED), (down, ArtifactOrigin.LOCAL)):
        bundle = tmp_path / "runs" / _run_name(config)
        manifest = load_manifest(bundle)
        assert [ref.kind for ref in manifest.artifacts] == ["elmer_mesh"]
        assert manifest.artifacts[0].origin is expected_origin
        assert manifest.mesh_stats is not None and manifest.mesh_stats.element_count == 42
        # Ids come from the cached mesh.names, the sif targets them, entities.json records them, and the mesh files left after the run.
        sif = (bundle / "case.sif").read_text(encoding="utf-8")
        assert "Target Bodies(1) = 2" in sif
        assert "Target Boundaries(1) = 3" in sif
        assert (bundle / "entities.json").is_file()
        assert not (bundle / "mesh.nodes").exists()
        assert (bundle / "case.sif").is_file()
    up_keys = [ref.key for ref in load_manifest(tmp_path / "runs" / _run_name(up)).artifacts]
    down_keys = [ref.key for ref in load_manifest(tmp_path / "runs" / _run_name(down)).artifacts]
    assert up_keys == down_keys


def test_a_cached_mesh_that_no_longer_binds_fails_at_the_mesh_stage(tmp_path: Path) -> None:
    PRODUCTIONS.clear()
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"))
    config = _magnet_config(Direction(0, 0, 1))
    driver = _MeshCachingDriver(runs_dir=tmp_path / "runs", code_sha="sha1", artifacts=cache)
    _ = driver.evaluate(config)

    key = cache.key(ELMER_MESH, driver_module.mesh_spec(config))
    groups_file = cache.local.path(ELMER_MESH, key) / "groups.json"
    # Same byte length: the store checks sizes on fetch, so only a size-preserving corruption reaches the rebind.
    _ = groups_file.write_text(groups_file.read_text(encoding="utf-8").replace('"material": "N42"', '"material": "XXX"'), encoding="utf-8")

    other = _magnet_config(Direction(0, 0, -1))
    with pytest.raises(MeshRebindError, match="material tag"):
        _ = driver.evaluate(other)
    manifest = load_manifest(tmp_path / "runs" / _run_name(other))
    assert manifest.status == "error"
    assert manifest.current_stage is Stage.MESH
    assert len(PRODUCTIONS) == 1


def test_a_manifest_without_a_result_is_not_reusable(tmp_path: Path) -> None:
    config = _config()
    bundle = tmp_path / _run_name(config)
    bundle.mkdir()
    _ = write_manifest(
        bundle,
        RunManifest.build(
            config,
            status="ok",
            run_id=bundle.name,
            code_sha="sha1",
        ),
    )

    solver = _CountingSolver()
    assert Driver(runs_dir=tmp_path, code_sha="sha1", solver=solver).evaluate(config).status == "ok"
    assert solver.calls == 1


class _FakeMesher(MeshBackend):
    """Writes a stub ``.msh`` and reports the magnet-in-air groups, keeping every job it was given."""

    def __init__(self) -> None:
        self.jobs: list[tuple[MeshJob, Path]] = []

    def mesh(self, job: MeshJob, out_dir: Path) -> MeshReport:
        self.jobs.append((job, out_dir))
        _ = (out_dir / job.msh).write_text("msh", encoding="utf-8")

        return MeshReport(msh=job.msh, groups=_magnet_groups(), gmsh_version="4.15.0", duration_s=0.1)


class _FakeConverter(ExecutionBackend):
    """Converts by writing stub Elmer mesh files and the fixture ``mesh.names``; it never partitions or solves."""

    @property
    def partitioned(self) -> bool:
        return False

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
        assert (mesh_dir / msh_name).is_file()
        _write_elmer_mesh(out_dir)
        log_path = out_dir / "elmergrid.log"
        _ = log_path.write_text("converted", encoding="utf-8")

        return CommandResult(returncode=0, duration_s=0.0, log_path=log_path, timed_out=False)

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
        raise AssertionError("the mesh stage never partitions")

    def solve(
        self,
        sim_dir: Path,
        *,
        n_procs: int = 1,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        raise AssertionError("the mesh stage never solves")


def test_an_uncached_mesh_is_meshed_converted_and_bound_in_the_run_dir(tmp_path: Path) -> None:
    mesher = _FakeMesher()
    config = _magnet_config(Direction(0, 0, 1))
    state = SolveState(config=config, sim_dir=tmp_path)

    Driver(runs_dir=tmp_path, code_sha="sha1", mesher=mesher, backend=_FakeConverter()).mesh(state)

    job, out_dir = mesher.jobs[0]
    assert out_dir == tmp_path
    assert job.step == config.mesh.step and job.spec == driver_module.mesh_spec(config)
    assert (tmp_path / "geometry.msh").is_file() and (tmp_path / "groups.json").is_file() and (tmp_path / "entities.json").is_file()
    assert [(group.name, group.id) for group in state.physical_groups] == [("AIR", 1), ("N42_MAGNETIZATIONN", 2)]
    assert [(group.name, group.id) for group in state.boundary_groups] == [("AIR__EXT", 3), ("AIR__N42_MAGNETIZATIONN", 4)]
    assert state.element_count == 42


def test_a_produced_mesh_records_its_gmsh_and_leaves_its_msh_behind(tmp_path: Path) -> None:
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"))
    config = _magnet_config(Direction(0, 0, 1))
    state = SolveState(config=config, sim_dir=tmp_path / "run")
    state.sim_dir.mkdir()
    driver = Driver(
        runs_dir=tmp_path / "runs",
        code_sha="sha1",
        artifacts=cache,
        mesher=_FakeMesher(),
        backend=_FakeConverter(),
    )

    driver.mesh(state)

    record = cache.lookup(ELMER_MESH, driver_module.mesh_spec(config))
    assert record is not None
    assert (record.tools.gmsh, record.tools.code_sha) == ("4.15.0", "sha1")
    assert "geometry.msh" not in record.file_names and "elmergrid.log" in record.file_names
    assert not (state.sim_dir / "geometry.msh").exists()
    assert state.element_count == 42


def test_a_cached_mesh_missing_a_name_warns_and_continues(tmp_path: Path) -> None:
    PRODUCTIONS.clear()
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"))
    config = _magnet_config(Direction(0, 0, 1))
    driver = _MeshCachingDriver(runs_dir=tmp_path / "runs", code_sha="sha1", artifacts=cache)
    _ = driver.evaluate(config)

    key = cache.key(ELMER_MESH, driver_module.mesh_spec(config))
    names_file = cache.local.path(ELMER_MESH, key) / "mesh.names"
    # Comment the line out in place (same byte length, so the fetch size check still passes).
    _ = names_file.write_text(
        names_file.read_text(encoding="utf-8").replace("$ AIR__EXT = 3", "! AIR__EXT = 3"),
        encoding="utf-8",
        newline="\n",
    )

    other = _magnet_config(Direction(0, 0, -1))
    assert driver.evaluate(other).status == "ok"
    assert len(PRODUCTIONS) == 1
