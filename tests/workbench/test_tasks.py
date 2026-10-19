"""Background tasks: the plan, the phases, the rows records move, the serial queue across saves, failures, and the stop sequence on real child processes."""

import asyncio
import importlib
import json
import os
import sys
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import cast

import pytest

import femforge
import femforge_workbench
from femforge.common.log import RelayedRecord
from femforge.run.stages import Stage
from femforge_workbench.registry import import_root, target_of
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
    break_until_exit,
    clock,
    observe,
    ordinal,
    plan_of,
    shown_label,
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
    """A task function that does nothing."""


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


def _line(**extra: object) -> str:
    return json.dumps(_record(**extra)) + "\n"


def _plan(*labels: str) -> list[Task]:
    return [Task(label, solve_all) for label in labels]


def _idle(board: TaskBoard) -> bool:
    return not board.queue and board.running is None


@pytest.mark.parametrize("stage", list(Stage))
def test_every_stage_maps_to_a_phase(stage: Stage) -> None:
    assert TaskPhase.of(stage) is EXPECTED_PHASES[stage]


def test_a_plan_refuses_duplicate_empty_or_foreign_entries() -> None:
    plan = plan_of([Task("Export", solve_all), Task("shell comparison", solve_all)])

    assert [task.label for task in plan] == ["Export", "shell comparison"]
    assert [shown_label(label) for label in ("Gap 0.5 mm", "shell comparison")] == ["Gap 0.5 mm", "Shell Comparison"], "a label with a capital is shown as written"
    with pytest.raises(ValueError, match="'Export'"):
        _ = plan_of([Task("Export", solve_all), Task("Export", solve_all)])
    with pytest.raises(ValueError, match="no task"):
        _ = plan_of([])
    with pytest.raises(ValueError, match="empty"):
        _ = plan_of([Task(" ", solve_all)])
    with pytest.raises(TypeError, match="tuple"):
        _ = plan_of((Task("Export", solve_all),))
    with pytest.raises(TypeError, match="function"):
        _ = plan_of([solve_all])
    with pytest.raises(TypeError, match="str"):
        _ = plan_of("Export")


def test_a_named_function_labels_its_own_task_and_a_partial_or_lambda_needs_one() -> None:
    assert Task(solve_all) == Task("Solve All", solve_all) == Task(run=solve_all)
    assert Task(label="Export", run=solve_all).label == "Export"
    with pytest.raises(TypeError, match="needs a label"):
        _ = Task(partial(print, "x"))
    with pytest.raises(TypeError, match="needs a label"):
        _ = Task(lambda: None)
    with pytest.raises(TypeError, match="function to run"):
        _ = cast("Callable[..., Task]", Task)(label="Export")


def test_task_field_names_every_row_field() -> None:
    assert set(TaskField) == set(TaskRow.model_fields) | set(TaskRow.model_computed_fields)


def test_records_move_the_row() -> None:
    row = TaskRow(label="a", title="A")

    started = observe(row, _record(run_id="r1"))
    meshing = observe(started, _record(run_id="r1", stage="mesh"))
    solving = observe(meshing, _record(run_id="r1", stage="run_solver"))
    progressed = observe(solving, _record(run_id="r1", progress=0.42, solver="linear elasticity"))
    bumped = observe(progressed, _record(run_id="r1", progress=0.3))
    beat = observe(bumped, _record(run_id="r1", iteration=412, max_iterations=5000))
    finishing = observe(beat, _record(run_id="r1", stage="cleanup"))

    assert (started.phase, started.run_id, started.runs) == (TaskPhase.STARTING, "r1", 1)
    assert meshing.phase is TaskPhase.MESHING and meshing.progress is None and meshing.busy
    assert progressed.phase is TaskPhase.SOLVING and progressed.progress == 0.42 and progressed.percent == "42%"
    assert progressed.state == "Solving · Linear Elasticity"
    assert bumped.progress == 0.42, "the bar never moves back within one solver's solve"
    assert beat.iterations == "It 412/5000" and observe(solving, _record(run_id="r1", iteration=7)).iterations == "It 7"
    assert finishing.phase is TaskPhase.FINISHING and finishing.progress is None and finishing.iterations == ""
    assert observe(row, _record(stage="not a stage")) is row
    assert observe(meshing, _record(run_id="r1", progress=0.5)).progress is None, "a percent outside the solve is no solve progress"


