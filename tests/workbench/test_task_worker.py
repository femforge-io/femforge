"""The task worker: it runs the labelled task of a plan it imports by target and writes how it ended, its records as JSON lines."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import TypeAdapter

import femforge
import femforge_workbench
from femforge.common.log import RelayedRecord
from femforge_workbench.task_worker import main
from femforge_workbench.tasks import OUTCOME_FILE, WORKER_MODULE, TaskDone, TaskExit, TaskFailed, TaskJob, read_outcome


SCRIPT = """
from femforge.common.log import log
from femforge_workbench.tasks import Task

import helper


def fine():
    log.info(f"fine with {helper.VALUE}")


def boom():
    raise RuntimeError("boom")


def tasks():
    return [Task("Fine", fine), Task("Boom", boom)]
"""


def _run(tmp_path: Path, label: str) -> tuple[int, TaskDone | TaskFailed | None, list[str]]:
    _ = (tmp_path / "script.py").write_text(SCRIPT, encoding="utf-8")
    _ = (tmp_path / "helper.py").write_text("VALUE = 7\n", encoding="utf-8")
    job = tmp_path / "job.json"
    _ = job.write_text(TaskJob(factory="script:tasks", label=label, path=str(tmp_path)).model_dump_json(), encoding="utf-8")

    sources = [Path(femforge.__file__).parents[1], Path(femforge_workbench.__file__).parents[1]]
    env = {name: value for name, value in os.environ.items() if not name.startswith(("COV_CORE_", "COVERAGE_", "FEMFORGE_TELEMETRY"))}
    env["PYTHONPATH"] = os.pathsep.join(str(source) for source in sources)
    done = subprocess.run(
        [sys.executable, "-m", WORKER_MODULE, str(job), str(tmp_path / OUTCOME_FILE)],
        cwd=tmp_path.parent,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )

    return done.returncode, read_outcome(tmp_path / OUTCOME_FILE), done.stdout.splitlines()


def test_a_task_that_returns_writes_done_and_logs_json_lines(tmp_path: Path) -> None:
    code, outcome, lines = _run(tmp_path, "Fine")

    records = [TypeAdapter(RelayedRecord).validate_json(line) for line in lines]
    assert code == TaskExit.OK and isinstance(outcome, TaskDone)
    assert "fine with 7" in [record["message"] for record in records], "the job path makes the script's sibling module importable"


def test_a_task_that_raises_writes_its_type_message_and_traceback(tmp_path: Path) -> None:
    code, outcome, _ = _run(tmp_path, "Boom")

    assert code == TaskExit.FAILED and isinstance(outcome, TaskFailed)
    assert (outcome.error, outcome.message) == ("RuntimeError", "boom")
    assert "in boom" in outcome.traceback


def test_a_label_the_plan_lacks_fails_with_a_lookup_error(tmp_path: Path) -> None:
    code, outcome, _ = _run(tmp_path, "Missing")

    assert code == TaskExit.FAILED and isinstance(outcome, TaskFailed) and outcome.error == "LookupError"


def test_the_worker_names_its_arguments_when_they_are_missing(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == TaskExit.USAGE
    assert "JOB OUTCOME" in capsys.readouterr().err
