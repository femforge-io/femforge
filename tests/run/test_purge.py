"""Post-hoc purge: one bundle, a whole runs directory with its skip rules, the rich report, and the CLI."""

import os
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from femforge.artifacts.sidecar import ArtifactOrigin, ArtifactRef
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.run import purge as purge_module
from femforge.run.bundle import write_manifest
from femforge.run.manifest import RunManifest
from femforge.run.purge import SkipReason, cleanup_bundle, main, purge_runs, render_report
from femforge.run.result import LifecycleStatus, ManifestStatus, RunStatus
from femforge.run.retention import Cleanup, CleanupTrigger, RetentionPolicy

FIXED_DT = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)


def _bundle(runs_dir: Path, name: str, status: ManifestStatus = RunStatus.OK, *, created_at: datetime = FIXED_DT) -> Path:
    bundle = runs_dir / name
    bundle.mkdir(parents=True)

    manifest = RunManifest.build(
        SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step")),
        status=status,
        run_id=name,
        code_sha="sha1",
        created_at=created_at,
    )
    _ = write_manifest(bundle, manifest)

    _ = (bundle / "mesh.nodes").write_bytes(b"n" * 1000)
    _ = (bundle / "mesh.elements").write_bytes(b"e" * 2000)
    _ = (bundle / "case_t0001.vtu").write_bytes(b"v" * 4096)
    _ = (bundle / "result.json").write_text("{}", encoding="utf-8")
    return bundle


def test_cleanup_bundle_records_a_purge_pass(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "run")
    report = cleanup_bundle(bundle, RetentionPolicy.lean())

    assert report.bytes_freed == 3000
    assert not (bundle / "mesh.nodes").exists()
    assert (bundle / "case_t0001.vtu").exists()

    manifest = RunManifest.load(bundle)
    assert len(manifest.cleanups) == 1
    assert manifest.cleanups[0].trigger is CleanupTrigger.PURGE
    assert manifest.cleanups[0].status == "ok"
    assert {entry.path for entry in manifest.cleanups[0].removed} == {"mesh.nodes", "mesh.elements"}

    # A second pass with nothing to do leaves no record.
    _ = cleanup_bundle(bundle, RetentionPolicy.lean())
    assert len(RunManifest.load(bundle).cleanups) == 1


def test_cleanup_bundle_dry_run_changes_nothing(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "run")
    report = cleanup_bundle(bundle, RetentionPolicy.minimal(), dry_run=True)
    assert report.dry_run is True
    assert report.bytes_freed == 7096
    assert (bundle / "mesh.nodes").exists()
    assert RunManifest.load(bundle).cleanups == []


def test_cleanup_bundle_applies_the_cached_paths_it_is_given(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "run")

    untouched = cleanup_bundle(bundle, RetentionPolicy.default(), dry_run=True)
    escalated = cleanup_bundle(bundle, RetentionPolicy.default(), dry_run=True, cached=["mesh.nodes"])

    # The default policy keeps every class and drops only what the store can re-fetch, so `cached` alone decides.
    assert untouched.removed == []
    assert [entry.path for entry in escalated.removed] == ["mesh.nodes"]
    assert escalated.removed[0].cached is True
    assert escalated.removed[0].reason is Cleanup.ALWAYS


def test_cleanup_bundle_treats_store_sourced_inputs_as_cached(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "run")
    manifest = RunManifest.load(bundle)
    manifest.artifacts.append(ArtifactRef(kind="elmer_mesh", key="0123456789abcdef" * 4, origin=ArtifactOrigin.LOCAL, files=["mesh.nodes", "mesh.elements"]))
    _ = write_manifest(bundle, manifest)

    report = cleanup_bundle(bundle, RetentionPolicy.default(), dry_run=True)

    # The default policy keeps every class, so only what the manifest marks as re-fetchable goes.
    assert sorted(entry.path for entry in report.removed) == ["mesh.elements", "mesh.nodes"]
    assert all(entry.cached for entry in report.removed)


def test_cleanup_bundle_nuke_removes_the_directory(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "run")
    report = cleanup_bundle(bundle, RetentionPolicy.nuke())
    assert not bundle.exists()
    assert report.removed[0].path == "."


def test_cleanup_bundle_requires_a_manifest(tmp_path: Path) -> None:
    (tmp_path / "not-a-bundle").mkdir()
    with pytest.raises(FileNotFoundError):
        _ = cleanup_bundle(tmp_path / "not-a-bundle", RetentionPolicy.lean())


def test_purge_runs_applies_the_skip_rules(tmp_path: Path) -> None:
    ok = _bundle(tmp_path, "ok-run")
    error = _bundle(tmp_path, "error-run", RunStatus.ERROR)
    _ = _bundle(tmp_path, "running-fresh", LifecycleStatus.RUNNING)
    stale = _bundle(tmp_path, "running-stale", LifecycleStatus.RUNNING)

    old = 1_000_000_000
    os.utime(stale / "manifest.json", (old, old))

    (tmp_path / "no-manifest").mkdir()
    _ = (tmp_path / "stray.txt").write_text("x", encoding="utf-8")

    broken = tmp_path / "broken"
    broken.mkdir()
    _ = (broken / "manifest.json").write_text("{not json", encoding="utf-8")

    policy = RetentionPolicy.lean().model_copy(update={"elmer_mesh": Cleanup.ALWAYS})
    report = purge_runs(tmp_path, policy)

    touched = {cleanup.plan.sim_dir.name for cleanup in report.reports}
    assert touched == {"ok-run", "error-run", "running-stale"}

    assert not (ok / "mesh.nodes").exists()
    assert not (error / "mesh.nodes").exists()
    assert not (stale / "mesh.nodes").exists()
    assert (tmp_path / "running-fresh" / "mesh.nodes").exists()
    assert report.skipped_by_reason() == {SkipReason.RUNNING: 1, SkipReason.NOT_A_BUNDLE: 2, SkipReason.UNREADABLE: 1}
    assert report.bytes_freed == 9000
    assert report.files_removed == 6
    assert report.dry_run is False