def test_a_claim_wait_and_a_stall_move_the_row() -> None:
    row = TaskRow(label="a", title="A")

    waiting = observe(row, _record(run_id="r1", holder=4242))
    solving = observe(waiting, _record(run_id="r1", stage="run_solver"))
    flat = observe(solving, _record(stall="repeated", solver="linear elasticity", iteration=1339, residual=3.14e-8))
    slow = observe(solving, _record(stall="no_progress"))

    assert (waiting.phase, waiting.state, waiting.glyph, waiting.active) == (TaskPhase.WAITING, "Waiting · Another Process Is Solving It", "◐", True)
    assert observe(waiting, _record(run_id="r1", run_dir="runs/r1")).phase is TaskPhase.STARTING, "a claim taken after the wait starts the run"
    assert flat.stall == "residual flat at 3.14e-08 since it 1339" and slow.stall == "residual not improving", "a reason is data, not a label"
    assert observe(solving, _record(stall="not a reason")) is solving


def test_a_reuse_and_a_solve_are_counted_by_their_records() -> None:
    row = TaskRow(label="a", title="A")

    reused = observe(observe(row, _record(run_id="r1", origin="local")), _record(run_id="r2", run_dir="runs/r2"))

    assert (reused.runs, reused.reuses, reused.solves) == (2, 1, 1)


def test_the_bar_restarts_for_each_solver_and_a_stack_of_several_names_the_solver_place() -> None:
    solving = observe(TaskRow(label="a", title="A"), _record(run_id="r1", stage="run_solver"))
    stacked = observe(solving, _record(linear_solvers=["heat equation", "linear elasticity", "stress"], direct_solvers=[]))

    first = observe(stacked, _record(progress=0.9, solver="heat equation", iteration=400))
    second = observe(first, _record(progress=0.1, solver="linear elasticity"))
    single = observe(solving, _record(linear_solvers=["linear elasticity"], direct_solvers=[]))

    assert (first.percent, first.state) == ("90%", "Solving · Heat Equation 1/3")
    assert (second.progress, second.percent, second.iteration) == (0.1, "10%", None), "the next solver's percent, not the best of the last"
    assert second.state == "Solving · Linear Elasticity 2/3"
    assert observe(single, _record(solver="linear elasticity")).state == "Solving · Linear Elasticity", "one linear solver takes no place"
    assert solving.state == "Solving" and observe(solving, _record(linear_solvers="heat", direct_solvers=[])) is solving


def test_a_direct_solve_reads_direct_with_the_pulse_and_no_percent() -> None:
    solving = observe(TaskRow(label="a", title="A"), _record(run_id="r1", stage="run_solver"))
    direct = observe(solving, _record(linear_solvers=["linear elasticity"], direct_solvers=["linear elasticity"]))
    converged = observe(direct, _record(progress=0.5, solver="linear elasticity"))
    mixed = observe(solving, _record(linear_solvers=["heat equation", "linear elasticity"], direct_solvers=["heat equation"]))

    assert (direct.state, direct.percent, direct.busy) == ("Solving · Linear Elasticity Direct", "", True), "the stack's first solver solves before any record names it"
    assert (converged.state, converged.percent, converged.busy) == ("Solving · Linear Elasticity Direct", "", True)
    assert mixed.state == "Solving · Heat Equation 1/2 Direct"
    assert observe(mixed, _record(progress=0.3, solver="linear elasticity")).percent == "30%"


