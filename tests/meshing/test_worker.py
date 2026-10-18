"""The mesh worker's command line, and WorkerMesher's job hand-off, environment, outcome reading, log relay and failure reports."""

from __future__ import annotations

import io
import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from rich.console import Console

import femforge
import femforge.meshing.worker as worker
from femforge.common.log import TELEMETRY_ENV, _reset_for_tests, log, setup_logging
from femforge.frames import Solid
from femforge.meshing.backend import MeshDone, MeshError, MeshFailed, MeshJob, MeshReport, WorkerMesher, WorkerProbe, mesh_outcomes
from femforge.meshing.gmsh_options import GmshOptions
from femforge.meshing.groups import MeshGroupsFile
from femforge.meshing.spec import MeshSpec
from femforge.meshing.worker import WorkerExit


if TYPE_CHECKING:
    from loguru import Message, Record


SRC = Path(femforge.__file__).resolve().parents[1]
"""The source tree under test, which a worker child must import too."""

FAKE_WORKER = """\
import json
import os
import sys
import time
from pathlib import Path

from femforge.meshing.backend import MeshDone, MeshFailed, MeshJob, MeshReport
from femforge.meshing.groups import MeshGroupsFile

mode = os.environ["FAKE_WORKER_MODE"]
job = MeshJob.model_validate_json(Path(sys.argv[1]).read_text(encoding="utf-8"))
outcome = Path(sys.argv[2])
target = Path(job.msh)
record = {"level": "INFO", "message": "Fake meshing.", "name": "fake_mesh_worker", "function": "main", "line": 9, "extra": {"elements": 7}}
print(json.dumps(record), flush=True)
print("a plain line", flush=True)

if mode == "done":
    _ = target.write_text("msh", encoding="utf-8")
    seen = {"telemetry": os.environ.get("FEMFORGE_TELEMETRY"), "utf8": os.environ.get("PYTHONUTF8"), "step": job.step, "cwd": os.getcwd()}
    _ = (target.parent / "seen.json").write_text(json.dumps(seen), encoding="utf-8")
    report = MeshReport(msh=target.name, groups=MeshGroupsFile(element_count=7), gmsh_version="4.15.0", duration_s=0.1)
    _ = outcome.write_text(MeshDone(report=report).model_dump_json(), encoding="utf-8")
elif mode == "failed":
    _ = outcome.write_text(MeshFailed(error="MeshError", message="no material").model_dump_json(), encoding="utf-8")
    sys.exit(1)
elif mode == "crash":
    print("Traceback (most recent call last): boom", flush=True)
    sys.exit(3)
else:
    time.sleep(60)
"""
"""A stand-in worker module whose ``FAKE_WORKER_MODE`` picks how the job ends."""


def _spec() -> MeshSpec:
    return MeshSpec(step_key="0" * 64, model_dim=3, global_mesh_size=0.002, gmsh_options=GmshOptions(), deterministic=True)


def _report(msh: str = "geometry.msh") -> MeshReport:
    return MeshReport(msh=msh, groups=MeshGroupsFile(element_count=7), gmsh_version="4.15.0", duration_s=0.1)


@pytest.fixture
def clean_logging() -> Iterator[None]:
    """Restore the stdlib root logger and common.log's state around a test that reconfigures logging."""
    handlers = logging.root.handlers[:]
    level = logging.root.level
    _reset_for_tests()
    yield
    _reset_for_tests()
    logging.root.handlers[:] = handlers
    logging.root.setLevel(level)


@pytest.fixture
def fake_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """The module name of a fake worker on the children's import path, with this source tree behind it."""
    folder = tmp_path / "fake"
    folder.mkdir()
    _ = (folder / "fake_mesh_worker.py").write_text(FAKE_WORKER, encoding="utf-8")
    paths = [str(folder), str(SRC), os.environ.get("PYTHONPATH", "")]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(path for path in paths if path))

    return "fake_mesh_worker"


@pytest.fixture
def relayed() -> Iterator[list[Record]]:
    """The records logged while the test runs, with the terminal sink writing into a buffer."""
    setup_logging(level="DEBUG", console=Console(file=io.StringIO()))
    records: list[Record] = []

    def keep(message: Message) -> None:
        records.append(message.record)

    sink_id: int = log.add(keep, level="DEBUG")
    yield records
    log.remove(sink_id)


