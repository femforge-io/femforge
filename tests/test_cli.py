"""The femforge command: root resolution, listing, show, path, materialize, rehydrate, purge, and the error exits."""

import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel

import femforge
from femforge import cli
from femforge.artifacts.cache import ArtifactCache
from femforge.artifacts.lock import OWNER_FILE, DirLock, LockOwner
from femforge.artifacts.keys import ArtifactKey
from femforge.artifacts.kind import ArtifactKind, Produced
from femforge.artifacts.local import LocalArtifactStore
from femforge.artifacts.sidecar import ArtifactOrigin, ArtifactRecord, ArtifactRef
from femforge.config import SimulationConfig
from femforge.geometry.config import GeometryArea
from femforge.run.artifacts import prune_store_sourced
from femforge.run.bundle import write_manifest
from femforge.frames import Solid
from femforge.common.log import TELEMETRY_ENV
from femforge.run.kinds import ELMER_MESH, RUN_BUNDLE, STEP, MeshSpec, RunSpec, StepSpec, mesh_spec
from femforge.run.manifest import RunManifest
from femforge.telemetry import cli as telemetry_cli
from femforge.telemetry.paths import Telemetry
from femforge.telemetry.schemas import SweepState
from femforge.telemetry.sweep import SweepJournal
from femforge.run.result import LifecycleStatus, RunStatus


class _StubGeometry(GeometryArea):
    width: float = 1.0

    def build_step(self, path: Path) -> Path:
        _ = path.write_text(f"ISO-10303-21; width={self.width}", encoding="utf-8")
        return path


class _Spec(BaseModel):
    a: int = 1


THINGS = ArtifactKind(name="things", spec_type=_Spec)


def _produce_mesh(spec: MeshSpec, workdir: Path) -> Produced:
    _ = spec
    for name in ELMER_MESH.required_files:
        _ = (workdir / name).write_text("m", encoding="utf-8")
    return Produced(files=ELMER_MESH.required_files)


@dataclass
class _Fixture:
    root: Path
    bundle: Path
    config: SimulationConfig
    cache: ArtifactCache
    manifest: RunManifest

    @property
    def mesh(self) -> ArtifactRef:
        return self.manifest.artifacts[1]


def _fixture(tmp_path: Path) -> _Fixture:
    """A store with a STEP and a mesh, and a bundle outside it whose manifest references both (inputs pruned)."""
    root = tmp_path / "store"
    cache = ArtifactCache(LocalArtifactStore(root))
    config = SimulationConfig(geometry=_StubGeometry(width=2.0))
    bundle = tmp_path / "run"
    bundle.mkdir()

    step = cache.get_or_produce(STEP, StepSpec(source=_StubGeometry(width=2.0), frame=Solid()), bundle)
    mesh = cache.get_or_produce(
        ELMER_MESH,
        mesh_spec(config),
        bundle,
        produce=_produce_mesh,
    )
    manifest = RunManifest.build(
        config,
        status=RunStatus.OK,
        run_id="run123",
        code_sha="sha1",
    )
    manifest.artifacts.extend([step.ref, mesh.ref])
    _ = write_manifest(bundle, manifest)
    _ = prune_store_sourced(bundle)

    return _Fixture(root=root, bundle=bundle, config=config, cache=cache, manifest=manifest)


def _publish_thing(root: Path, key: str) -> Path:
    store = LocalArtifactStore(root)
    typed = ArtifactKey[_Spec](key)
    staging = store.staging_dir(THINGS, typed)
    _ = (staging / "a.txt").write_bytes(b"alpha")

    record = ArtifactRecord.build(
        kind="things",
        key=key,
        producer_version=1,
        spec_type="t:_Spec",
        spec={"a": 1},
        workdir=staging,
        files=["a.txt"],
    )
    assert store.put(THINGS, typed, staging, record)

    return store.path(THINGS, typed)


# --- root resolution ---------------------------------------------------------