def test_a_cached_mesh_a_fetched_bundle_and_the_task_code_after_a_run_read_in_the_strip() -> None:
    row = TaskRow(label="a", title="A")

    meshing = observe(observe(row, _record(run_id="r1")), _record(stage="mesh"))
    cached = observe(meshing, _record(origin="local", cached="elmer_mesh"))
    fetched = observe(row, _record(run_id="r2", origin="local", cached="run_bundle"))
    finishing = observe(cached, _record(stage="cleanup", ended=False))
    running = observe(cached, _record(stage="cleanup", ended=True))

    assert (meshing.state, cached.state, cached.reuses) == ("Meshing", "Meshing · Cached", 0), "a cached mesh is no reused run"
    assert (fetched.phase, fetched.state, fetched.reuses, fetched.fetched) == (TaskPhase.RUNNING, "Running", 1, True), "the task's own code runs after a reuse"
    assert fetched.model_copy(update={"phase": TaskPhase.REUSED}).state == "Reused · From Cache"
    assert TaskRow(label="a", title="A", phase=TaskPhase.REUSED, reuses=1).state == "Reused"
    assert finishing.phase is TaskPhase.FINISHING and (running.phase, running.state, running.active, running.busy) == (TaskPhase.RUNNING, "Running", True, True)
    assert observe(running, _record(run_id="r3")).mesh_cached is False, "a new run starts without the last one's mesh"


def test_the_strip_copy_names_the_queue_place_the_runs_and_the_failure() -> None:
    queued = TaskRow(label="a", title="A", position=2)
    done = TaskRow(label="a", title="A", phase=TaskPhase.DONE, runs=2)
    failed = TaskRow(label="a", title="A", phase=TaskPhase.FAILED, error="RuntimeError: boom")

    assert queued.state == "Queued · 2nd In Line" and TaskRow(label="a", title="A", position=1).state == "Queued"
    assert done.state == "Done · 2 Runs" and done.glyph == "●" and not done.retryable and not done.cancellable
    assert failed.state == "Failed · RuntimeError: boom" and failed.failed and failed.glyph == "⊘" and failed.retryable
    assert TaskRow(label="a", title="A", phase=TaskPhase.STOPPED).state == "Cancelled" and TaskRow(label="a", title="A", phase=TaskPhase.STOPPED).retryable
    assert not TaskRow(label="a", title="A", phase=TaskPhase.REUSED).retryable, "Done and Reused rows offer Open only"
    assert TaskRow(label="a", title="A", phase=TaskPhase.GEOMETRY).state == "Preparing Geometry"
    assert [ordinal(number) for number in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23)] == ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd"]
    assert [clock(seconds) for seconds in (None, 0.3, 9.94, 9.96, 12.0, 72.0, 2463.0, 3720.0)] == ["—", "0.3s", "9.9s", "10s", "12s", "1m 12s", "41m 03s", "1h 02m"]
    stalled = TaskRow(label="a", title="A", phase=TaskPhase.STALLED, stall="residual flat at 3.14e-08 since it 1339")
    assert (stalled.state, stalled.glyph, stalled.failed, stalled.stalled, stalled.retryable) == ("Stalled · residual flat at 3.14e-08 since it 1339", "⧖", True, True, True)
    cancelling = TaskRow(label="a", title="A", phase=TaskPhase.SOLVING, solver="linear elasticity", stopping=True)
    assert (cancelling.state, cancelling.cancellable, cancelling.active) == ("Cancelling", False, True), "a confirmed Cancel shows at once and cannot be pressed twice"


def test_the_queue_runs_in_plan_order_and_a_save_reruns_after_the_running_task(tmp_path: Path) -> None:
    host = Host()
    release = asyncio.Event()

    def scripts(label: str, nth: int) -> Script:
        return Script(lines=[_line(run_id=f"{label}{nth}")], hold=release if (label, nth) == ("b", 1) else None)

    spawner = Spawner(scripts)

    async def scenario() -> tuple[bool, bool, list[str], tuple[str | None, str]]:
        board = TaskBoard(host, "main:tasks", _plan("a", "b", "c"), path=None, roots=[tmp_path], spawn=spawner)
        board.start()
        await _until(lambda: spawner.spawned == ["a", "b"])
        board.submit(_plan("a", "b", "c"))
        again = board.by_label["b"].again
        queued = [row.state for row in board.view().rows]
        requeued = (board.by_label["a"].run_id, board.by_label["a"].clock)
        release.set()
        await _until(lambda: _idle(board) and len(spawner.spawned) == 5)
        settled = board.by_label["b"].again
        board.shutdown()

        return again, settled, queued, requeued

    again, settled, queued, requeued = asyncio.run(scenario())

    assert spawner.spawned == ["a", "b", "a", "b", "c"]
    assert again and not settled, "b runs again after its first run ends"
    assert queued == ["Queued", "Starting", "Queued · 3rd In Line"]
    assert requeued == (None, "—"), "a queued row shows no run and no clock until it starts again"
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

    assert [row.phase for row in board.by_label.values()] == [TaskPhase.FAILED, TaskPhase.DONE, TaskPhase.DONE]
    assert board.by_label["a"].error == "RuntimeError: boom" and board.by_label["a"].traceback is not None
    assert host.views[-1].summary == "3 Tasks · 2 Done · 1 Failed" and host.views[-1].failed == 1
    assert host.views[-1].focus is not None and host.views[-1].focus.label == "a", "the last failure leads an idle strip"


