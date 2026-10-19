"""Background tasks: the plan, the phases, the rows records move, the serial queue across saves, failures, and the stop sequence on real child processes."""

import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import pytest

import femforge
import femforge_workbench
from femforge.common.log import RelayedRecord
from femforge.run.stages import Stage
from femforge_workbench.tasks import (
    OUTCOME_FILE,
    Task,
    TaskBoard,
    TaskDone,
    TaskFailed,
    TaskField,
    TaskJob,
    TaskPhase,
    TaskProcess,
    TaskRow,
    TaskView,
    clock,
    observe,
    ordinal,
    plan_of,
)


TIMEOUT = 30.0
EXPECTED_PHASES = {
    Stage.PREPARE_GEOMETRY: TaskPhase.GEOMETRY,
    Stage.PRE_MESH: TaskPhase.MESHING,
    Stage.MESH: TaskPhase.MESHING,
    Stage.POST_MESH: TaskPhase.MESHING,
    Stage.BUILD_SIM: TaskPhase.SOLVING,
    Stage.CONFIGURE_SIM: TaskPhase.SOLVING,
    Stage.WRITE_SIF: TaskPhase.SOLVING,
    Stage.RUN_SOLVER: TaskPhase.SOLVING,
    Stage.MESH_STATS: TaskPhase.FINISHING,
    Stage.PUBLISH: TaskPhase.FINISHING,
    Stage.CLEANUP: TaskPhase.FINISHING,
}


def solve_all() -> None:
    """A named function, labelled by its name."""


def _record(**extra: object) -> RelayedRecord:
    return {"level": "INFO", "message": "m", "name": "main", "function": "solve", "line": 1, "extra": dict(extra)}


class Host:
    """The shell as the board sees it: views and follows are recorded, marshalled calls run at once."""

    def __init__(self) -> None:
        self.views: list[TaskView] = []
        self.follows: list[tuple[str, str]] = []

    def marshal(self, fn: Callable[..., object], *args: object) -> None:
        _ = fn(*args)

    def show_tasks(self, view: TaskView) -> None:
        self.views.append(view)

    def follow_run(self, previous: str, current: str) -> None:
        self.follows.append((previous, current))


@dataclass
class Script:
    """What a fake child does: the lines it prints, how it ends, and an event it waits on before ending."""

    lines: Sequence[str] = ()
    outcome: TaskDone | TaskFailed | None = field(default_factory=TaskDone)
    code: int = 0
    hold: asyncio.Event | None = None


@dataclass
class FakeProcess:
    """A child process that prints its script's lines and writes its outcome file."""

    script: Script
    folder: Path
    pid: int = 0

    async def lines(self) -> AsyncIterator[str]:
        for line in self.script.lines:
            yield line

        if self.script.hold is not None:
            _ = await self.script.hold.wait()

    async def wait(self) -> int:
        if self.script.outcome is not None:
            _ = (self.folder / OUTCOME_FILE).write_text(self.script.outcome.model_dump_json(), encoding="utf-8")

        return self.script.code

    def wait_for(self, timeout: float) -> bool:
        return True

    def interrupt(self) -> bool:
        return True

    def kill(self) -> None:
        pass

    def close(self) -> None:
        pass


class Spawner:
    """Starts a fake child per job, recording the labels in spawn order."""

    def __init__(self, scripts: Callable[[str, int], Script]) -> None:
        self.scripts = scripts
        self.spawned: list[str] = []

    async def __call__(self, job: TaskJob, folder: Path) -> TaskProcess:
        self.spawned.append(job.label)

        return FakeProcess(self.scripts(job.label, self.spawned.count(job.label)), folder)


async def _until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + TIMEOUT
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met")

        await asyncio.sleep(0.02)


def _plan(*labels: str) -> list[Task]:
    return [Task(label, solve_all) for label in labels]


def _idle(board: TaskBoard) -> bool:
    return not board.queue and board.running is None


@pytest.mark.parametrize("stage", list(Stage))
def test_every_stage_maps_to_a_phase(stage: Stage) -> None:
    assert TaskPhase.of(stage) is EXPECTED_PHASES[stage]


