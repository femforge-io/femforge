"""The task worker: it runs the labelled task of a plan it imports by target and writes how it ended, its records as JSON lines."""

import os
import runpy
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pydantic import TypeAdapter

import femforge
import femforge_workbench
from femforge.common.log import RelayedRecord
from femforge_workbench import task_worker
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
    assert "in boom" in outcome.traceback and outcome.traceback.rstrip().endswith("RuntimeError: boom")
    assert "task_worker" not in outcome.traceback, "the traceback opens at the task's own frame"


def test_a_label_the_plan_lacks_fails_with_a_lookup_error(tmp_path: Path) -> None:
    code, outcome, _ = _run(tmp_path, "Missing")

    assert code == TaskExit.FAILED and isinstance(outcome, TaskFailed) and outcome.error == "LookupError"


def test_the_worker_names_its_arguments_when_they_are_missing(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == TaskExit.USAGE
    assert "JOB OUTCOME" in capsys.readouterr().err


INPROCESS = "tw_inprocess"
NOT_CALLABLE = "VALUE = 7\n"


@pytest.fixture
def in_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str, str], tuple[int, TaskDone | TaskFailed | None]]]:
    """Runs the worker in this process on the test script, leaving the import path, the break handler and the log sinks as they were."""
    _ = (tmp_path / f"{INPROCESS}.py").write_text(SCRIPT, encoding="utf-8")
    _ = (tmp_path / "helper.py").write_text(NOT_CALLABLE, encoding="utf-8")
    handler = signal.getsignal(task_worker.BREAK_SIGNUM)
    monkeypatch.setattr(sys, "path", list(sys.path))

    def keep_sinks(**_: object) -> None:
        return None

    monkeypatch.setattr(task_worker, "log_to_stdout_json", keep_sinks)

    def run(factory: str, label: str) -> tuple[int, TaskDone | TaskFailed | None]:
        job = tmp_path / "job.json"
        _ = job.write_text(TaskJob(factory=factory, label=label, path=str(tmp_path), sys_path=[str(tmp_path / "extra")]).model_dump_json(), encoding="utf-8")
        code = main([str(job), str(tmp_path / OUTCOME_FILE)])

        return code, read_outcome(tmp_path / OUTCOME_FILE)

    yield run

    _ = signal.signal(task_worker.BREAK_SIGNUM, handler)
    for name in (INPROCESS, "helper"):
        _ = sys.modules.pop(name, None)


def test_in_process_a_task_runs_to_done_with_the_job_path_first(in_process: Callable[[str, str], tuple[int, TaskDone | TaskFailed | None]], tmp_path: Path) -> None:
    code, outcome = in_process(f"{INPROCESS}:tasks", "Fine")

    assert code == TaskExit.OK and isinstance(outcome, TaskDone)
    assert sys.path[:2] == [str(tmp_path), str(tmp_path / "extra")]
    assert signal.getsignal(task_worker.BREAK_SIGNUM) == signal.SIG_IGN, "a finished task ignores the breaks that follow"


def test_in_process_a_failure_keeps_the_task_frames_only(in_process: Callable[[str, str], tuple[int, TaskDone | TaskFailed | None]]) -> None:
    code, outcome = in_process(f"{INPROCESS}:tasks", "Boom")

    assert code == TaskExit.FAILED and isinstance(outcome, TaskFailed) and outcome.message == "boom"
    assert "task_worker" not in outcome.traceback and "in boom" in outcome.traceback


def test_in_process_a_factory_that_is_not_callable_or_lacks_the_label_fails(in_process: Callable[[str, str], tuple[int, TaskDone | TaskFailed | None]]) -> None:
    code, outcome = in_process("helper:VALUE", "Fine")
    _, missing = in_process(f"{INPROCESS}:tasks", "Missing")

    assert code == TaskExit.FAILED and isinstance(outcome, TaskFailed)
    assert (outcome.error, outcome.message) == ("TypeError", "helper:VALUE is not callable")
    assert isinstance(missing, TaskFailed) and missing.error == "LookupError"


def test_the_first_break_interrupts_and_the_rest_are_ignored() -> None:
    handler = signal.getsignal(task_worker.BREAK_SIGNUM)
    try:
        with pytest.raises(KeyboardInterrupt):
            task_worker.interrupt(task_worker.BREAK_SIGNUM, None)

        assert signal.getsignal(task_worker.BREAK_SIGNUM) == signal.SIG_IGN
    finally:
        _ = signal.signal(task_worker.BREAK_SIGNUM, handler)


def test_posix_breaks_arrive_as_sigterm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    names = runpy.run_path(task_worker.__file__, run_name="task_worker_on_posix")

    assert names["BREAK_SIGNUM"] == signal.SIGTERM