def test_the_summary_names_every_ending_once_and_a_busy_one_its_failures() -> None:
    board = TaskBoard(Host(), "main:tasks", _plan("a", "b", "c", "d"), path=None, roots=[])
    started = board.view().submitted
    board.queue = []
    for label, phase in zip("abcd", (TaskPhase.DONE, TaskPhase.REUSED, TaskPhase.FAILED, TaskPhase.STOPPED), strict=True):
        board.by_label[label] = board.by_label[label].model_copy(update={TaskField.PHASE: phase})

    idle = board.view()
    board.retry("d")
    busy = board.view()
    board.by_label["c"] = board.by_label["c"].model_copy(update={TaskField.PHASE: TaskPhase.STALLED})
    stalled = board.view()
    board.submit(_plan("a", "b", "c", "d"))

    assert idle.summary == "4 Tasks · 1 Done · 1 Reused · 1 Failed · 1 Cancelled" and not idle.busy
    assert (idle.reused, idle.failed, idle.stalled) == (1, 1, 0)
    assert busy.summary == "2 of 4 Done · 1 Failed" and busy.busy, "a failure during a busy run reaches the summary the live region reads"
    assert stalled.summary == "2 of 4 Done · 1 Stalled"
    assert started.startswith("Submitted at ") and board.view().submitted.startswith("Reloaded at "), "a save reloads the plan"


def test_a_child_that_dies_without_an_outcome_shows_its_exit_code_and_last_lines(tmp_path: Path) -> None:
    host = Host()
    spawner = Spawner(lambda label, nth: Script(lines=["Traceback (most recent call last):\n", "MemoryError\n"], outcome=None, code=3))

    async def scenario() -> TaskBoard:
        board = TaskBoard(host, "main:tasks", _plan("a"), path=None, roots=[tmp_path], spawn=spawner)
        board.start()
        await _until(lambda: _idle(board) and board.by_label["a"].phase.ended)
        board.shutdown()

        return board

    row = asyncio.run(scenario()).by_label["a"]

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
        note = board.by_label["b"].note
        release.set()
        await _until(lambda: _idle(board) and len(spawner.spawned) == 4)
        board.shutdown()

        return queue, note, list(board.by_label)

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
                board.submit(_plan("a"))

        board.shutdown()

        return board

    board = asyncio.run(scenario())

    assert host.follows == [("r1", "r2")], "a reuse of the run shown has nothing newer to follow"
    assert board.by_label["a"].phase is TaskPhase.REUSED and board.by_label["a"].state == "Reused"
    assert host.views[-1].summary == "1 Task · 1 Reused" and host.views[-1].count == "1/1"


