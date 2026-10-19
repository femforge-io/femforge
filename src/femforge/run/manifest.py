"""Run provenance: the self-describing run manifest and the git version stamp."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from femforge.artifacts.sidecar import ArtifactRef
from femforge.common.files import BundleFile
from femforge.common.names import bundle_name
from femforge.config import SimulationConfig
from femforge.elmer.config import PhysicsConfig
from femforge.elmer.physics import Physics
from femforge.elmer.solver_log import StallVerdict
from femforge.frames import GeometryCheck
from femforge.run.kinds import cache_key, mesh_key, step_key
from femforge.run.result import ManifestStatus, MeshStats
from femforge.run.retention import CleanupRecord
from femforge.run.stages import Stage, StageEvent


__all__ = ["RunManifest", "cache_key", "git_sha", "mesh_key", "step_key"]


def _git(*args: str) -> str | None:
    try:
        out: subprocess.CompletedProcess[str] = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    return out.stdout


def git_sha(*, short: bool = True) -> str | None:
    """The current commit, with ``-dirty`` appended when the working tree has uncommitted changes; None without git or outside a repository."""
    head: str | None = _git("rev-parse", "HEAD")
    if head is None:
        return None

    sha: str = head.strip()
    if not sha:
        return None

    if short:
        sha = sha[:12]
    status: str | None = _git("status", "--porcelain")
    if status is not None and status.strip():
        sha += "-dirty"
    return sha


class RunManifest(BaseModel):
    """The self-describing record of one run."""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    run_id: str
    """The bundle directory name: ``<adjective>-<noun>-<hash8>`` derived from ``cache_key``."""

    cache_key: str
    """The full content hash of the result-affecting inputs; ``run_id`` is its derived name."""

    created_at: datetime
    """When this run was assembled (UTC). Provenance only — never feeds run_id."""

    git_sha: str | None = None
    """Code version that produced the run (``-dirty`` when uncommitted changes were present). Provenance only — never feeds run_id."""

    elmer_version: str | None = None
    """ElmerSolver version parsed from the log, if available."""

    config: SimulationConfig
    """The full typed input that drove both the mesh and the sif."""

    physics: Physics
    """Which physics preset was solved."""

    physics_config: PhysicsConfig
    """The resolved solver stack the run solved with (preset or authored overrides)."""

    mesh_key: str
    """The mesh artifact key (geometry, mesh settings, body grouping)."""

    mesh_name: str = ""
    """The mesh artifact's store directory name; always derived from ``mesh_key``, never read from input."""

    mesh_stats: MeshStats | None = None
    """Mesh size/quality summary for consistency auditing."""

    geometry_check: GeometryCheck | None = None
    """The frame's check of the authored model, recorded on a pass and on a failure; None when the frame meshes the model as authored."""

    status: ManifestStatus
    """Lifecycle status: ``running`` while the pipeline executes, then the terminal solve status (mirrors Result.status), or ``abandoned`` when its claim went silent first."""

    stall: StallVerdict | None = None
    """The watchdog's verdict when it stopped the solve; None otherwise."""

    stage_history: list[StageEvent] = Field(default_factory=list)
    """Per-stage start/finish timeline, appended as the pipeline runs."""

    artifacts: list[ArtifactRef] = Field(default_factory=list)
    """Store-sourced inputs this run consumed (STEP, mesh): kind, key, origin, and the bundle files they materialized —
    enough to re-hydrate the directory and for retention to know what is re-fetchable."""

    published: ArtifactRef | None = None
    """The bundle's own store entry (kind ``run_bundle``, key == ``cache_key``, the files its policy included); None when never published."""

    cleanups: list[CleanupRecord] = Field(default_factory=list)
    """Every retention pass applied to this bundle (the driver's cleanup stage, then any post-hoc purge), oldest first."""

    notes: str = ""
    """Free-form provenance note. Cosmetic — never feeds run_id."""

    @model_validator(mode="after")
    def _derive_mesh_name(self) -> Self:
        self.mesh_name = bundle_name(self.mesh_key)
        return self

    @property
    def current_stage(self) -> Stage | None:
        """The stage currently running — the first event without a finish time — or None when idle/finished; a crashed stage stays current through cleanup."""
        for event in self.stage_history:
            if event.finished_at is None:
                return event.stage

        return None

    def cached_inputs(self) -> frozenset[str]:
        """Bundle-relative paths the artifact store can re-fetch, so retention may drop them freely."""
        return frozenset(name for ref in self.artifacts for name in ref.files)

    @classmethod
    def load(cls, path: str | Path) -> "RunManifest":
        """Load a bundle's ``manifest.json`` (pass the bundle directory or the file itself)."""
        file: Path = Path(path)
        if file.is_dir():
            file = file / BundleFile.MANIFEST
        if not file.is_file():
            raise FileNotFoundError(f"no {BundleFile.MANIFEST} at {file}")

        return cls.model_validate_json(file.read_text(encoding="utf-8"))

    @classmethod
    def build(
        cls,
        config: SimulationConfig,
        *,
        status: ManifestStatus,
        run_id: str | None = None,
        mesh_stats: MeshStats | None = None,
        code_sha: str | None = None,
        elmer_version: str | None = None,
        created_at: datetime | None = None,
        notes: str = "",
    ) -> "RunManifest":
        """Assemble the manifest of ``config``: its ``cache_key`` and ``mesh_key``, and the physics and solver stack the config solves.

        Args:
            config: The run's full typed input.
            status: The manifest's lifecycle status.
            run_id: The bundle name to record verbatim; None derives it from the cache key.
            mesh_stats: The mesh size summary, when known.
            code_sha: The producing commit, stored as the manifest's ``git_sha``.
            elmer_version: The ElmerSolver version, when known.
            created_at: When the run was assembled; None takes the current UTC time.
            notes: A free-form provenance note.
        """
        key: str = cache_key(config)
        return cls(
            run_id=run_id if run_id is not None else bundle_name(key),
            cache_key=key,
            created_at=created_at if created_at is not None else datetime.now(timezone.utc),
            git_sha=code_sha,
            elmer_version=elmer_version,
            config=config,
            physics=config.solved_physics(),
            physics_config=config.resolved_stack(),
            mesh_key=mesh_key(config),
            mesh_stats=mesh_stats,
            status=status,
            notes=notes,
        )