def test_purge_runs_filters_by_status_and_age(tmp_path: Path) -> None:
    month_ago = datetime.now(timezone.utc) - timedelta(days=30)
    _ = _bundle(tmp_path, "ok-old", created_at=month_ago)
    _ = _bundle(tmp_path, "ok-new", created_at=datetime.now(timezone.utc))
    _ = _bundle(
        tmp_path,
        "diverged-old",
        RunStatus.DIVERGED,
        created_at=month_ago,
    )

    by_status = purge_runs(
        tmp_path,
        RetentionPolicy.lean(),
        dry_run=True,
        only_status=[RunStatus.OK],
    )
    assert {cleanup.plan.sim_dir.name for cleanup in by_status.reports} == {"ok-old", "ok-new"}
    assert by_status.skipped_by_reason() == {SkipReason.STATUS: 1}

    by_age = purge_runs(
        tmp_path,
        RetentionPolicy.lean(),
        dry_run=True,
        older_than=timedelta(days=1),
    )
    assert {cleanup.plan.sim_dir.name for cleanup in by_age.reports} == {"ok-old", "diverged-old"}
    assert by_age.skipped_by_reason() == {SkipReason.AGE: 1}
    assert (tmp_path / "ok-old" / "mesh.nodes").exists()


def test_purge_runs_skips_links_and_survives_a_failing_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _bundle(tmp_path, "linked")
    _ = _bundle(tmp_path, "explodes")
    _ = _bundle(tmp_path, "fine")

    def linked(path: Path) -> bool:
        return path.name == "linked"

    monkeypatch.setattr(Path, "is_symlink", linked)

    real_cleanup = purge_module.cleanup_bundle

    def flaky(sim_dir: Path, policy: RetentionPolicy, *, dry_run: bool = False) -> object:
        if sim_dir.name == "explodes":
            raise RuntimeError("disk on fire")

        return real_cleanup(sim_dir, policy, dry_run=dry_run)

    monkeypatch.setattr(purge_module, "cleanup_bundle", flaky)
    report = purge_runs(tmp_path, RetentionPolicy.lean())
    assert {cleanup.plan.sim_dir.name for cleanup in report.reports} == {"fine"}
    assert report.skipped_by_reason() == {SkipReason.LINK: 1, SkipReason.FAILED: 1}


def test_render_report_lists_classes_totals_and_skips(tmp_path: Path) -> None:
    _ = _bundle(tmp_path, "ok-run")
    (tmp_path / "no-manifest").mkdir()
    report = purge_runs(tmp_path, RetentionPolicy.minimal(), dry_run=True)

    sink = StringIO()
    Console(file=sink, width=200).print(render_report(report))
    text: str = sink.getvalue()

    assert "Would free" in text
    assert "elmer_mesh" in text
    assert "fields" in text
    assert "skipped: not_a_bundle" in text
    assert report.files_by_class() == {purge_module.RunFile.ELMER_MESH: 2, purge_module.RunFile.FIELDS: 1}


def test_main_is_a_dry_run_unless_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(tmp_path, "ok-run")
    sink = StringIO()
    monkeypatch.setattr(purge_module, "get_console", lambda: Console(file=sink, width=200))

    assert main(["--runs-dir", str(tmp_path), "--preset", "lean"]) == 0
    assert (bundle / "mesh.nodes").exists()
    assert "Dry run" in sink.getvalue()

    assert main(["--runs-dir", str(tmp_path), "--preset", "lean", "--dry-run"]) == 0
    assert (bundle / "mesh.nodes").exists()

    assert main(["--runs-dir", str(tmp_path), "--preset", "lean", "--fields", "always", "--only-status", "ok", "--older-than", "0", "--running-grace", "1", "--apply"]) == 0
    assert not (bundle / "mesh.nodes").exists()
    assert not (bundle / "case_t0001.vtu").exists()
    assert (bundle / "result.json").exists()


def test_main_only_status_selects_abandoned_bundles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    abandoned = _bundle(tmp_path, "abandoned-run", LifecycleStatus.ABANDONED)
    finished = _bundle(tmp_path, "ok-run")
    monkeypatch.setattr(purge_module, "get_console", lambda: Console(file=StringIO(), width=200))

    assert main(["--runs-dir", str(tmp_path), "--preset", "lean", "--elmer-mesh", "always", "--only-status", "abandoned", "--apply"]) == 0

    assert not (abandoned / "mesh.nodes").exists()
    assert (finished / "mesh.nodes").exists()


def test_main_only_status_selects_stalled_bundles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stalled = _bundle(tmp_path, "stalled-run", RunStatus.STALLED)
    finished = _bundle(tmp_path, "ok-run")
    monkeypatch.setattr(purge_module, "get_console", lambda: Console(file=StringIO(), width=200))

    assert main(["--runs-dir", str(tmp_path), "--preset", "lean", "--elmer-mesh", "always", "--only-status", "stalled", "--apply"]) == 0

    assert not (stalled / "mesh.nodes").exists()
    assert (finished / "mesh.nodes").exists()