def test_resolve_root_prefers_the_flag_then_the_environment_then_the_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _fixture(tmp_path)
    inside = fixture.root / "step" / fixture.manifest.artifacts[0].name / "geometry.step"

    monkeypatch.delenv(cli.ROOT_ENV, raising=False)
    assert cli.resolve_root(str(tmp_path / "flag"), str(inside)) == tmp_path / "flag"
    assert cli.resolve_root(None, str(inside)) == fixture.root

    monkeypatch.setenv(cli.ROOT_ENV, str(tmp_path / "env"))
    assert cli.resolve_root(None, str(inside)) == tmp_path / "env"

    monkeypatch.delenv(cli.ROOT_ENV)
    with pytest.raises(cli.CliError, match=cli.ROOT_ENV):
        _ = cli.resolve_root(None, str(tmp_path / "elsewhere"))
    with pytest.raises(cli.CliError, match="no artifact store"):
        _ = cli.resolve_root(None, None)


def test_console_reconfigures_text_streams_to_utf8_and_widens_when_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapped = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr("sys.stdout", wrapped)
    monkeypatch.setattr("sys.stderr", io.StringIO())

    console = cli.console()
    assert wrapped.encoding == "utf-8"
    assert console.width == cli.PIPED_WIDTH

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdout", _Tty())
    assert cli.console().width != cli.PIPED_WIDTH or cli.console().width > 0


# --- artifacts subcommands ---------------------------------------------------


