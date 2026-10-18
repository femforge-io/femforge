"""The runs/<run_id>/ bundle round-trips: write the manifest and result, read them back typed."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from femforge.frames import Solid
from femforge.common.vector import Vec3
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.physical.units import U, quantity
from femforge.run.bundle import load_manifest, load_result, write_bundle, write_manifest
from femforge.run.manifest import RunManifest
from femforge.run.result import MeshStats, Result

FIXED_DT = datetime(2026, 6, 14, 10, 0, 0, tzinfo=timezone.utc)


def test_bundle_write_then_load_round_trips(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    result = Result(
        frame=Solid(),
        status="ok",
        force=quantity(Vec3(1.0, 2.0, 3.0), U.N),
        flux_density_max=1.1 * U.T,
    )
    manifest = RunManifest.build(
        config,
        status="ok",
        mesh_stats=MeshStats(element_count=10, min_element_size=0.1, max_element_size=1.0),
        code_sha="sha1",
        created_at=FIXED_DT,
    )

    bundle = write_bundle(tmp_path / "run", manifest, result)
    assert (bundle / "manifest.json").is_file()
    assert (bundle / "result.json").is_file()
    # The atomic write must not strand its temp siblings.
    assert not list(bundle.glob("*.tmp"))

    loaded_manifest = load_manifest(bundle)
    loaded_result = load_result(bundle)

    assert loaded_manifest.run_id == manifest.run_id
    assert loaded_manifest.mesh_stats is not None
    assert loaded_manifest.mesh_stats.element_count == 10
    assert loaded_result.force is not None and loaded_result.flux_density_max is not None
    assert loaded_result.force.magnitude.x == pytest.approx(1.0)
    assert loaded_result.flux_density_max.to(U.T).magnitude == pytest.approx(1.1)


def test_bundle_copies_geometry_when_step_exists(tmp_path: Path) -> None:
    step = tmp_path / "geo.step"
    step.write_text("ISO-10303-21; fake step", encoding="utf-8")
    config = SimulationConfig(mesh=MeshConfig(step=str(step)))
    result = Result(frame=Solid(), status="ok")
    manifest = RunManifest.build(
        config,
        status="ok",
        code_sha="sha1",
        created_at=FIXED_DT,
    )

    bundle = write_bundle(tmp_path / "run", manifest, result)
    assert (bundle / "geometry.step").read_text(encoding="utf-8") == "ISO-10303-21; fake step"


def test_bundle_keeps_the_solve_frame_step_the_run_meshed(tmp_path: Path) -> None:
    step = tmp_path / "authored.step"
    step.write_text("ISO-10303-21; authored", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    (run / "geometry.step").write_text("ISO-10303-21; section", encoding="utf-8")
    manifest = RunManifest.build(
        SimulationConfig(mesh=MeshConfig(step=str(step))),
        status="ok",
        code_sha="sha1",
        created_at=FIXED_DT,
    )

    bundle = write_bundle(run, manifest, Result(frame=Solid(), status="ok"))
    assert (bundle / "geometry.step").read_text(encoding="utf-8") == "ISO-10303-21; section"


def test_load_result_stamps_the_bundle_dir(tmp_path: Path) -> None:
    # run_dir is never serialized (a baked absolute path would go stale on move); the loader stamps where it actually read from.
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    result = Result(frame=Solid(), status="ok", run_id="abc123", run_dir=tmp_path / "elsewhere")
    manifest = RunManifest.build(
        config,
        status="ok",
        code_sha="sha1",
        created_at=FIXED_DT,
    )

    bundle = write_bundle(tmp_path / "run", manifest, result)
    assert '"run_dir"' not in (bundle / "result.json").read_text(encoding="utf-8")

    loaded = load_result(bundle)
    assert loaded.run_id == "abc123"
    assert loaded.run_dir == bundle


def _manifest(status: str = "running") -> RunManifest:
    from femforge.run.result import ManifestStatus
    from typing import cast

    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    return RunManifest.build(
        config,
        status=cast("ManifestStatus", status),
        code_sha="sha1",
        created_at=FIXED_DT,
    )


def test_write_manifest_creates_the_dir_and_leaves_no_tmp(tmp_path: Path) -> None:
    path = write_manifest(tmp_path / "run", _manifest())
    assert path == tmp_path / "run" / "manifest.json"
    assert load_manifest(tmp_path / "run").status == "running"
    assert not list((tmp_path / "run").glob("*.tmp"))


def test_write_manifest_overwrites_an_existing_manifest(tmp_path: Path) -> None:
    # The early-and-often flow rewrites the same file as the run progresses; the last write must win.
    write_manifest(tmp_path, _manifest("running"))
    write_manifest(tmp_path, _manifest("ok"))
    assert load_manifest(tmp_path).status == "ok"
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_retries_after_a_sharing_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A viz poller holding the file open makes os.replace fail transiently on Windows; one retry must recover.
    import os

    from femforge.common import files

    real_replace = os.replace
    attempts: list[int] = []

    def flaky(src: str | Path, dst: str | Path) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise PermissionError("sharing violation")

        real_replace(src, dst)

    def no_sleep(_s: float) -> None:
        pass

    monkeypatch.setattr(files.os, "replace", flaky)
    monkeypatch.setattr(files.time, "sleep", no_sleep)

    write_manifest(tmp_path, _manifest())
    assert len(attempts) == 2
    assert load_manifest(tmp_path).status == "running"


def test_atomic_write_raises_after_exhausting_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from femforge.common import files

    sleeps: list[float] = []

    def always_locked(_src: str | Path, _dst: str | Path) -> None:
        raise PermissionError("sharing violation")

    monkeypatch.setattr(files.os, "replace", always_locked)
    monkeypatch.setattr(files.time, "sleep", sleeps.append)

    with pytest.raises(PermissionError, match="sharing violation"):
        write_manifest(tmp_path, _manifest())
    # Backoff happens between attempts, not after the final failure.
    assert len(sleeps) == 2