def test_a_plan_labels_named_functions_and_refuses_duplicate_empty_or_unnamed_entries() -> None:
    plan = plan_of([Task("Export", solve_all), solve_all])

    assert [task.label for task in plan] == ["Export", "Solve All"]
    with pytest.raises(ValueError, match="'Export'"):
        _ = plan_of([Task("Export", solve_all), Task("Export", solve_all)])
    with pytest.raises(ValueError, match="no task"):
        _ = plan_of([])
    with pytest.raises(ValueError, match="empty"):
        _ = plan_of([Task(" ", solve_all)])
    with pytest.raises(TypeError, match="label"):
        _ = plan_of([partial(solve_all)])
    with pytest.raises(TypeError, match="label"):
        _ = plan_of([lambda: None])
    with pytest.raises(TypeError, match="label"):
        _ = plan_of([3])
    with pytest.raises(TypeError, match="str"):
        _ = plan_of("Export")


def test_task_field_names_every_row_field() -> None:
    assert set(TaskField) == set(TaskRow.model_fields) | set(TaskRow.model_computed_fields)


def test_records_move_the_row() -> None:
    row = TaskRow(label="a", title="A")

    started = observe(row, _record(run_id="r1"))
    meshing = observe(started, _record(run_id="r1", stage="mesh"))
    solving = observe(meshing, _record(run_id="r1", stage="run_solver"))
    progressed = observe(solving, _record(run_id="r1", progress=0.42, solver="linear elasticity"))
    bumped = observe(progressed, _record(run_id="r1", progress=0.3))
    finishing = observe(bumped, _record(run_id="r1", stage="cleanup"))

    assert (started.phase, started.run_id, started.runs) == (TaskPhase.STARTING, "r1", 1)
    assert meshing.phase is TaskPhase.MESHING and meshing.progress is None and meshing.busy
    assert progressed.phase is TaskPhase.SOLVING and progressed.progress == 0.42 and progressed.percent == "42%"
    assert progressed.state == "Solving · Linear Elasticity"
    assert bumped.progress == 0.42, "the bar never moves back within a stage"
    assert finishing.phase is TaskPhase.FINISHING and finishing.progress is None
    assert observe(row, _record(stage="not a stage")) is row
    assert observe(meshing, _record(run_id="r1", progress=0.5)).progress is None, "a percent outside the solve is no solve progress"


def test_a_reuse_and_a_solve_are_counted_by_their_records() -> None:
    row = TaskRow(label="a", title="A")

    reused = observe(observe(row, _record(run_id="r1", origin="local")), _record(run_id="r2", run_dir="runs/r2"))

    assert (reused.runs, reused.reuses, reused.solves) == (2, 1, 1)


def test_the_strip_copy_names_the_queue_place_the_runs_and_the_failure() -> None:
    queued = TaskRow(label="a", title="A", position=2)
    done = TaskRow(label="a", title="A", phase=TaskPhase.DONE, runs=2)
    failed = TaskRow(label="a", title="A", phase=TaskPhase.FAILED, error="RuntimeError: boom")

    assert queued.state == "Queued · 2nd In Line" and TaskRow(label="a", title="A", position=1).state == "Queued"
    assert done.state == "Done · 2 Runs" and done.glyph == "●" and done.retryable and not done.cancellable
    assert failed.state == "Failed · RuntimeError: boom" and failed.failed and failed.glyph == "⊘"
    assert TaskRow(label="a", title="A", phase=TaskPhase.STOPPED).state == "Cancelled"
    assert TaskRow(label="a", title="A", phase=TaskPhase.GEOMETRY).state == "Preparing Geometry"
    assert [ordinal(number) for number in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23)] == ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd"]
    assert [clock(seconds) for seconds in (None, 0.3, 12.0, 72.0, 2463.0, 3720.0)] == ["—", "0.3s", "12s", "1m 12s", "41m 03s", "1h 02m"]


def test_the_queue_runs_in_plan_order_and_a_save_reruns_after_the_running_task(tmp_path: Path) -> None:
    host = Host()
    release = asyncio.Event()

    def scripts(label: str, nth: int) -> Script:
        return Script(hold=release if (label, nth) == ("b", 1) else None)

    spawner = Spawner(scripts)

    async def scenario() -> tuple[bool, bool, list[str]]:
        board = TaskBoard(host, "main:tasks", _plan("a", "b", "c"), path=None, roots=[tmp_path], spawn=spawner)
        board.start()
        await _until(lambda: spawner.spawned == ["a", "b"])
        board.submit(_plan("a", "b", "c"))
        again = board.rows["b"].again
        queued = [row.state for row in board.view().rows]
        release.set()
        await _until(lambda: _idle(board) and len(spawner.spawned) == 5)
        settled = board.rows["b"].again
        board.shutdown()

        return again, settled, queued

    again, settled, queued = asyncio.run(scenario())

    assert spawner.spawned == ["a", "b", "a", "b", "c"]
    assert again and not settled, "b runs again after its first run ends"
    assert queued == ["Queued", "Starting", "Queued · 3rd In Line"]
    assert host.views[-1].summary == "3 Tasks · 3 Done" and host.views[-1].focus is None