def test_a_stalled_run_ends_its_task_stalled_with_its_log_and_the_summary_counts_each_ending(tmp_path: Path) -> None:
    host = Host()
    boom = TaskFailed(error="RuntimeError", message="the solve stalled", traceback="Traceback ...")
    stall = _line(run_id="r1", stall="repeated", iteration=1339, residual=3.14e-8)
    reuse = _line(run_id="r2", origin="local")

    def scripts(label: str, nth: int) -> Script:
        match label:
            case "stalls":
                return Script(lines=[_line(run_id="r1", stage="run_solver"), stall], outcome=boom)
            case "returns":
                return Script(lines=[stall])
            case "reuses":
                return Script(lines=[reuse])
            case "done":
                return Script()
            case _:
                return Script(outcome=boom)

    spawner = Spawner(scripts)

    async def scenario() -> TaskBoard:
        board = TaskBoard(host, "main:tasks", _plan("stalls", "returns", "reuses", "done", "fails"), path=None, roots=[tmp_path], spawn=spawner)
        board.start()
        await _until(lambda: _idle(board) and len(spawner.spawned) == 5)
        board.shutdown()

        return board

    board = asyncio.run(scenario())
    view = host.views[-1]
    stalled = board.by_label["stalls"]

    assert [row.phase for row in board.by_label.values()] == [TaskPhase.STALLED, TaskPhase.STALLED, TaskPhase.REUSED, TaskPhase.DONE, TaskPhase.FAILED]
    assert stalled.state == "Stalled · residual flat at 3.14e-08 since it 1339" and stalled.error is None
    assert stalled.traceback == "m\nm", "a stalled row shows the child's last log messages"
    assert view.summary == "5 Tasks · 1 Done · 1 Reused · 1 Failed · 2 Stalled" and (view.reused, view.failed, view.stalled) == (1, 1, 2)
    assert view.focus is not None and view.focus.label == "fails"


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
        await _until(lambda: _idle(board) and board.by_label["a"].phase.ended)
        board.shutdown()

        return board

    board = asyncio.run(scenario())

    assert spawner.spawned == ["a"]
    assert [row.phase for row in board.by_label.values()] == [TaskPhase.STOPPED, TaskPhase.STOPPED]


def test_a_cancel_that_lands_after_the_task_returned_reads_done(tmp_path: Path) -> None:
    host = Host()
    release = asyncio.Event()
    spawner = Spawner(lambda label, nth: Script(hold=release))

    async def scenario() -> TaskBoard:
        board = TaskBoard(host, "main:tasks", _plan("a"), path=None, roots=[tmp_path], spawn=spawner)
        board.start()
        await _until(lambda: board.running is not None)
        board.cancel("a")
        release.set()
        await _until(lambda: _idle(board) and board.by_label["a"].phase.ended)
        board.shutdown()

        return board

    board = asyncio.run(scenario())

    assert board.by_label["a"].phase is TaskPhase.DONE, "the task returned before the break, so its run stands"


def test_a_confirmed_cancel_reads_cancelling_until_the_child_exits(tmp_path: Path) -> None:
    host = Host()
    release = asyncio.Event()
    spawner = Spawner(lambda label, nth: Script(hold=release, outcome=None, code=1))

    async def scenario() -> tuple[TaskRow, TaskRow]:
        board = TaskBoard(host, "main:tasks", _plan("a"), path=None, roots=[tmp_path], spawn=spawner)
        board.start()
        await _until(lambda: board.running is not None)
        board.cancel("a")
        cancelling = host.views[-1].rows[0]
        release.set()
        await _until(lambda: _idle(board) and board.by_label["a"].phase.ended)
        board.shutdown()

        return cancelling, board.by_label["a"]

    cancelling, ended = asyncio.run(scenario())

    assert (cancelling.state, cancelling.cancellable) == ("Cancelling", False)
    assert (ended.state, ended.stopping) == ("Cancelled", False)


def test_retry_queues_only_a_failed_or_cancelled_task(tmp_path: Path) -> None:
    board = TaskBoard(Host(), "main:tasks", _plan("a", "b", "c"), path=None, roots=[tmp_path], spawn=Spawner(lambda label, nth: Script()))
    board.queue.clear()
    board.by_label["a"] = board.by_label["a"].model_copy(update={"phase": TaskPhase.DONE, "run_id": "r1", "elapsed": 2.0})
    board.by_label["b"] = board.by_label["b"].model_copy(update={"phase": TaskPhase.FAILED, "error": "RuntimeError: boom", "run_id": "r2", "elapsed": 1.0})
    board.by_label["c"] = board.by_label["c"].model_copy(update={"phase": TaskPhase.STOPPED})

    for label in ("a", "b", "c", "b"):
        board.retry(label)

    retried = board.by_label["b"]
    assert board.queue == ["b", "c"] and board.by_label["a"].phase is TaskPhase.DONE
    assert (retried.phase, retried.run_id, retried.clock, retried.error) == (TaskPhase.QUEUED, None, "—", None)


