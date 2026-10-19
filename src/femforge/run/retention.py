"""Run retention: which classes of bundle files leave after a run, the policy that says so, and the engine that applies it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated, Iterable

from pydantic import BaseModel, ConfigDict, Field

from femforge.common.config import ConfigArea
from femforge.common.files import RunFile, remove_path, tree_size
from femforge.common.pydantic import Map, mapped_items
from femforge.run.result import ManifestStatus, RunStatus


class Cleanup(StrEnum):
    """When a file class leaves the bundle; definition order is escalation order."""

    NEVER = "never"
    """Keep the files."""

    ON_SUCCESS = "on_success"
    """Remove the files when the run ended ``ok``."""

    ALWAYS = "always"
    """Remove the files whatever the run's status."""

    def applies(self, status: ManifestStatus) -> bool:
        """Whether this rule removes its files for a run that ended in ``status`` (``ON_SUCCESS`` means ``ok`` only)."""
        match self:
            case Cleanup.NEVER:
                return False
            case Cleanup.ON_SUCCESS:
                return status is RunStatus.OK
            case Cleanup.ALWAYS:
                return True

    def escalate(self, other: Cleanup) -> Cleanup:
        """The stricter of two rules."""
        members: list[Cleanup] = list(Cleanup)
        return self if members.index(self) >= members.index(other) else other


class Preset(StrEnum):
    """Named retention policies, from keep-everything to removing the run directory."""

    DEFAULT = "default"
    """Keep everything the run cannot re-fetch; drop store-sourced inputs."""

    KEEP_ALL = "keep_all"
    """Keep everything, store-sourced inputs included."""

    LEAN = "lean"
    """Drop the regenerable meshes after a successful run."""

    MINIMAL = "minimal"
    """Lean plus the field output after a successful run."""

    NUKE = "nuke"
    """Remove the run directory whatever happened."""


class RetentionPolicy(ConfigArea):
    """One [`Cleanup`][] rule per bundle file class; the default keeps everything the run cannot re-fetch."""

    geometry: Annotated[Cleanup, Map(RunFile.GEOMETRY)] = Cleanup.NEVER
    """The ``geometry.step`` copy pinned into the bundle."""

    gmsh_mesh: Annotated[Cleanup, Map(RunFile.GMSH_MESH)] = Cleanup.NEVER
    """The gmsh ``.msh`` (only in the bundle for a parametric geometry)."""

    elmer_mesh: Annotated[Cleanup, Map(RunFile.ELMER_MESH)] = Cleanup.NEVER
    """ElmerGrid's ``mesh.header/nodes/elements/boundary``."""

    partitioning: Annotated[Cleanup, Map(RunFile.PARTITIONING)] = Cleanup.NEVER
    """The ``partitioning.<n>/`` directory and its log from a parallel solve."""

    sif: Annotated[Cleanup, Map(RunFile.SIF)] = Cleanup.NEVER
    """``case.sif`` and ``ELMERSOLVER_STARTINFO``."""

    fields: Annotated[Cleanup, Map(RunFile.FIELDS)] = Cleanup.NEVER
    """The VTU/PVTU field output, master and per-rank pieces together."""

    scalars: Annotated[Cleanup, Map(RunFile.SCALARS)] = Cleanup.NEVER
    """SaveScalars ``.dat`` exports and their ``.names`` sidecars."""

    logs: Annotated[Cleanup, Map(RunFile.LOGS)] = Cleanup.NEVER
    """ElmerGrid/ElmerSolver logs, ``convergence.jsonl``, ``run.log``."""

    sidecars: Annotated[Cleanup, Map(RunFile.SIDECARS)] = Cleanup.NEVER
    """``entities.json``, ``boundaries.yml``, ``components.yml``, ``mesh.names``, ``entities.sif``."""

    run_dir: Annotated[Cleanup, Map(RunFile.RUN_DIR)] = Cleanup.NEVER
    """The whole bundle, manifest included; when it applies nothing else matters."""

    cached_inputs: Cleanup = Cleanup.ALWAYS
    """Escalation for files the artifact store can re-fetch (``manifest.artifacts``); the stricter of this and the class rule wins."""

    def rules(self) -> list[tuple[RunFile, Cleanup]]:
        """The ``(file class, rule)`` pairs, read off the ``Map()`` metadata."""
        rules: list[tuple[RunFile, Cleanup]] = []
        for key, value in mapped_items(self):
            if isinstance(value, Cleanup):
                rules.append((RunFile(key), value))
        return rules

    @classmethod
    def default(cls) -> RetentionPolicy:
        """Keep everything the run cannot re-fetch; drop store-sourced inputs."""
        return cls()

    @classmethod
    def keep_all(cls) -> RetentionPolicy:
        """Nothing leaves, store-sourced inputs included."""
        return cls(cached_inputs=Cleanup.NEVER)

    @classmethod
    def lean(cls) -> RetentionPolicy:
        """Drop the regenerable meshes after a successful run."""
        return cls(
            gmsh_mesh=Cleanup.ON_SUCCESS,
            elmer_mesh=Cleanup.ON_SUCCESS,
            partitioning=Cleanup.ON_SUCCESS,
        )

    @classmethod
    def minimal(cls) -> RetentionPolicy:
        """Lean plus the field output: keep only what re-reading the numbers needs."""
        return cls.lean().model_copy(update={"fields": Cleanup.ON_SUCCESS})

    @classmethod
    def nuke(cls) -> RetentionPolicy:
        """Remove the run directory whatever happened (throwaway evaluations, bundles already pushed elsewhere)."""
        return cls(
            geometry=Cleanup.ALWAYS,
            gmsh_mesh=Cleanup.ALWAYS,
            elmer_mesh=Cleanup.ALWAYS,
            partitioning=Cleanup.ALWAYS,
            sif=Cleanup.ALWAYS,
            fields=Cleanup.ALWAYS,
            scalars=Cleanup.ALWAYS,
            logs=Cleanup.ALWAYS,
            sidecars=Cleanup.ALWAYS,
            run_dir=Cleanup.ALWAYS,
        )

    @classmethod
    def from_preset(cls, preset: Preset) -> RetentionPolicy:
        """The policy a preset names."""
        match preset:
            case Preset.DEFAULT:
                return cls.default()
            case Preset.KEEP_ALL:
                return cls.keep_all()
            case Preset.LEAN:
                return cls.lean()
            case Preset.MINIMAL:
                return cls.minimal()
            case Preset.NUKE:
                return cls.nuke()