def test_a_failed_task_never_stops_the_queue(tmp_path: Path) -> None:
    host = Host()
    boom = TaskFailed(error="RuntimeError", message="boom\nsecond line", traceback="Traceback ...\nRuntimeError: boom")

    def scripts(label: str, nth: int) -> Script:
        return Script(outcome=boom) if label == "a" else Script()

    spawner = Spawner(scripts)

    async def scenario() -> TaskBoard:
        board = TaskBoard(host, "main:tasks", _plan("a", "b", "c"), path=None, roots=[tmp_path], spawn=spawner)
        board.start()
        await _until(lambda: _idle(board) and len(spawner.spawned) == 3)
        board.shutdown()

        return board

    board = asyncio.run(scenario())

    assert [row.phase for row in board.rows.values()] == [TaskPhase.FAILED, TaskPhase.DONE, TaskPhase.DONE]
    assert board.rows["a"].error == "RuntimeError: boom" and board.rows["a"].traceback is not None
    assert host.views[-1].summary == "3 Tasks · 2 Done · 1 Failed" and host.views[-1].failed == 1
    assert host.views[-1].focus is not None and host.views[-1].focus.label == "a", "the last failure leads an idle strip"


def test_a_child_that_dies_without_an_outcome_shows_its_exit_code_and_last_lines(tmp_path: Path) -> None:
    host = Host()
    spawner = Spawner(lambda label, nth: Script(lines=["Traceback (most recent call last):\n", "MemoryError\n"], outcome=None, code=3))

    async def scenario() -> TaskBoard:
        board = TaskBoard(host, "main:tasks", _plan("a"), path=None, roots=[tmp_path], spawn=spawner)
        board.start()
        await _until(lambda: _idle(board) and board.rows["a"].phase.ended)
        board.shutdown()

        return board

    row = asyncio.run(scenario()).rows["a"]

    assert row.phase is TaskPhase.FAILED and row.error == "Task Process Exited (3)"
    assert row.traceback == "Traceback (most recent call last):\nMemoryError"


def test_a_task_dropped_by_a_save_finishes_then_leaves(tmp_path: Path) -> None:
    host = Host()
    release = asyncio.Event()
    spawner = Spawner(lambda label, nth: Script(hold=release if label == "b" else None))

    async def scenario() -> tuple[list[str], str, list[str]]:
        board = TaskBoard(host, "main:tasks", _plan("a", "b", "c"), path=None, roots=[tmp_path], spawn=spawner)
        board.start()
        await _until(lambda: spawner.spawned == ["a", "b"])
        board.submit(_plan("a", "c"))
        queue = list(board.queue)
        note = board.rows["b"].note
        release.set()
        await _until(lambda: _idle(board) and len(spawner.spawned) == 4)
        board.shutdown()

        return queue, note, list(board.rows)

    queue, note, rows = asyncio.run(scenario())

    assert queue == ["a", "c"] and note == "Removed From Script, Finishing"
    assert rows == ["a", "c"] and spawner.spawned == ["a", "b", "a", "c"]


def test_a_newer_run_of_a_task_is_followed_and_a_reused_one_reads_reused(tmp_path: Path) -> None:
    host = Host()

    def scripts(label: str, nth: int) -> Script:
        solved = [f'{{"level": "INFO", "message": "m", "name": "n", "function": "f", "line": 1, "extra": {{"run_id": "r{nth}", "run_dir": "runs/r{nth}"}}}}\n']
        reused = ['{"level": "INFO", "message": "Reusing bundle.", "name": "n", "function": "f", "line": 1, "extra": {"run_id": "r2", "origin": "local"}}\n']

        return Script(lines=reused if nth == 3 else solved)

    spawner = Spawner(scripts)

    async def scenario() -> TaskBoard:
        board = TaskBoard(host, "main:tasks", _plan("a"), path=None, roots=[tmp_path], spawn=spawner)
        board.start()
        for runs in (1, 2, 3):
            await _until(lambda runs=runs: _idle(board) and len(spawner.spawned) == runs)
            if runs < 3:
                board.retry("a")

        board.shutdown()

        return board

    board = asyncio.run(scenario())

    assert host.follows == [("r1", "r2")], "a reuse of the run shown has nothing newer to follow"
    assert board.rows["a"].phase is TaskPhase.REUSED and board.rows["a"].state == "Reused"