def test_ls_lists_every_kind_or_one_kind(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = _fixture(tmp_path)
    step_name = fixture.manifest.artifacts[0].name
    mesh_name = fixture.mesh.name

    assert cli.main(["artifacts", "ls", "--root", str(fixture.root)]) == 0
    out = capsys.readouterr().out
    assert step_name in out and mesh_name in out and "MeshSpec" in out

    assert cli.main(["artifacts", "ls", "--root", str(fixture.root), "--kind", "step"]) == 0
    out = capsys.readouterr().out
    assert step_name in out and mesh_name not in out


def test_path_resolves_a_key_a_prefix_a_name_and_a_path_inside(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _fixture(tmp_path)
    mesh_dir = fixture.root / "elmer_mesh" / fixture.mesh.name
    monkeypatch.setenv(cli.ROOT_ENV, str(fixture.root))

    for ref in (fixture.mesh.key, fixture.mesh.key[:10], fixture.mesh.name, str(mesh_dir / "mesh.names")):
        assert cli.main(["artifacts", "path", ref]) == 0
        assert capsys.readouterr().out.strip() == str(mesh_dir)


def test_path_errors_on_no_match_and_on_ambiguity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "store"
    _ = _publish_thing(root, "a" * 64)
    _ = _publish_thing(root, "a" * 8 + "d" * 56)

    assert cli.main(["artifacts", "path", "c" * 64, "--root", str(root)]) == 1
    assert "no artifact" in capsys.readouterr().err

    assert cli.main(["artifacts", "path", "a" * 8, "--root", str(root)]) == 1
    assert "ambiguous" in capsys.readouterr().err


def test_show_prints_a_record_its_files_and_optionally_its_spec(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = _fixture(tmp_path)
    step = fixture.manifest.artifacts[0]

    assert cli.main(["artifacts", "show", step.key, "--root", str(fixture.root)]) == 0
    out = capsys.readouterr().out
    assert f"step/{step.name}" in out and "geometry.step" in out and "width" not in out

    assert cli.main(["artifacts", "show", step.key, "--spec", "--root", str(fixture.root)]) == 0
    assert '"width": 2.0' in capsys.readouterr().out


def test_show_on_a_bundle_lists_its_store_inputs_wherever_the_bundle_lives(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = _fixture(tmp_path)
    fixture.manifest.artifacts.append(ArtifactRef(kind="step", key="0" * 64, origin=ArtifactOrigin.LOCAL))
    _ = write_manifest(fixture.bundle, fixture.manifest)

    assert cli.main(["artifacts", "show", str(fixture.bundle), "--root", str(fixture.root)]) == 0
    out = capsys.readouterr().out
    assert "bundle run123" in out and fixture.mesh.name in out and "not in store" in out

    published = fixture.cache.publish(RUN_BUNDLE, RunSpec(config=fixture.config), fixture.bundle, ["manifest.json"])
    stored = fixture.cache.local.path(RUN_BUNDLE, published.key)
    assert cli.main(["artifacts", "show", str(stored / "manifest.json")]) == 0
    out = capsys.readouterr().out
    assert f"run_bundle/{published.key.name}" in out and "Store-sourced inputs" in out and fixture.mesh.name in out


def test_show_errors_when_nothing_matches(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = _fixture(tmp_path)
    assert cli.main(["artifacts", "show", "nope-nope-00000000", "--root", str(fixture.root)]) == 1
    assert "no artifact" in capsys.readouterr().err


def test_materialize_copies_an_artifact_into_a_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = _fixture(tmp_path)
    into = tmp_path / "out"

    assert cli.main(["artifacts", "materialize", fixture.mesh.name, "--into", str(into), "--verify", "--root", str(fixture.root)]) == 0
    assert f"{len(ELMER_MESH.required_files)} files into" in capsys.readouterr().out
    assert (into / "mesh.names").read_text(encoding="utf-8") == "m"


def test_rehydrate_puts_the_inputs_back_and_notes_a_bundle_inside_the_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = _fixture(tmp_path)
    assert not (fixture.bundle / "geometry.step").exists()

    assert cli.main(["artifacts", "rehydrate", str(fixture.bundle), "--root", str(fixture.root)]) == 0
    out = capsys.readouterr().out
    assert (fixture.bundle / "geometry.step").is_file() and (fixture.bundle / "mesh.names").is_file()
    assert fixture.mesh.name in out and "lives in the store" not in out

    published = fixture.cache.publish(RUN_BUNDLE, RunSpec(config=fixture.config), fixture.bundle, ["manifest.json"])
    stored = fixture.cache.local.path(RUN_BUNDLE, published.key)
    assert cli.main(["artifacts", "rehydrate", str(stored)]) == 0
    assert "lives in the store" in capsys.readouterr().out
    assert (stored / "geometry.step").is_file()


def test_rehydrate_errors_on_a_non_bundle_and_on_a_missing_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = _fixture(tmp_path)

    assert cli.main(["artifacts", "rehydrate", str(tmp_path), "--root", str(fixture.root)]) == 1
    assert "not a run bundle" in capsys.readouterr().err

    fixture.manifest.artifacts.append(ArtifactRef(kind="mystery", key="0" * 64, origin=ArtifactOrigin.LOCAL))
    _ = write_manifest(fixture.bundle, fixture.manifest)
    assert cli.main(["artifacts", "rehydrate", str(fixture.bundle), "--root", str(fixture.root)]) == 1
    assert "mystery" in capsys.readouterr().err


# --- purge -------------------------------------------------------------------


def test_purge_runs_under_the_same_entry_point(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    assert cli.main(["purge", "--runs-dir", str(runs), "--preset", "lean"]) == 0


# --- telemetry subcommands ---------------------------------------------------


FIXTURE_AT = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
FIXTURE_ISO = FIXTURE_AT.isoformat()
SINCE_DAYS = str((datetime.now(timezone.utc) - FIXTURE_AT).days + 1)
"""A --since window that keeps reaching the fixtures as the calendar moves on."""


def _bundle(runs: Path, run_id: str, *, status: str = "ok", description: str = "stall snapshot") -> Path:
    """A synthetic run bundle carrying the manifest keys the index reads."""
    sim_dir = runs / run_id
    sim_dir.mkdir(parents=True)
    payload: dict[str, object] = {
        "run_id": run_id,
        "created_at": FIXTURE_ISO,
        "status": status,
        "physics": "thermal",
        "mesh_key": "9c1d" * 16,
        "config": {"meta": {"description": description, "tags": {"experiment": "thermal_limit"}}, "frame": {"kind": "axisymmetric"}},
    }
    _ = (sim_dir / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    return sim_dir


def _log_journal(root: Path, *rows: dict[str, object]) -> Path:
    """The live journal a process leaves under the telemetry root, holding what parquet has not settled yet."""
    live = root / "live"
    live.mkdir(parents=True, exist_ok=True)
    path = live / "logs-box-999999.jsonl"
    _ = path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    return path


def _record(level: str, message: str, **bound: str) -> dict[str, object]:
    return {"time": FIXTURE_ISO, "level": level, "process": "run_sweep", "message": message, **bound}


def test_a_telemetry_command_without_a_root_fails_clearly(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)

    assert cli.main(["runs", "ls"]) == 1
    assert "no telemetry root" in capsys.readouterr().err


def test_index_build_and_update_report_what_they_read(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = _fixture(tmp_path)
    runs = tmp_path / "runs-dir"
    _ = _bundle(runs, "one-1111")
    root = tmp_path / "telemetry"

    assert cli.main(["index", "build", "--runs-dir", str(runs), "--store", str(fixture.root), "--telemetry", str(root)]) == 0
    out = capsys.readouterr().out
    assert "1 run rows" in out and "2 artifact rows" in out

    assert cli.main(["index", "update", "--runs-dir", str(runs), "--telemetry", str(root)]) == 0
    assert "0 run rows" in capsys.readouterr().out


def test_runs_ls_filters_and_marks_the_runs_in_flight(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    runs = tmp_path / "runs-dir"
    _ = _bundle(runs, "one-1111")
    _ = _bundle(
        runs,
        "two-2222",
        status="error",
        description="a failure",
    )
    root = tmp_path / "telemetry"
    monkeypatch.setenv(TELEMETRY_ENV, str(root))

    assert cli.main(["index", "build", "--runs-dir", str(runs)]) == 0
    _ = capsys.readouterr()

    assert cli.main(["runs", "ls"]) == 0
    out = capsys.readouterr().out
    assert "one-1111" in out and "two-2222" in out and "2 runs" in out

    assert cli.main(["runs", "ls", "--status", "error", "--tag", "experiment=thermal_limit", "--text", "failure", "--since", SINCE_DAYS, "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "two-2222" in out and "one-1111" not in out

    assert cli.main(["runs", "ls", "--sweep", "never-ran"]) == 0
    assert "0 runs" in capsys.readouterr().out

    claim = DirLock(runs / "one-1111.lock")
    assert claim.try_acquire()

    assert cli.main(["runs", "ls", "--live"]) == 0
    assert "heartbeat_age_s" in capsys.readouterr().out

    claim.release()


def test_runs_reap_breaks_silent_claims_and_re_indexes_their_bundles(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    runs = tmp_path / "runs-dir"
    manifest = RunManifest.build(
        SimulationConfig(),
        status=LifecycleStatus.RUNNING,
        run_id="killed-3333",
        code_sha="sha1",
    )
    _ = write_manifest(runs / "killed-3333", manifest)

    lock = runs / "killed-3333.lock"
    lock.mkdir()
    silent = datetime.now(timezone.utc) - timedelta(minutes=10)
    owner = LockOwner(
        host="box",
        pid=4242,
        started_at=silent,
        heartbeat_at=silent,
    )
    _ = (lock / OWNER_FILE).write_text(owner.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))

    assert cli.main(["runs", "reap", "--runs-dir", str(runs)]) == 0
    out = capsys.readouterr().out
    assert "1 claims reaped" in out and "1 rows re-indexed" in out
    assert "killed-3333" in out and "box" in out and "4242" in out and "abandoned" in out
    assert not lock.exists()

    assert cli.main(["runs", "ls", "--status", "abandoned"]) == 0
    assert "killed-3333" in capsys.readouterr().out


def test_logs_query_and_tail_read_the_live_journal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "telemetry"
    _ = _log_journal(
        root,
        _record(
            "ERROR",
            "gmsh died",
            run_id="one-1111",
            sweep_id="sweep-a",
        ),
        _record("INFO", "Beginning evaluation."),
    )

    assert cli.main(["logs", "query", "--telemetry", str(root)]) == 0
    assert "2 records" in capsys.readouterr().out

    assert cli.main(["logs", "query", "--telemetry", str(root), "--level", "ERROR", "--process", "run_sweep", "--run", "one-1111", "--sweep", "sweep-a", "--since", SINCE_DAYS, "--text", "gmsh"]) == 0
    out = capsys.readouterr().out
    assert "1 records" in out and "gmsh died" in out

    assert cli.main(["logs", "tail", "--telemetry", str(root)]) == 0
    out = capsys.readouterr().out
    assert "12:00:00 ERROR    [one-1111] gmsh died" in out
    assert "Beginning evaluation." in out


def test_a_journal_line_that_is_not_json_prints_as_it_stands() -> None:
    assert telemetry_cli.journal_line("half a line") == "half a line"


def test_cell_renders_nulls_timestamps_and_floats() -> None:
    assert telemetry_cli.cell(None) == ""
    assert telemetry_cli.cell(datetime(2026, 9, 5, 12, 30, tzinfo=timezone.utc)) == "2026-09-05 12:30"
    assert telemetry_cli.cell(0.123456) == "0.123"
    assert telemetry_cli.cell(7) == "7"


def test_sweeps_are_listed_and_shown_with_their_failures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "telemetry"
    journal = SweepJournal(Telemetry(root), sweep_id="sweep-a", process="run_sweep")
    journal.start(
        "thermal limit sensitivity",
        total=2,
        skipped=0,
        max_workers=2,
    )
    journal.event(
        "p1",
        SweepState.DONE,
        origin="solved",
        duration_s=3.0,
    )
    journal.event("p2", SweepState.FAILED, error="RuntimeError: gmsh died")
    journal.finish()

    assert cli.main(["sweeps", "ls", "--telemetry", str(root)]) == 0
    assert "thermal limit sensitivity" in capsys.readouterr().out

    assert cli.main(["sweeps", "show", "sweep-a", "--telemetry", str(root)]) == 0
    out = capsys.readouterr().out
    assert "Sweep sweep-a" in out and "Points" in out and "Failures" in out and "gmsh died" in out


def test_a_sweep_without_failures_shows_only_its_points(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "telemetry"
    journal = SweepJournal(Telemetry(root), sweep_id="sweep-b", process="run_sweep")
    journal.start("a clean sweep", total=1)
    journal.event("p1", SweepState.DONE)
    journal.finish()

    assert cli.main(["sweeps", "show", "sweep-b", "--telemetry", str(root)]) == 0
    out = capsys.readouterr().out
    assert "Points" in out and "Failures" not in out


def test_telemetry_compact_folds_the_settled_shards(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "telemetry"
    runs = tmp_path / "runs-dir"
    _ = _bundle(runs, "one-1111")

    assert cli.main(["index", "build", "--runs-dir", str(runs), "--telemetry", str(root)]) == 0
    assert cli.main(["index", "build", "--runs-dir", str(runs), "--telemetry", str(root)]) == 0
    _ = capsys.readouterr()

    assert cli.main(["telemetry", "compact", "--telemetry", str(root), "--settle", "0"]) == 0
    out = capsys.readouterr().out
    assert "Compacted" in out and "runs" in out
    assert len(Telemetry(root).runs.shards()) == 1


def test_artifacts_ls_prefers_the_index_when_one_holds_rows(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _fixture(tmp_path)
    root = tmp_path / "telemetry"
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)

    # No telemetry root at all, then a root whose index is still empty: both fall back to the store scan.
    assert cli.main(["artifacts", "ls", "--root", str(fixture.root)]) == 0
    assert "MeshSpec" in capsys.readouterr().out

    assert cli.main(["artifacts", "ls", "--root", str(fixture.root), "--telemetry", str(root)]) == 0
    assert "MeshSpec" in capsys.readouterr().out

    assert cli.main(["index", "build", "--runs-dir", str(tmp_path / "nothing"), "--store", str(fixture.root), "--telemetry", str(root)]) == 0
    _ = capsys.readouterr()

    assert cli.main(["artifacts", "ls", "--root", str(fixture.root), "--telemetry", str(root)]) == 0
    out = capsys.readouterr().out
    assert "artifacts indexed under" in out and fixture.mesh.name in out


def test_new_starts_a_project_and_refuses_to_write_over_it_without_force(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert cli.main(["new", "magnet-plate"]) == 0
    assert capsys.readouterr().out == "uv run magnet-plate/main.py\n"

    assert cli.main(["new", "magnet-plate"]) == 1
    assert "not empty; pass --force" in capsys.readouterr().err

    assert cli.main(["new", "magnet-plate", "--force"]) == 0
    assert sorted(path.name for path in (tmp_path / "magnet-plate").iterdir()) == [".gitignore", "README.md", "main.py"]


@pytest.mark.parametrize(("version", "requirements"), [("0.1.0", '"femforge[workbench]~=0.1.0"'), ("0.1.0rc1", '"femforge[workbench]==0.1.0rc1", "femforge-workbench==0.1.0rc1"')])
def test_new_pins_the_running_femforge_version(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, version: str, requirements: str) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(femforge, "__version__", version)

    assert cli.main(["new", "magnet-plate"]) == 0
    _ = capsys.readouterr()

    script = (tmp_path / "magnet-plate" / "main.py").read_text(encoding="utf-8")

    assert f"# dependencies = [{requirements}]" in script.splitlines()
