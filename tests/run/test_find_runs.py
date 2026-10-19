"""Metadata search over bundle manifests: RunMeta matching, find_runs filters, and the Run.meta accessor."""

import os
from pathlib import Path

from femforge.config import RunMeta, SimulationConfig
from femforge.run.bundle import write_manifest
from femforge.run.inspect import Run, find_runs
from femforge.run.manifest import RunManifest
from femforge.run.result import RunStatus


def _bundle(runs_dir: Path, name: str, *, description: str | None = None, tags: dict[str, str] | None = None, notes: str = "", mtime: float | None = None) -> Path:
    config = SimulationConfig()
    config.meta.description = description
    config.meta.tags = tags or {}
    manifest = RunManifest.build(config, status=RunStatus.OK, run_id=name, code_sha=name, notes=notes)
    bundle = runs_dir / name
    bundle.mkdir(parents=True)
    path = write_manifest(bundle, manifest)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return bundle


def test_run_meta_matches_by_subset() -> None:
    meta = RunMeta(description="ripple", tags={"experiment": "force_ripple", "scale": "0.25"})
    assert meta.matches({})
    assert meta.matches({"experiment": "force_ripple"})
    assert meta.matches({"experiment": "force_ripple", "scale": "0.25"})
    assert not meta.matches({"experiment": "thermal_limit"})
    assert not meta.matches({"experiment": "force_ripple", "scale": "0.09"})


def test_find_runs_filters_by_tags(tmp_path: Path) -> None:
    _ = _bundle(tmp_path, "brisk-otter-00000001", tags={"experiment": "force_ripple", "scale": "0.25"})
    _ = _bundle(tmp_path, "mellow-lynx-00000002", tags={"experiment": "thermal_limit"})

    hits = find_runs(tmp_path, tags={"experiment": "force_ripple"})
    assert [run.manifest.run_id for run in hits] == ["brisk-otter-00000001"]
    assert find_runs(tmp_path, tags={"experiment": "nope"}) == []


def test_find_runs_matches_text_in_name_description_and_notes(tmp_path: Path) -> None:
    _ = _bundle(tmp_path, "brisk-otter-00000001", description="Ripple sweep at -3pi")
    _ = _bundle(tmp_path, "mellow-lynx-00000002", notes="referee re-run")

    assert [run.path.name for run in find_runs(tmp_path, text="RIPPLE")] == ["brisk-otter-00000001"]
    assert [run.path.name for run in find_runs(tmp_path, text="referee")] == ["mellow-lynx-00000002"]
    assert [run.path.name for run in find_runs(tmp_path, text="otter")] == ["brisk-otter-00000001"]


def test_find_runs_returns_newest_first_and_skips_unreadable(tmp_path: Path) -> None:
    _ = _bundle(tmp_path, "older", mtime=1_000_000.0)
    _ = _bundle(tmp_path, "newer", mtime=2_000_000.0)
    junk = tmp_path / "broken"
    junk.mkdir()
    _ = (junk / "manifest.json").write_text("{not json", encoding="utf-8")

    assert [run.path.name for run in find_runs(tmp_path)] == ["newer", "older"]


def test_run_meta_accessor_reads_the_manifest(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "brisk-otter-00000001", description="hello", tags={"experiment": "x"})
    meta = Run(bundle).meta
    assert meta.description == "hello"
    assert meta.tags == {"experiment": "x"}