@dataclass
class Deaf:
    """A process that exits only once it saw `needed` breaks, or never; `console` False refuses every break."""

    needed: int | None
    console: bool = True
    breaks: int = 0
    pid: int = 0

    def interrupt(self) -> bool:
        self.breaks += self.console

        return self.console

    def wait_for(self, timeout: float) -> bool:
        time.sleep(timeout)

        return self.needed is not None and self.breaks >= self.needed


def test_the_break_is_sent_again_until_the_process_exits() -> None:
    late = Deaf(needed=3)
    deaf = Deaf(needed=None)
    alone = Deaf(needed=1, console=False)

    assert break_until_exit(cast("TaskProcess", late), 5.0) and late.breaks == 3, "a process that missed the first breaks exits on a later one"
    assert not break_until_exit(cast("TaskProcess", deaf), 1.2) and deaf.breaks == 3
    assert not break_until_exit(cast("TaskProcess", alone), 5.0) and alone.breaks == 0, "without a console the stop goes straight to the kill"


def test_a_save_that_lists_no_tasks_keeps_the_rows_and_says_why(tmp_path: Path) -> None:
    host = Host()
    board = TaskBoard(host, "main:tasks", _plan("a"), path=None, roots=[tmp_path], spawn=Spawner(lambda label, nth: Script()))

    board.apply("not a plan", 0.1)
    kept = list(board.by_label)
    board.apply(_plan("a", "b"), 0.1)

    assert kept == ["a"] and host.views[-2].error == "TypeError: tasks must be a list of Task, not a str"
    assert list(board.by_label) == ["a", "b"] and host.views[-1].error is None


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
    assert board.by_label["Hold"].phase is TaskPhase.STOPPED


@pytest.mark.usefixtures("child_env")
def test_shutdown_kills_a_deaf_child_and_reaps_its_claim(tmp_path: Path) -> None:
    elapsed, board, lock = _stop_a_holder(tmp_path, deaf=True, grace=0.5)

    assert not lock.exists(), "the reap broke the claim the killed child left"
    assert elapsed < 3.0
    assert board.by_label["Hold"].phase is TaskPhase.STOPPED


PREPENDING_PLAN = """
import sys

sys.path.insert(0, {other!r})

import prepended_helper
import served_helper
from femforge_workbench.tasks import Task


def use():
    assert prepended_helper.VALUE + served_helper.VALUE == 7


def solves():
    return [Task("Use", use)]
"""
"""A plan whose import prepends another directory to sys.path and needs a module only the server's own path finds."""


@pytest.mark.usefixtures("child_env")
def test_a_plan_that_prepends_to_sys_path_still_imports_its_own_module_in_the_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script, other, shared = (tmp_path / name for name in ("script", "other", "shared"))
    for folder in (script, other, shared):
        folder.mkdir()

    _ = (script / "prepending_plan.py").write_text(PREPENDING_PLAN.format(other=str(other)), encoding="utf-8")
    _ = (other / "prepended_helper.py").write_text("VALUE = 3\n", encoding="utf-8")
    _ = (shared / "served_helper.py").write_text("VALUE = 4\n", encoding="utf-8")
    monkeypatch.setattr(sys, "path", [str(script), str(shared), *sys.path])
    try:
        module = importlib.import_module("prepending_plan")
        factory = cast("Callable[[], list[Task]]", module.solves)
        prepended = sys.path[0]
        root = import_root(factory)

        async def scenario() -> TaskBoard:
            board = TaskBoard(Host(), target_of(factory), plan_of(factory()), path=str(root), roots=[tmp_path])
            board.start()
            await _until(lambda: board.by_label["Use"].phase.ended)
            board.shutdown()

            return board

        board = asyncio.run(scenario())
    finally:
        for name in ("prepending_plan", "prepended_helper", "served_helper"):
            _ = sys.modules.pop(name, None)

    assert prepended == str(other), "the plan's import put another directory first"
    assert root == script.resolve(), "the child imports from the plan's own directory, not sys.path[0]"
    assert board.by_label["Use"].phase is TaskPhase.DONE, board.by_label["Use"].traceback