@pytest.mark.usefixtures("clean_logging")
def test_main_meshes_into_the_msh_directory_and_writes_a_done_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[tuple[str, Path]] = []

    def mesh_with_gmsh(job: MeshJob, out_dir: Path) -> MeshReport:
        seen.append((job.msh, out_dir))
        log.info("Meshing.")

        return _report()

    monkeypatch.setattr(worker, "mesh_with_gmsh", mesh_with_gmsh)
    target = tmp_path / "out" / "geometry.msh"
    job_file = tmp_path / "job.json"
    outcome_file = tmp_path / "outcome.json"
    _ = job_file.write_text(MeshJob(spec=_spec(), frame=Solid(), step="model.step", msh=str(target)).model_dump_json(), encoding="utf-8")

    code = worker.main([str(job_file), str(outcome_file)])

    assert code == WorkerExit.OK
    assert seen == [("geometry.msh", target.parent)]
    assert mesh_outcomes().validate_json(outcome_file.read_text(encoding="utf-8")) == MeshDone(report=_report())
    assert cast("dict[str, object]", json.loads(capsys.readouterr().out.splitlines()[0]))["message"] == "Meshing."


@pytest.mark.usefixtures("clean_logging")
def test_main_turns_a_meshing_error_into_a_failed_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def mesh_with_gmsh(job: MeshJob, out_dir: Path) -> MeshReport:
        raise MeshError("All entities must have an associated material.")

    monkeypatch.setattr(worker, "mesh_with_gmsh", mesh_with_gmsh)
    job_file = tmp_path / "job.json"
    outcome_file = tmp_path / "outcome.json"
    _ = job_file.write_text(MeshJob(spec=_spec(), frame=Solid(), step="model.step", msh=str(tmp_path / "geometry.msh")).model_dump_json(), encoding="utf-8")

    code = worker.main([str(job_file), str(outcome_file)])

    assert code == WorkerExit.FAILED
    failed = MeshFailed(error="MeshError", message="All entities must have an associated material.")
    assert mesh_outcomes().validate_json(outcome_file.read_text(encoding="utf-8")) == failed


def test_main_without_a_job_prints_its_usage(capsys: pytest.CaptureFixture[str]) -> None:
    code = worker.main([])

    assert code == WorkerExit.USAGE
    assert "JOB and OUTCOME are required" in capsys.readouterr().err


def test_main_probe_prints_the_gmsh_version(capsys: pytest.CaptureFixture[str]) -> None:
    import gmsh

    code = worker.main(["--probe"])

    assert code == WorkerExit.OK
    assert WorkerProbe.model_validate_json(capsys.readouterr().out) == WorkerProbe(gmsh=gmsh.__version__)


@pytest.mark.usefixtures("clean_logging")
def test_worker_mesher_reads_the_report_and_relays_the_worker_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_worker: str, relayed: list[Record]) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", "done")
    monkeypatch.setenv(TELEMETRY_ENV, str(tmp_path / "telemetry"))
    out_dir = tmp_path / "out"
    job = MeshJob(spec=_spec(), frame=Solid(), step="model.step")
    monkeypatch.chdir(tmp_path)

    report = WorkerMesher(module=fake_worker).mesh(job, out_dir)

    seen = cast("dict[str, str | None]", json.loads((out_dir / "seen.json").read_text(encoding="utf-8")))
    assert report == _report()
    assert (out_dir / "geometry.msh").read_text(encoding="utf-8") == "msh"
    assert seen["telemetry"] is None and seen["utf8"] == "1"
    assert seen["step"] == str((tmp_path / "model.step").resolve())
    assert seen["cwd"] != str(out_dir)

    worker_records = [record for record in relayed if record["extra"].get("worker") == "mesh"]
    assert [(record["level"].name, record["message"]) for record in worker_records] == [("INFO", "Fake meshing."), ("DEBUG", "a plain line")]
    assert worker_records[0]["extra"] == {"elements": 7, "worker": "mesh"}
    assert (worker_records[0]["name"], worker_records[0]["function"]) == ("fake_mesh_worker", "main")


@pytest.mark.usefixtures("clean_logging", "relayed")
def test_worker_mesher_raises_the_worker_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_worker: str) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", "failed")

    with pytest.raises(MeshError, match="MeshError: no material"):
        _ = WorkerMesher(module=fake_worker).mesh(MeshJob(spec=_spec(), frame=Solid(), step="model.step"), tmp_path)


@pytest.mark.usefixtures("clean_logging", "relayed")
def test_worker_mesher_reports_a_crash_with_the_worker_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_worker: str) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", "crash")

    with pytest.raises(MeshError, match="exited with code 3 without an outcome") as raised:
        _ = WorkerMesher(module=fake_worker).mesh(MeshJob(spec=_spec(), frame=Solid(), step="model.step"), tmp_path)

    assert "Traceback (most recent call last): boom" in str(raised.value)


@pytest.mark.usefixtures("clean_logging", "relayed")
def test_worker_mesher_stops_a_worker_past_its_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_worker: str) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", "hang")

    with pytest.raises(MeshError, match="ran past its 1.0 s"):
        _ = WorkerMesher(module=fake_worker, timeout_s=1.0).mesh(MeshJob(spec=_spec(), frame=Solid(), step="model.step"), tmp_path)