def test_cancel_takes_a_queued_task_out_and_stops_the_running_one(tmp_path: Path) -> None:
    host = Host()
    release = asyncio.Event()
    spawner = Spawner(lambda label, nth: Script(hold=release if label == "a" else None, outcome=None, code=1))

    async def scenario() -> TaskBoard:
        board = TaskBoard(host, "main:tasks", _plan("a", "b"), path=None, roots=[tmp_path], spawn=spawner)
        board.start()
        await _until(lambda: spawner.spawned == ["a"])
        board.cancel("b")
        board.cancel("a")
        await asyncio.sleep(0)
        release.set()
        await _until(lambda: _idle(board) and board.rows["a"].phase.ended)
        board.shutdown()

        return board

    board = asyncio.run(scenario())

    assert spawner.spawned == ["a"]
    assert [row.phase for row in board.rows.values()] == [TaskPhase.STOPPED, TaskPhase.STOPPED]


def test_a_save_that_lists_no_tasks_keeps_the_rows_and_says_why(tmp_path: Path) -> None:
    host = Host()
    board = TaskBoard(host, "main:tasks", _plan("a"), path=None, roots=[tmp_path], spawn=Spawner(lambda label, nth: Script()))

    board.apply("not a plan", 0.1)
    kept = list(board.rows)
    board.apply(_plan("a", "b"), 0.1)

    assert kept == ["a"] and host.views[-2].error == "TypeError: tasks must list Task entries, not a str"
    assert list(board.rows) == ["a", "b"] and host.views[-1].error is None


@pytest.fixture
def child_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Children import this checkout's femforge and workbench, without the coverage hooks of this process."""
    sources = [Path(femforge.__file__).parents[1], Path(femforge_workbench.__file__).parents[1]]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(str(source) for source in sources))
    for name in [name for name in os.environ if name.startswith(("COV_CORE_", "COVERAGE_"))]:
        monkeypatch.delenv(name)


HOLDER = """
import signal
import subprocess
import sys
import time
from pathlib import Path

from femforge.artifacts.lock import DirLock
from femforge.common.log import log
from femforge_workbench.tasks import Task

RUNS = Path({runs!r})


def hold():
    if {deaf!r}:
        signal.signal(signal.SIGBREAK if sys.platform == "win32" else signal.SIGTERM, signal.SIG_IGN)

    lock = DirLock(RUNS / "r1.lock")
    assert lock.try_acquire()
    try:
        log.bind(run_id="r1", run_dir=str(RUNS / "r1")).info("Beginning evaluation.")
        if {deaf!r}:
            while True:
                time.sleep(0.1)

        subprocess.run([sys.executable, "-c", "import time; time.sleep(60)"], check=False)
    finally:
        lock.release()


def tasks():
    return [Task("Hold", hold)]
"""
"""A task holding a run's claim: a cooperative one waits on a sleeping grandchild, a deaf one ignores the break."""


def _stop_a_holder(tmp_path: Path, *, deaf: bool, grace: float) -> tuple[float, TaskBoard, Path]:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "r1").mkdir()
    _ = (tmp_path / "holder.py").write_text(HOLDER.format(runs=str(runs), deaf=deaf), encoding="utf-8")
    lock = runs / "r1.lock"

    async def scenario() -> tuple[float, TaskBoard]:
        board = TaskBoard(Host(), "holder:tasks", [Task("Hold", solve_all)], path=str(tmp_path), roots=[tmp_path])
        board.start()
        await _until(lambda: lock.is_dir() and board.running is not None and board.running.run_dir is not None)
        started = time.monotonic()
        board.shutdown(grace)

        return time.monotonic() - started, board

    elapsed, board = asyncio.run(scenario())

    return elapsed, board, lock


@pytest.mark.usefixtures("child_env")
def test_shutdown_lets_a_cooperative_child_release_its_claim(tmp_path: Path) -> None:
    elapsed, board, lock = _stop_a_holder(tmp_path, deaf=False, grace=10.0)

    assert not lock.exists(), "the child's own cleanup released the claim"
    assert elapsed < 2.0
    assert board.rows["Hold"].phase is TaskPhase.STOPPED


@pytest.mark.usefixtures("child_env")
def test_shutdown_kills_a_deaf_child_and_reaps_its_claim(tmp_path: Path) -> None:
    elapsed, board, lock = _stop_a_holder(tmp_path, deaf=True, grace=0.5)

    assert not lock.exists(), "the reap broke the claim the killed child left"
    assert elapsed < 3.0
    assert board.rows["Hold"].phase is TaskPhase.STOPPED