class RemovedFile(BaseModel):
    """One path a cleanup pass removed, recorded in the manifest."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    path: str
    """Bundle-relative posix path (``"."`` for the run directory itself)."""

    size: int
    """Bytes freed."""

    file_class: RunFile
    """The retention class the path matched."""

    reason: Cleanup
    """The rule that applied, after any ``cached_inputs`` escalation."""

    cached: bool = False
    """True when the file was store-sourced and re-fetchable."""


class CleanupTrigger(StrEnum):
    """Who ran a cleanup pass."""

    DRIVER = "driver"
    PURGE = "purge"


class CleanupRecord(BaseModel):
    """One cleanup pass over a bundle: when, by whom, under which policy, and what left."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    applied_at: datetime
    """When the pass ran (UTC)."""

    trigger: CleanupTrigger
    """The driver's cleanup stage or a post-hoc purge."""

    status: ManifestStatus
    """The run status the ``ON_SUCCESS`` rules were resolved against."""

    policy: RetentionPolicy
    """The policy that was applied."""

    removed: list[RemovedFile] = Field(default_factory=list)
    """Every path that left, with its class and the rule that removed it."""

    @property
    def bytes_freed(self) -> int:
        """Total bytes the pass removed."""
        return sum(entry.size for entry in self.removed)

    def covers(self, file_class: RunFile) -> bool:
        """Whether this pass removed anything of ``file_class`` (a removed run directory covers every class)."""
        return any(entry.file_class in (file_class, RunFile.RUN_DIR) for entry in self.removed)


@dataclass(frozen=True)
class PlannedRemoval:
    """One path a plan will remove."""

    path: Path
    relative: str
    size: int
    file_class: RunFile
    reason: Cleanup
    cached: bool

    def record(self) -> RemovedFile:
        """The manifest's record of this removal."""
        return RemovedFile(
            path=self.relative,
            size=self.size,
            file_class=self.file_class,
            reason=self.reason,
            cached=self.cached,
        )


@dataclass(frozen=True)
class CleanupPlan:
    """What a policy removes from one bundle in one status: pure data, nothing touched yet."""

    sim_dir: Path
    policy: RetentionPolicy
    status: ManifestStatus
    entries: tuple[PlannedRemoval, ...]
    remove_run_dir: bool
    run_dir_bytes: int = 0

    @property
    def total_bytes(self) -> int:
        """Bytes the plan frees: the whole tree when the run directory goes, else the sum over entries."""
        return self.run_dir_bytes if self.remove_run_dir else sum(entry.size for entry in self.entries)

    def bytes_by_class(self) -> dict[RunFile, int]:
        """Bytes per retention class (``RUN_DIR`` alone when the directory goes)."""
        if self.remove_run_dir:
            return {RunFile.RUN_DIR: self.run_dir_bytes}

        totals: dict[RunFile, int] = {}
        for entry in self.entries:
            totals[entry.file_class] = totals.get(entry.file_class, 0) + entry.size
        return totals


