"""Retention: the Cleanup rules, the policy presets, and the plan/apply engine over a bundle of fake files."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from femforge.common.files import RunFile
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.run import retention as retention_module
from femforge.run.bundle import write_manifest
from femforge.run.manifest import RunManifest
from femforge.run.result import ManifestStatus
from femforge.run.retention import Cleanup, CleanupRecord, CleanupTrigger, Preset, RemovedFile, RetentionPolicy, apply_cleanup, bundle_inventory, plan_cleanup, remove_run_dir

FIXED_DT = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)

SIZES: dict[str, int] = {
    "mesh.header": 10,
    "mesh.nodes": 1000,
    "mesh.elements": 2000,
    "mesh.boundary": 5,
    "mesh.names": 20,
    "entities.sif": 8,
    "case_t0001.vtu": 4096,
    "partitioning.4/part.1.nodes": 300,
    "elmergrid_partition.log": 7,
    "scalars.dat": 50,
    "scalars.dat.names": 60,
    "scalars.dat.0": 55,
    "run.log": 100,
    "elmersolver.log": 200,
    "elmergrid.log": 33,
    "convergence.jsonl": 15,
    "geometry.step": 500,
    "geometry.msh": 700,
    "entities.json": 30,
    "boundaries.yml": 12,
    "components.yml": 14,
    "case.sif": 80,
    "ELMERSOLVER_STARTINFO": 9,
    "result.json": 40,
}


def _bundle(tmp_path: Path, status: ManifestStatus = "ok") -> Path:
    bundle = tmp_path / "run"
    bundle.mkdir()
    manifest = RunManifest.build(
        SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step")),
        status=status,
        run_id="run123",
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    _ = write_manifest(bundle, manifest)
    for name, size in SIZES.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(b"x" * size)
    return bundle


def _names(bundle: Path) -> set[str]:
    return {path.relative_to(bundle).as_posix() for path in bundle.rglob("*")}


# --- Cleanup rules -----------------------------------------------------------


@pytest.mark.parametrize("status", ["running", "ok", "diverged", "error"])
def test_cleanup_rules_by_status(status: ManifestStatus) -> None:
    assert Cleanup.NEVER.applies(status) is False
    assert Cleanup.ALWAYS.applies(status) is True
    assert Cleanup.ON_SUCCESS.applies(status) is (status == "ok")


def test_cleanup_escalation_picks_the_stricter_rule() -> None:
    assert Cleanup.NEVER.escalate(Cleanup.ALWAYS) is Cleanup.ALWAYS
    assert Cleanup.ALWAYS.escalate(Cleanup.NEVER) is Cleanup.ALWAYS
    assert Cleanup.ON_SUCCESS.escalate(Cleanup.NEVER) is Cleanup.ON_SUCCESS
    assert Cleanup.ON_SUCCESS.escalate(Cleanup.ON_SUCCESS) is Cleanup.ON_SUCCESS


# --- Policy -------------------------------------------------------------------


def test_policy_rules_cover_every_class_but_the_protected_bundle() -> None:
    classes = [file_class for file_class, _ in RetentionPolicy().rules()]
    assert classes == [file_class for file_class in RunFile if file_class is not RunFile.BUNDLE]


def test_presets() -> None:
    default = RetentionPolicy.default()
    assert all(rule is Cleanup.NEVER for _, rule in default.rules())
    assert default.cached_inputs is Cleanup.ALWAYS

    assert RetentionPolicy.keep_all().cached_inputs is Cleanup.NEVER

    lean = RetentionPolicy.lean()
    assert (lean.gmsh_mesh, lean.elmer_mesh, lean.partitioning, lean.fields) == (Cleanup.ON_SUCCESS, Cleanup.ON_SUCCESS, Cleanup.ON_SUCCESS, Cleanup.NEVER)

    minimal = RetentionPolicy.minimal()
    assert minimal.fields is Cleanup.ON_SUCCESS
    assert minimal.elmer_mesh is Cleanup.ON_SUCCESS
    assert minimal.logs is Cleanup.NEVER

    nuke = RetentionPolicy.nuke()
    assert nuke.run_dir is Cleanup.ALWAYS
    assert all(rule is Cleanup.ALWAYS for _, rule in nuke.rules())

    for preset in Preset:
        assert isinstance(RetentionPolicy.from_preset(preset), RetentionPolicy)
    assert RetentionPolicy.from_preset(Preset.LEAN) == lean
    assert RetentionPolicy.from_preset(Preset.NUKE) == nuke


# --- Plan ---------------------------------------------------------------------


def test_default_policy_removes_nothing_unless_cached(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    assert plan_cleanup(bundle, RetentionPolicy.default(), status="ok").entries == ()

    plan = plan_cleanup(
        bundle,
        RetentionPolicy.default(),
        status="error",
        cached=["mesh.nodes", "mesh.elements"],
    )
    assert {entry.relative for entry in plan.entries} == {"mesh.nodes", "mesh.elements"}
    assert all(entry.cached and entry.reason is Cleanup.ALWAYS for entry in plan.entries)

    kept = plan_cleanup(
        bundle,
        RetentionPolicy.keep_all(),
        status="ok",
        cached=["mesh.nodes"],
    )
    assert kept.entries == ()


def test_lean_removes_the_meshes_only_on_success(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    plan = plan_cleanup(bundle, RetentionPolicy.lean(), status="ok")

    planned = {entry.relative for entry in plan.entries}
    assert planned == {"geometry.msh", "mesh.header", "mesh.nodes", "mesh.elements", "mesh.boundary", "partitioning.4", "elmergrid_partition.log"}
    assert plan.bytes_by_class() == {RunFile.GMSH_MESH: 700, RunFile.ELMER_MESH: 3015, RunFile.PARTITIONING: 307}
    assert plan.total_bytes == 4022
    assert plan.remove_run_dir is False

    for status in ("error", "diverged", "running"):
        assert plan_cleanup(bundle, RetentionPolicy.lean(), status=status).entries == ()


def test_minimal_adds_the_fields(tmp_path: Path) -> None:
    plan = plan_cleanup(_bundle(tmp_path), RetentionPolicy.minimal(), status="ok")
    assert plan.bytes_by_class()[RunFile.FIELDS] == 4096


def test_manifest_and_result_are_never_planned(tmp_path: Path) -> None:
    everything = RetentionPolicy.nuke().model_copy(update={"run_dir": Cleanup.NEVER})
    plan = plan_cleanup(_bundle(tmp_path), everything, status="running")
    names = {entry.relative for entry in plan.entries}

    assert "manifest.json" not in names
    assert "result.json" not in names
    assert names == set(SIZES) - {"result.json", "partitioning.4/part.1.nodes"} | {"partitioning.4"}
    assert plan.total_bytes == sum(SIZES.values()) - SIZES["result.json"]


def test_nuke_plans_the_whole_directory(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    plan = plan_cleanup(bundle, RetentionPolicy.nuke(), status="error")
    assert plan.remove_run_dir is True
    assert plan.entries == ()
    assert plan.total_bytes == plan.run_dir_bytes == sum(SIZES.values()) + (bundle / "manifest.json").stat().st_size
    assert plan.bytes_by_class() == {RunFile.RUN_DIR: plan.run_dir_bytes}


def test_plan_refuses_a_linked_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(tmp_path)

    def linked(path: Path) -> bool:
        return path == bundle

    monkeypatch.setattr(Path, "is_symlink", linked)
    with pytest.raises(ValueError, match="link"):
        _ = plan_cleanup(bundle, RetentionPolicy.lean(), status="ok")


def test_plan_skips_linked_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(tmp_path)

    def linked(path: Path) -> bool:
        return path.name == "mesh.nodes"

    monkeypatch.setattr(Path, "is_symlink", linked)
    plan = plan_cleanup(bundle, RetentionPolicy.lean(), status="ok")
    assert "mesh.nodes" not in {entry.relative for entry in plan.entries}


# --- Apply --------------------------------------------------------------------


def test_apply_removes_files_and_directories_and_records_relative_paths(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    report = apply_cleanup(plan_cleanup(bundle, RetentionPolicy.lean(), status="ok"))

    assert report.failed == []
    assert report.dry_run is False
    assert report.bytes_freed == 4022
    removed = {entry.path for entry in report.removed}
    assert removed == {"geometry.msh", "mesh.header", "mesh.nodes", "mesh.elements", "mesh.boundary", "partitioning.4", "elmergrid_partition.log"}
    assert all(entry.reason is Cleanup.ON_SUCCESS and not entry.cached for entry in report.removed)
    assert "mesh.nodes" not in _names(bundle)
    assert "partitioning.4" not in _names(bundle)
    assert {"manifest.json", "result.json", "case_t0001.vtu", "mesh.names", "case.sif"} <= _names(bundle)


def test_dry_run_touches_nothing(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    before = _names(bundle)
    report = apply_cleanup(plan_cleanup(bundle, RetentionPolicy.minimal(), status="ok"), dry_run=True)
    assert report.dry_run is True
    assert len(report.removed) == 8
    assert _names(bundle) == before


def test_apply_reports_a_path_it_cannot_remove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(tmp_path)
    real_remove = retention_module.remove_path

    def flaky(path: Path) -> None:
        if path.name == "mesh.nodes":
            raise PermissionError("held open")

        real_remove(path)

    monkeypatch.setattr(retention_module, "remove_path", flaky)
    report = apply_cleanup(plan_cleanup(bundle, RetentionPolicy.lean(), status="ok"))
    assert report.failed == [("mesh.nodes", "held open")]
    assert "mesh.nodes" not in {entry.path for entry in report.removed}
    assert (bundle / "mesh.nodes").exists()
    assert not (bundle / "mesh.elements").exists()


def test_apply_reports_the_run_dir_and_remove_run_dir_deletes_it(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    plan = plan_cleanup(bundle, RetentionPolicy.nuke(), status="ok")
    report = apply_cleanup(plan)

    assert report.removed == [
        RemovedFile(
            path=".",
            size=plan.run_dir_bytes,
            file_class=RunFile.RUN_DIR,
            reason=Cleanup.ALWAYS,
        )
    ]
    assert bundle.is_dir()
    assert remove_run_dir(bundle) == plan.run_dir_bytes
    assert not bundle.exists()


# --- Records ------------------------------------------------------------------


def test_cleanup_record_totals_and_coverage() -> None:
    record = CleanupRecord(
        applied_at=FIXED_DT,
        trigger=CleanupTrigger.DRIVER,
        status="ok",
        policy=RetentionPolicy.lean(),
        removed=[
            RemovedFile(
                path="mesh.nodes",
                size=1000,
                file_class=RunFile.ELMER_MESH,
                reason=Cleanup.ON_SUCCESS,
            )
        ],
    )
    assert record.bytes_freed == 1000
    assert record.covers(RunFile.ELMER_MESH)
    assert not record.covers(RunFile.FIELDS)

    nuked = record.model_copy(
        update={
            "removed": [
                RemovedFile(
                    path=".",
                    size=5,
                    file_class=RunFile.RUN_DIR,
                    reason=Cleanup.ALWAYS,
                )
            ]
        }
    )
    assert nuked.covers(RunFile.FIELDS)

    restored = CleanupRecord.model_validate_json(record.model_dump_json())
    assert restored == record
    assert restored.removed[0].file_class is RunFile.ELMER_MESH


# --- Inventory -----------------------------------------------------------------


def test_bundle_inventory_sizes_every_file_and_names_its_class(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    entries = bundle_inventory(bundle)

    sizes = {entry.relative: entry.size for entry in entries}
    classes = {entry.relative: entry.file_class for entry in entries}
    assert set(sizes) == set(SIZES) | {"manifest.json"}
    assert all(sizes[name] == size for name, size in SIZES.items())
    assert classes["geometry.step"] is RunFile.GEOMETRY
    assert classes["geometry.msh"] is RunFile.GMSH_MESH
    assert classes["mesh.nodes"] is RunFile.ELMER_MESH
    assert classes["partitioning.4/part.1.nodes"] is RunFile.PARTITIONING
    assert classes["elmergrid_partition.log"] is RunFile.PARTITIONING
    assert classes["case.sif"] is RunFile.SIF
    assert classes["case_t0001.vtu"] is RunFile.FIELDS
    assert classes["scalars.dat.names"] is RunFile.SCALARS
    assert classes["elmersolver.log"] is RunFile.LOGS
    assert classes["entities.json"] is RunFile.SIDECARS
    assert classes["manifest.json"] is RunFile.BUNDLE


def test_bundle_inventory_files_an_unclaimed_name_under_the_run_directory(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    _ = (bundle / "notes.txt").write_text("keep me", encoding="utf-8")
    (bundle / "extra").mkdir()
    _ = (bundle / "extra" / "blob.bin").write_bytes(b"ab")

    classes = {entry.relative: entry.file_class for entry in bundle_inventory(bundle)}

    assert classes["notes.txt"] is RunFile.RUN_DIR
    assert classes["extra/blob.bin"] is RunFile.RUN_DIR


def test_bundle_inventory_of_an_empty_directory_is_empty(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()

    assert bundle_inventory(empty) == []
