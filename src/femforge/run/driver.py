"""
The driver: assemble one run end-to-end (identity, reuse, mesh + sif + solve, parse, bundle, publish, retention); the generators (``Mesher``, ``Sim``) stay run-ignorant.

``evaluate(config) -> Result`` is the interface the optimization layer calls once per evaluation; the solve is isolated behind
an injectable ``solver``, so tests exercise the identity → parse → manifest → bundle path without gmsh/Elmer.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Callable, cast

from femforge.artifacts.cache import ArtifactCache, ArtifactHandle
from femforge.artifacts.keys import ArtifactKey, artifact_key
from femforge.artifacts.kind import Produced
from femforge.artifacts.lock import HEARTBEAT_S, LOCK_SUFFIX, DirLock, keep_alive
from femforge.artifacts.sidecar import ArtifactOrigin, ArtifactRef, ToolVersions
from femforge.common.files import BundleFile, BundleGlob, RunFile
from femforge.common.log import TELEMETRY_ENV, log, run_log_context, telemetry_scope
from femforge.common.names import bundle_name
from femforge.config import SimulationConfig
from femforge.elmer.config import MagnetoDynamicsCalcFieldsSolver, convergence_targets, linear_targets, needs_halobc
from femforge.elmer.runtime.backend import ExecutionBackend
from femforge.elmer.runtime.docker import DockerElmer
from femforge.elmer.runtime.native import NativeElmer
from femforge.elmer.runtime.solve import SolveRun, solve_case
from femforge.elmer.runtime.tools import native_elmer_installed
from femforge.elmer.sim import Sim
from femforge.execution.docker.image import DockerImage
from femforge.frames import FrameGeometryError, GeometryCheck
from femforge.meshing.backend import GmshMesher, MeshBackend, MeshJob, MeshReport
from femforge.run.artifacts import BundlePolicy, MeshGroupsFile, bundle_files, rebind_groups
from femforge.run.bundle import write_bundle, write_manifest
from femforge.run.entities import write_entities
from femforge.run.kinds import ELMER_MESH, RUN_BUNDLE, STEP, MeshSpec, RunSpec, StepSpec, mesh_spec, step_spec
from femforge.run.manifest import RunManifest, cache_key, git_sha
from femforge.run.parse import parse_result
from femforge.run.result import LifecycleStatus, MeshStats, Result, RunStatus
from femforge.run.retention import CleanupRecord, CleanupReport, CleanupTrigger, RetentionPolicy, apply_cleanup, plan_cleanup, remove_run_dir
from femforge.run.stages import Stage, StageEvent


if TYPE_CHECKING:
    from femforge.meshing.boundary import BoundaryGroup
    from femforge.meshing.groups import PhysicalGroup
    from femforge.telemetry.runs import RunIndex


CLAIM_POLL_S: float = 2.0
"""`t_poll` `[s]`: How long a driver waits between checks while another process holds the claim on its run directory."""

REUSABLE_STATUSES: frozenset[RunStatus] = frozenset({RunStatus.OK, RunStatus.DIVERGED, RunStatus.STALLED})
"""Bundle statuses a re-evaluation returns as-is: divergence and a stall are outcomes of the config, an error is a pipeline failure worth retrying."""


SolverFn = Callable[[SimulationConfig, Path], MeshStats]
"""A solve step that fills ``sim_dir`` with Elmer outputs and reports the mesh it built; injecting one replaces the gmsh and Elmer pipeline."""


def repoint_step(config: SimulationConfig, step_path: Path) -> SimulationConfig:
    """A copy of ``config`` whose ``mesh.step`` names ``step_path`` (forward slashes: ElmerGrid on Windows chokes on backslashes in the ``$MeshDB`` path)."""
    mesh_config = config.mesh.model_copy(update={"step": str(step_path).replace("\\", "/")})
    return config.model_copy(update={"mesh": mesh_config})


def materialize_geometry(config: SimulationConfig, sim_dir: Path) -> SimulationConfig:
    """
    Build the parametric geometry (if any) into ``sim_dir``, convert it or the authored STEP into ``config.frame``, and
    return a config whose ``mesh.step`` names the solve-frame STEP (``config`` itself when nothing changed).
    """
    step_path: Path = sim_dir / BundleFile.GEOMETRY_STEP
    authored: Path = Path(config.mesh.step)
    writes_step: bool = config.geometry is not None or config.frame.rewrites_geometry

    if writes_step and step_path.resolve() != authored.resolve():
        # A cached run of this config may have left geometry.step as a hardlink into the store; never write through it.
        step_path.unlink(missing_ok=True)

    if config.geometry is not None:
        authored = config.geometry.build_step(step_path)

    solve_step: Path = config.frame.prepare_geometry(authored, step_path)

    if solve_step == Path(config.mesh.step):
        return config

    return repoint_step(config, solve_step)


def default_backend(*, docker: bool | None = None, docker_image: str | None = None, mpi_processes: int = 1) -> ExecutionBackend:
    """Docker when asked for, when given an image, for a partitioned solve or when no native Elmer is installed; native Elmer otherwise.

    Args:
        docker: True runs Elmer in Docker, False runs it natively, None decides by ``mpi_processes`` and the installed tools.
        docker_image: An image tag to run Elmer in instead of femforge's own image.
        mpi_processes: The solve's MPI rank count.
    """
    if docker is False and docker_image is not None:
        raise ValueError("docker_image runs Elmer in Docker, so it cannot be combined with docker=False.")

    if docker or docker_image is not None or (docker is None and (mpi_processes > 1 or not native_elmer_installed())):
        return DockerElmer(DockerImage(docker_image) if docker_image is not None else None)

    return NativeElmer()


@dataclass
class SolveState:
    """Mutable pipeline state one solve threads through the [`Driver`][] stages; each stage reads what earlier stages populated."""

    config: SimulationConfig
    sim_dir: Path
    step_key: str | None = None
    physical_groups: list[PhysicalGroup] = field(default_factory=list)
    boundary_groups: list[BoundaryGroup] = field(default_factory=list)
    element_count: int = 0
    sim: Sim | None = None


class Driver:
    """Runs each config through the pipeline into a ``runs/<run_id>/`` bundle and returns its typed [`Result`][].

    The config alone decides a run's identity: execution settings such as the backend, the mesher, the rank count, retention and telemetry never enter it.
    """

    def __init__(
        self,
        *,
        runs_dir: str | Path = "runs",
        code_sha: str | None = None,
        solver: SolverFn | None = None,
        mpi_processes: int = 1,
        backend: ExecutionBackend | None = None,
        mesher: MeshBackend | None = None,
        docker: bool | None = None,
        docker_image: str | None = None,
        retention: RetentionPolicy | None = None,
        artifacts: ArtifactCache | None = None,
        reuse: bool = True,
        bundle_policy: BundlePolicy = BundlePolicy.FIELDS,
        telemetry: str | Path | None = None,
    ) -> None:
        """Set where runs land and where each stage executes.

        Args:
            runs_dir: The directory each run's bundle is written under.
            code_sha: The code version each manifest records; None reads it from git.
            solver: A replacement for the staged pipeline, for tests that run without gmsh or Elmer.
            mpi_processes: The ElmerSolver rank count; more than 1 needs a partitioned backend.
            backend: Where ElmerGrid and ElmerSolver run; None picks Docker or native Elmer from ``docker``, ``docker_image`` and ``mpi_processes``.
            mesher: Where gmsh meshes each STEP; None meshes in this process, and a [`WorkerMesher`][femforge.meshing.backend.WorkerMesher] meshes in a child process.
            docker: True runs Elmer in Docker and False runs it natively; it cannot be combined with ``backend``.
            docker_image: An image tag to run Elmer in instead of femforge's own image; it cannot be combined with ``backend``.
            retention: What leaves each bundle after its evaluation; ``evaluate(retention=...)`` overrides it for one call.
            artifacts: The cache the STEP, mesh and bundle stages read from and publish to; None keeps runs store-free.
            reuse: Whether to return a finished bundle with the same identity instead of solving again.
            bundle_policy: Which bundle files a publish carries, and so which files a reused bundle must still hold.
            telemetry: The telemetry root for each run's index row and log records; None falls back to ``FEMFORGE_TELEMETRY``.
        """
        self.runs_dir: Path = Path(runs_dir)

        self.code_sha: str | None = code_sha if code_sha is not None else git_sha()

        self._solver: SolverFn = solver if solver is not None else self._default_solve

        if mpi_processes < 1:
            raise ValueError(f"mpi_processes must be >= 1, got {mpi_processes}")

        if backend is not None and (docker is not None or docker_image is not None):
            raise ValueError("Pass either backend or docker/docker_image, not both.")

        self.backend: ExecutionBackend = backend if backend is not None else default_backend(docker=docker, docker_image=docker_image, mpi_processes=mpi_processes)
        self.mesher: MeshBackend = mesher if mesher is not None else GmshMesher()

        if mpi_processes > 1 and not self.backend.partitioned:
            raise ValueError(f"mpi_processes > 1 needs a partitioned backend such as DockerElmer; {type(self.backend).__name__} is not one.")

        self.mpi_processes: int = mpi_processes
        self.retention: RetentionPolicy = retention if retention is not None else RetentionPolicy.default()
        self.artifacts: ArtifactCache | None = artifacts
        self.reuse: bool = reuse
        self.bundle_policy: BundlePolicy = bundle_policy
        self.telemetry: str | Path | None = telemetry

        self._active: tuple[RunManifest, Path] | None = None
        """The in-flight run [`on_stage_event`][] records into — a Driver executes one solve at a time; set by
        [`evaluate`][] before its first manifest write and cleared once the cleanup stage has recorded."""

    # -- evaluate ------------------------------------------------------------

    def evaluate(self, config: SimulationConfig, *, retention: RetentionPolicy | None = None) -> Result:
        """
        Return the finished bundle for ``config`` when one is reusable, else mesh + sif + solve it under a claim on its
        run directory, parse the result, write and publish the bundle, then apply the retention policy.

        The run directory is ``bundle_name(cache_key)`` — a memorable name derived from the content hash — so
        re-evaluating identical inputs still targets the same bundle. ``retention`` overrides the driver's policy for this call.
        """
        # Every record of this evaluation lands in this driver's telemetry root, not the process default.
        with telemetry_scope(self.telemetry):
            policy: RetentionPolicy = retention if retention is not None else self.retention

            key: ArtifactKey[RunSpec] = ArtifactKey[RunSpec](cache_key(config))
            run_id: str = bundle_name(key)
            sim_dir: Path = self.runs_dir / run_id

            reused: Result | None = self._reuse(config, key, sim_dir)
            if reused is not None:
                return reused

            # One producer per bundle: a second process asking for the same identity waits, then reuses what the first left.
            claim = DirLock(self.runs_dir / f"{run_id}{LOCK_SUFFIX}")
            while not claim.try_acquire():
                time.sleep(CLAIM_POLL_S)
                reused = self._reuse(config, key, sim_dir)
                if reused is not None:
                    return reused

            try:
                with keep_alive(claim, HEARTBEAT_S):
                    return self._solve_bundle(
                        config,
                        key,
                        run_id,
                        sim_dir,
                        policy,
                    )
            finally:
                claim.release()

    def _reuse(self, config: SimulationConfig, key: ArtifactKey[RunSpec], sim_dir: Path) -> Result | None:
        """The finished bundle to return instead of solving: the local one when it still holds what the policy needs, else one fetched from the cache; None to solve."""
        if not self.reuse:
            return None

        result: Result | None = self._reusable_local(key, sim_dir)
        if result is not None:
            log.bind(run_id=sim_dir.name, origin=ArtifactOrigin.LOCAL).info("Reusing bundle.")
            return result

        if self.artifacts is None:
            return None

        handle: ArtifactHandle[RunSpec] | None = self.artifacts.fetch(
            RUN_BUNDLE,
            RunSpec(config=config),
            into=sim_dir,
        )
        if handle is None:
            return None

        log.bind(run_id=sim_dir.name, origin=handle.origin).info("Reusing bundle.")
        return Result.load(sim_dir)

    def _reusable_local(self, key: ArtifactKey[RunSpec], sim_dir: Path) -> Result | None:
        """The local bundle's result when it has this identity, a reusable status, its ``result.json``, and (under a fields policy) an unpurged VTU."""
        try:
            manifest: RunManifest = RunManifest.load(sim_dir)
        except (FileNotFoundError, ValueError):
            return None

        if manifest.cache_key != key or manifest.status not in REUSABLE_STATUSES:
            return None

        if not (sim_dir / BundleFile.RESULT).is_file():
            return None

        if self.bundle_policy is not BundlePolicy.CORE and any(record.covers(RunFile.FIELDS) for record in manifest.cleanups):
            return None

        return Result.load(sim_dir)

    def _solve_bundle(self, config: SimulationConfig, key: ArtifactKey[RunSpec], run_id: str, sim_dir: Path, policy: RetentionPolicy) -> Result:
        """Solve into ``sim_dir`` (the claim is held): manifest, staged solve, parse, bundle, publish, retention."""
        # The name carries ~50 hash bits; on the astronomically-unlikely collision, refuse to mix two runs' bundles.
        try:
            prior_key: str | None = RunManifest.load(sim_dir).cache_key
        except (FileNotFoundError, ValueError):
            prior_key = None
        if prior_key is not None and prior_key != key:
            raise RuntimeError(f"bundle name collision at {sim_dir}: existing cache_key {prior_key} != {key}; move the old bundle aside.")

        sim_dir.mkdir(parents=True, exist_ok=True)

        # Built from the ORIGINAL config: materialization repoints mesh.step, and the geometry key would then digest the derived STEP.
        manifest: RunManifest = RunManifest.build(
            config,
            status=LifecycleStatus.RUNNING,
            run_id=run_id,
            code_sha=self.code_sha,
            created_at=datetime.now(timezone.utc),
        )
        self._active = (manifest, sim_dir)

        try:
            # Every record from the early manifest write through the final bundle write carries run_id and lands in run.log.
            with run_log_context(run_id, sim_dir):
                log.bind(
                    run=run_id,
                    key=key.short,
                    backend=type(self.backend).__name__,
                    mpi=self.mpi_processes,
                    description=config.meta.description or None,
                ).info("Beginning evaluation.")

                # A re-used run dir must not leak a previous result or SaveScalars exports into parse_result's glob-all.
                for stale in (sim_dir / BundleFile.RESULT, *sim_dir.glob(BundleGlob.DAT), *sim_dir.glob(BundleGlob.DAT_NAMES)):
                    stale.unlink(missing_ok=True)

                write_manifest(sim_dir, manifest)
                self._record_run(manifest, sim_dir)

                mesh_stats: MeshStats = self._solver(config, sim_dir)

                result: Result = parse_result(sim_dir, frame=config.frame)
                if manifest.stall is not None:
                    result.status = RunStatus.STALLED

                # Stamp the run identity before the bundle is written, so result.json self-identifies alongside the manifest.
                result.run_id = run_id
                result.run_dir = sim_dir

                manifest.status = result.status
                manifest.mesh_stats = mesh_stats
                manifest.elmer_version = result.elmer_version

                write_bundle(sim_dir, manifest, result)
        except Exception:
            # A crashed solve or parse: the bundle self-describes as an error, and the ALWAYS rules still apply.
            manifest.status = RunStatus.ERROR
            write_manifest(sim_dir, manifest)
            _ = self._finish(manifest, sim_dir, policy)
            raise
        except BaseException:
            # An interrupt leaves the bundle exactly as it is.
            self._active = None
            raise

        # Publish and retention run after the log context closed its run.log handle, so the logs class can go too.
        try:
            if self.artifacts is not None and result.status is not RunStatus.ERROR:
                _ = self._run_stage(
                    Stage.PUBLISH,
                    partial(
                        self.publish,
                        manifest,
                        sim_dir,
                        config,
                        result,
                    ),
                )
        finally:
            report: CleanupReport = self._finish(manifest, sim_dir, policy)

        if report.plan.remove_run_dir:
            result.run_dir = None

        return result

    # -- telemetry -----------------------------------------------------------

    def run_index(self) -> RunIndex | None:
        """The index this driver records into, or None when telemetry is off — which is also what keeps polars unimported."""
        if self.telemetry is None and not os.environ.get(TELEMETRY_ENV):
            return None

        from femforge.telemetry.paths import Telemetry, resolve_root
        from femforge.telemetry.runs import RunIndex

        # The guard above already proved one of the two sources resolve_root reads is set.
        return RunIndex(Telemetry(cast("Path", resolve_root(self.telemetry))), self.runs_dir)

    def _record_run(self, manifest: RunManifest, sim_dir: Path) -> None:
        """Append this run's row to the telemetry index; telemetry must never fail a solve, so a failed write only warns."""
        index: RunIndex | None = self.run_index()
        if index is None:
            return

        try:
            index.record(manifest, sim_dir)
        except OSError as error:
            log.bind(run_id=manifest.run_id, error=str(error)).warning("Run could not be recorded in the telemetry index.")

    # -- publish and cleanup -------------------------------------------------

    def publish(self, manifest: RunManifest, sim_dir: Path, config: SimulationConfig, result: Result) -> ArtifactRef:
        """The [`Stage.PUBLISH`][] default: commit the files `bundle_policy` selects, store-sourced inputs excluded, to the artifact cache under the run identity.

        The store keeps the manifest as it stands on disk at publish time.

        Args:
            manifest: The run's manifest, which records the published reference.
            sim_dir: The run directory the files come from.
            config: The config whose identity keys the published bundle.
            result: The parsed result, whose Elmer version the store records.
        """
        if self.artifacts is None:
            raise RuntimeError("publish requires an artifact cache.")

        files: list[str] = bundle_files(
            sim_dir,
            self.bundle_policy,
            exclude=manifest.cached_inputs(),
        )
        key: ArtifactKey[RunSpec] = self.artifacts.key(RUN_BUNDLE, RunSpec(config=config))
        manifest.published = ArtifactRef(
            kind=RUN_BUNDLE.name,
            key=key,
            origin=ArtifactOrigin.PRODUCED,
            files=files,
        )
        write_manifest(sim_dir, manifest)

        handle: ArtifactHandle[RunSpec] = self.artifacts.publish(
            RUN_BUNDLE,
            RunSpec(config=config),
            sim_dir,
            files,
            ToolVersions(code_sha=self.code_sha, elmer=result.elmer_version),
        )
        manifest.published = handle.ref
        log.bind(
            run_id=manifest.run_id,
            origin=handle.origin,
            files=len(files),
            policy=self.bundle_policy,
        ).info("Bundle published.")
        return handle.ref

    def _finish(self, manifest: RunManifest, sim_dir: Path, policy: RetentionPolicy) -> CleanupReport:
        """Run the cleanup stage as the last recorded event, then remove the run directory when the policy says so."""
        try:
            report: CleanupReport = self._run_stage(
                Stage.CLEANUP,
                partial(
                    self.cleanup,
                    manifest,
                    sim_dir,
                    policy,
                ),
            )
        finally:
            self._active = None

        self._record_run(manifest, sim_dir)

        if report.plan.remove_run_dir:
            # After the stage's finish event: write_manifest creates the directory, so it must be the last write.
            try:
                _ = remove_run_dir(sim_dir)
            except OSError as error:
                log.bind(run_id=manifest.run_id, error=str(error)).warning("Run directory could not be removed.")

        return report

    def cleanup(self, manifest: RunManifest, sim_dir: Path, policy: RetentionPolicy) -> CleanupReport:
        """The [`Stage.CLEANUP`][] default: apply ``policy`` against the run's terminal status and record what left on the manifest.

        Override it to push the bundle elsewhere first, then call ``super()``.

        Args:
            manifest: The run's manifest, which records the cleanup.
            sim_dir: The run directory to clean.
            policy: The retention policy to apply.
        """
        plan = plan_cleanup(
            sim_dir,
            policy,
            status=manifest.status,
            cached=manifest.cached_inputs(),
        )
        report: CleanupReport = apply_cleanup(plan)

        if report.removed:
            manifest.cleanups.append(
                CleanupRecord(
                    applied_at=datetime.now(timezone.utc),
                    trigger=CleanupTrigger.DRIVER,
                    status=manifest.status,
                    policy=policy,
                    removed=report.removed,
                )
            )

        for relative, error in report.failed:
            log.bind(run_id=manifest.run_id, path=relative, error=error).warning("Cleanup could not remove a file.")

        log.bind(
            run_id=manifest.run_id,
            files=len(report.removed),
            bytes_freed=report.bytes_freed,
            remove_run_dir=plan.remove_run_dir,
        ).info("Cleanup applied.")

        return report

    # -- the staged solve ----------------------------------------------------

    def _default_solve(self, config: SimulationConfig, sim_dir: Path) -> MeshStats:
        """
        The real solve as a staged pipeline: geometry -> mesh -> sim -> sif -> ElmerSolver, each stage an overridable
        method sharing one [`SolveState`][]. Subclass and override a stage (or a no-op hook) to customize the pipeline.
        """
        state: SolveState = SolveState(config=config, sim_dir=sim_dir)
        # Self-bound methods (not enum -> attribute dispatch), so subclass stage overrides keep working.
        stages: list[tuple[Stage, Callable[[SolveState], None]]] = [
            (Stage.PREPARE_GEOMETRY, self.prepare_geometry),
            (Stage.PRE_MESH, self.pre_mesh),
            (Stage.MESH, self.mesh),
            (Stage.POST_MESH, self.post_mesh),
            (Stage.BUILD_SIM, self.build_sim),
            (Stage.CONFIGURE_SIM, self.configure_sim),
            (Stage.WRITE_SIF, self.write_sif),
            (Stage.RUN_SOLVER, self.run_solver),
        ]
        for stage, fn in stages:
            self._run_stage(stage, partial(fn, state))
        return self._run_stage(Stage.MESH_STATS, partial(self.mesh_stats, state))

    def _run_stage[T](self, stage: Stage, fn: Callable[[], T]) -> T:
        """Run one pipeline stage under a [`StageEvent`][]: emit it at start, run ``fn``, stamp ``finished_at``, emit again."""
        event: StageEvent = StageEvent(stage=stage, started_at=datetime.now(timezone.utc))
        self.on_stage_event(event)
        out: T = fn()
        event.finished_at = datetime.now(timezone.utc)
        self.on_stage_event(event)
        return out

    def on_stage_event(self, event: StageEvent) -> None:
        """Default stage-event sink: record the event on the active run's manifest and persist it (override + ``super()`` to add behavior)."""
        if self._active is None:
            return

        manifest, sim_dir = self._active

        if event.finished_at is None:
            log.info(f"[{event.stage.name}] started.")
            manifest.stage_history.append(event)
        else:
            time_taken = (event.finished_at - event.started_at).total_seconds()
            log.info(f"[{event.stage.name}] completed ({round(time_taken, 2)}s).")

        write_manifest(sim_dir, manifest)

    def _record_artifact(self, handle: ArtifactHandle[MeshSpec] | ArtifactHandle[StepSpec]) -> None:
        """Note a store-sourced input on the active manifest, so retention and rehydration know what came from the cache."""
        if self._active is None:
            return

        manifest, sim_dir = self._active
        manifest.artifacts.append(handle.ref)
        write_manifest(sim_dir, manifest)

    def _record_geometry_check(self, check: GeometryCheck | None) -> None:
        """Note the frame's verdict on the authored model on the active manifest."""
        if self._active is None or check is None:
            return

        manifest, sim_dir = self._active
        manifest.geometry_check = check
        write_manifest(sim_dir, manifest)

    def prepare_geometry(self, state: SolveState) -> None:
        """Materialize the solve-frame STEP into the run directory (from the artifact cache when one is configured), repoint ``state.config`` at it and record the frame's geometry check."""
        spec: StepSpec = step_spec(state.config)
        state.step_key = artifact_key(STEP, spec)
        step_path: Path = state.sim_dir / BundleFile.GEOMETRY_STEP

        try:
            if self.artifacts is None or spec.passes_through:
                state.config = materialize_geometry(state.config, state.sim_dir)
            else:
                handle: ArtifactHandle[StepSpec] = self.artifacts.get_or_produce(
                    STEP,
                    spec,
                    into=state.sim_dir,
                )
                self._record_artifact(handle)
                state.step_key = handle.key
                state.config = repoint_step(state.config, step_path)
        except FrameGeometryError as error:
            self._record_geometry_check(error.check)
            raise

        self._record_geometry_check(GeometryCheck.load(step_path))

    def pre_mesh(self, state: SolveState) -> None:
        """Hook before meshing (e.g. adjust ``state.config`` or drop extra inputs into ``state.sim_dir``)."""

    def mesh(self, state: SolveState) -> None:
        """Mesh with `mesher` and convert with ElmerGrid, served from the artifact cache when one is configured, then bind the groups to this config."""
        spec: MeshSpec = mesh_spec(state.config, state.step_key)
        if self.artifacts is None:
            _ = self._mesh_into(state, spec, state.sim_dir)
        else:
            handle: ArtifactHandle[MeshSpec] = self.artifacts.get_or_produce(
                ELMER_MESH,
                spec,
                into=state.sim_dir,
                produce=partial(self._produce_mesh, state),
            )
            self._record_artifact(handle)

        self._adopt_mesh(state, MeshGroupsFile.load(state.sim_dir))

    def _mesh_into(self, state: SolveState, spec: MeshSpec, workdir: Path) -> MeshReport:
        """Mesh the run's STEP into ``workdir``, convert it there with ElmerGrid, and write ``groups.json`` beside the Elmer mesh."""
        report: MeshReport = self.mesher.mesh(MeshJob(spec=spec, frame=state.config.frame, step=state.config.mesh.step), workdir)
        _ = self.backend.grid_convert(workdir, report.msh, out_dir=workdir)
        _ = report.groups.write(workdir)

        return report

    def _produce_mesh(self, state: SolveState, spec: MeshSpec, workdir: Path) -> Produced:
        """The mesh artifact's producer: mesh and convert inside ``workdir``, then drop the ``.msh``, which the artifact does not keep."""
        report: MeshReport = self._mesh_into(state, spec, workdir)
        (workdir / report.msh).unlink(missing_ok=True)

        return Produced(
            files=(*ELMER_MESH.required_files, BundleFile.ELMERGRID_LOG),
            tools=ToolVersions(code_sha=self.code_sha, gmsh=report.gmsh_version),
        )

    def _adopt_mesh(self, state: SolveState, groups: MeshGroupsFile) -> None:
        """Bind a materialized mesh to this config: rebuild the groups, take ElmerGrid's ids from ``mesh.names``, write ``entities.json``."""
        from femforge.elmer.mesh_names import parse_mesh_names, reconcile_ids

        bodies, boundaries = rebind_groups(groups, state.config.domain)
        # ElmerGrid renumbers physical groups (boundary tags start after the bodies), so remap each group's id to the
        # Elmer id mesh.names assigned its name before the sif targets it, or BCs land on the wrong/no surface.
        missing: list[str] = reconcile_ids(
            names=parse_mesh_names((state.sim_dir / BundleFile.MESH_NAMES).read_text(encoding="utf-8")),
            physical_groups=bodies,
            boundary_groups=boundaries,
        )
        if missing:
            log.bind(missing=missing).warning("No mesh.names entry; sif ids may not match the Elmer mesh.")

        _ = write_entities(state.sim_dir, bodies, boundaries)

        state.physical_groups = bodies
        state.boundary_groups = boundaries
        state.element_count = groups.element_count

    def post_mesh(self, state: SolveState) -> None:
        """Hook between meshing and sim construction (e.g. amend the Elmer mesh files in ``state.sim_dir``)."""

    def build_sim(self, state: SolveState) -> None:
        """Populate ``state.sim``: the typed `Sim` from the config's resolved solver stack, MPI-adjusted when partitioned."""
        from femforge.elmer.config import prepare_for_mpi

        sim: Sim = Sim(
            state.physical_groups,
            physics=state.config.physics.selected,
            boundary_groups=state.boundary_groups,
            config=state.config.resolved_stack(),
            frame=state.config.frame,
        )
        if self.mpi_processes > 1:
            prepare_for_mpi(sim.config)
        state.sim = sim

    def configure_sim(self, state: SolveState) -> None:
        """
        Hook to edit the typed solver stack (``state.sim.config``) before the sif is generated. Prefer authoring
        ``config.physics.overrides`` instead — edits made here happen after the run identity is computed.
        """

    def write_sif(self, state: SolveState) -> None:
        """Generate the sif object graph from the typed stack and write the sif into the run directory."""
        if state.sim is None:
            raise RuntimeError("write_sif requires build_sim to have populated state.sim.")

        state.sim.generate().write(str(state.sim_dir))

    def run_solver(self, state: SolveState) -> None:
        """Partition the mesh when the solve is parallel (a partitioned backend is checked at construction), then run ElmerSolver on `backend`."""
        from femforge.telemetry.convergence import ConvergenceTargetsFile

        if state.sim is None:
            raise RuntimeError("run_solver requires build_sim to have populated state.sim.")

        if self.mpi_processes > 1:
            # -halobc only when the stack needs it (coil electrode integrals): halo elements double-count the Component
            # force lumping of partition-cut bodies, and partition-split electrode boundaries bias the coil drive without them.
            halobc: bool = needs_halobc(state.sim.config)
            field_solvers = [solver for solver in state.sim.config.solvers if isinstance(solver, MagnetoDynamicsCalcFieldsSolver)]
            if halobc and any(solver.calculate_nodal_forces for solver in field_solvers):
                log.warning("Partitioned coil solve computes nodal forces: halo elements corrupt the Component force lumping — extract forces from a serial solve.")

            # The flat bundle is the Elmer mesh dir itself.
            _ = self.backend.grid_partition(state.sim_dir, self.mpi_processes, halobc=halobc)

        # The sif-authored iteration caps and tolerances contextualize the streamed convergence lines (iter/max, relc → tol, %),
        # and the file keeps them beside convergence.jsonl so the log can be read back the same way later.
        targets = convergence_targets(state.sim.config)
        _ = ConvergenceTargetsFile.of(targets).write(state.sim_dir)

        run: SolveRun = solve_case(
            self.backend,
            state.sim_dir,
            n_procs=self.mpi_processes,
            targets=targets,
            linear_targets=linear_targets(state.sim.config),
        )
        if run.stall is not None and self._active is not None:
            self._active[0].stall = run.stall

    def mesh_stats(self, state: SolveState) -> MeshStats:
        """The built mesh's [`MeshStats`][]: the meshed element count plus the config's size settings."""
        config: SimulationConfig = state.config
        sizes: list[float] = [tag.mesh_size.m_as("m") for tag in config.domain.tags if tag.mesh_size is not None]
        if not sizes:
            sizes = [config.mesh.global_mesh_size.m_as("m")]

        return MeshStats(
            element_count=state.element_count,
            min_element_size=min(sizes),
            max_element_size=max(sizes),
        )