@dataclass
class CleanupReport:
    """The outcome of applying a plan: what left (or would have, in a dry run) and what could not be removed."""

    plan: CleanupPlan
    """The plan that was applied."""

    removed: list[RemovedFile] = field(default_factory=list)
    """Every path that left, or would have in a dry run."""

    failed: list[tuple[str, str]] = field(default_factory=list)
    """The ``(path, error)`` pairs of the paths that could not be removed."""

    dry_run: bool = False
    """Whether the pass only reported, removing nothing."""

    @property
    def bytes_freed(self) -> int:
        """Total bytes removed, or that a dry run would remove."""
        return sum(entry.size for entry in self.removed)


@dataclass(frozen=True)
class BundleEntry:
    """One file found in a bundle, with the retention class its name falls in."""

    relative: str
    """Bundle-relative posix path."""

    size: int
    """File size in bytes."""

    file_class: RunFile
    """The retention class the file's name falls in."""


def bundle_inventory(sim_dir: str | Path) -> list[BundleEntry]:
    """Every file under a bundle with the retention class its name matches; a name no class claims is filed under ``RunFile.RUN_DIR``, the only rule that reaches it."""
    bundle: Path = Path(sim_dir)

    def classify(name: str) -> RunFile:
        for file_class in RunFile:
            if any(fnmatch(name, pattern) for pattern in file_class.patterns):
                return file_class

        return RunFile.RUN_DIR

    entries: list[BundleEntry] = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file():
            continue

        relative: Path = path.relative_to(bundle)
        entries.append(
            BundleEntry(
                relative=relative.as_posix(),
                size=path.stat().st_size,
                file_class=classify(relative.parts[0]),
            )
        )

    return entries


def _is_protected(path: Path) -> bool:
    return path.name in RunFile.BUNDLE.patterns


def plan_cleanup(sim_dir: str | Path, policy: RetentionPolicy, *, status: ManifestStatus, cached: Iterable[str] = ()) -> CleanupPlan:
    """Resolve ``policy`` against one bundle into the sized paths each applying rule removes; refuses a symlinked or junctioned directory.

    Args:
        sim_dir: The run bundle directory.
        policy: The retention policy to resolve.
        status: The run status the ``ON_SUCCESS`` rules resolve against.
        cached: Bundle-relative paths the store can re-fetch; ``policy.cached_inputs`` escalates their rules.
    """
    bundle: Path = Path(sim_dir)
    if bundle.is_symlink() or bundle.is_junction():
        raise ValueError(f"refusing to clean through a link: {bundle}")

    if policy.run_dir.applies(status):
        return CleanupPlan(
            sim_dir=bundle,
            policy=policy,
            status=status,
            entries=(),
            remove_run_dir=True,
            run_dir_bytes=tree_size(bundle),
        )

    cached_paths: frozenset[str] = frozenset(cached)
    seen: set[Path] = set()
    entries: list[PlannedRemoval] = []

    for file_class, rule in policy.rules():
        if file_class is RunFile.RUN_DIR:
            continue

        for pattern in file_class.patterns:
            for match in sorted(bundle.glob(pattern)):
                if match in seen or match.is_symlink() or _is_protected(match):
                    continue

                seen.add(match)

                relative: str = match.relative_to(bundle).as_posix()
                is_cached: bool = relative in cached_paths
                effective: Cleanup = rule.escalate(policy.cached_inputs) if is_cached else rule
                if not effective.applies(status):
                    continue

                entries.append(
                    PlannedRemoval(
                        path=match,
                        relative=relative,
                        size=tree_size(match),
                        file_class=file_class,
                        reason=effective,
                        cached=is_cached,
                    )
                )

    return CleanupPlan(
        sim_dir=bundle,
        policy=policy,
        status=status,
        entries=tuple(entries),
        remove_run_dir=False,
    )


def apply_cleanup(plan: CleanupPlan, *, dry_run: bool = False) -> CleanupReport:
    """Remove every planned path; never raises, so a path that cannot be removed lands in ``report.failed``.

    A planned run directory is only reported (as ``"."``); the caller removes it with ``remove_run_dir`` after its last manifest write.
    """
    report = CleanupReport(plan=plan, dry_run=dry_run)

    if plan.remove_run_dir:
        report.removed.append(
            RemovedFile(
                path=".",
                size=plan.run_dir_bytes,
                file_class=RunFile.RUN_DIR,
                reason=plan.policy.run_dir,
            )
        )
        return report

    for entry in plan.entries:
        if not dry_run:
            try:
                remove_path(entry.path)
            except OSError as error:
                report.failed.append((entry.relative, str(error)))
                continue

        report.removed.append(entry.record())

    return report


def remove_run_dir(sim_dir: str | Path) -> int:
    """Delete a bundle directory outright and return the bytes it held."""
    bundle: Path = Path(sim_dir)
    size: int = tree_size(bundle)
    remove_path(bundle)
    return size
