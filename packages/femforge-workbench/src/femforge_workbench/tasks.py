"""Background tasks: a script's labelled callables, run one at a time in child processes while the workbench serves, each shown as a row of the task strip."""

import asyncio
import functools
import os
import shutil
import sys
import tempfile
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast, overload

import psutil
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, computed_field

from femforge.common.log import RELAYED_ROUTE, TELEMETRY_ROUTE, LogKey, RelayedRecord, emit_relayed, get_console, log, parse_relayed
from femforge.common.utils import title
from femforge.elmer.solver_log import StallReason
from femforge.run.stages import Stage
from femforge_workbench.process_tree import CREATION_FLAGS, NEW_GROUP, NEW_SESSION, ProcessTree
from femforge_workbench.reload import Reloader
from femforge_workbench.runner import LINE_LIMIT, STREAM_ENV


STOP_GRACE_S = 10.0
"""`t_grace` `[s]`: How long a stopped task gets to release its run before its process tree is killed."""

PUBLISH_PERIOD_S = 1.0
"""`t_publish` `[s]`: The shortest gap between two pushes of the task rows to the client."""

KILL_WAIT_S = 5.0
"""`t_kill` `[s]`: How long a killed task process gets to vanish before its claims are reaped."""

BREAK_REPEAT_S = 0.5
"""`t_break` `[s]`: How often a stopping task's process group gets the break again; a process started after a break never saw it."""

WORKER_MODULE = "femforge_workbench.task_worker"
"""The module a task's child process runs."""

JOB_FILE = "job.json"
OUTCOME_FILE = "outcome.json"
LOG_TAIL = 12
"""How many of a task process's last plain output lines a failure without an outcome shows, and of its last log messages a stall shows."""


@dataclass(frozen=True, slots=True, init=False)
class Task:
    """One background task: a label unique in its plan and the zero-argument callable a child process runs."""

    label: str
    """The name the task strip shows, unique in its plan; the child process finds the task by it."""

    run: Callable[[], object]
    """What the child process calls; its return value is dropped."""

    @overload
    def __init__(self, label: str, run: Callable[[], object]) -> None: ...

    @overload
    def __init__(self, label: Callable[[], object], /) -> None: ...

    @overload
    def __init__(self, *, run: Callable[[], object]) -> None: ...

    def __init__(self, label: str | Callable[[], object] | None = None, run: Callable[[], object] | None = None) -> None:
        """A task of `run` under `label`, or of a named function alone under its name in Title Case; TypeError for a partial or a lambda without a label."""
        if callable(label):
            run, label = label, None

        if run is None:
            raise TypeError("a task needs a function to run")

        if label is None:
            name = getattr(run, "__name__", None)
            if not isinstance(name, str) or not name.isidentifier():
                raise TypeError(f"a task of {run!r} needs a label: Task(label, run)")

            label = title(name)

        object.__setattr__(self, "label", label)
        object.__setattr__(self, "run", run)


type TaskFactory = Callable[[], list[Task]]
"""A module-level function listing a script's tasks; called again after every save and in every task's child process."""


def shown_label(label: str) -> str:
    """A label as the strip shows it: as written when it has a capital, else in Title Case."""
    if any(char.isupper() for char in label):
        return label

    return title(label)


def plan_of(tasks: object) -> tuple[Task, ...]:
    """The tasks a factory listed; TypeError for anything but a list of Task, ValueError for an empty plan, an empty label or a label used twice."""
    if not isinstance(tasks, list):
        raise TypeError(f"tasks must be a list of Task, not a {type(tasks).__name__}")

    entries = cast("list[object]", tasks)
    foreign = [entry for entry in entries if not isinstance(entry, Task)]
    if foreign:
        raise TypeError(f"tasks must be a list of Task; {foreign[0]!r} is a {type(foreign[0]).__name__}")

    plan = tuple(cast("list[Task]", entries))
    if not plan:
        raise ValueError("tasks lists no task")

    labels = [task.label for task in plan]
    if not all(label.strip() for label in labels):
        raise ValueError("a task label is empty")

    repeated = sorted({label for label in labels if labels.count(label) > 1})
    if repeated:
        raise ValueError(f"task labels repeat: {repeated}")

    return plan


class TaskPhase(StrEnum):
    """Where a task stands: queued, one of the steps of its run, or how it ended."""

    QUEUED = "queued"
    STARTING = "starting"
    GEOMETRY = "geometry"
    WAITING = "waiting"
    """Waits for another process that holds the claim on the same run."""
    MESHING = "meshing"
    SOLVING = "solving"
    FINISHING = "finishing"
    DONE = "done"
    REUSED = "reused"
    """Done, every evaluation answered by a finished bundle."""
    FAILED = "failed"
    STALLED = "stalled"
    """Ended on a solve the watchdog stopped."""
    STOPPED = "stopped"

    @classmethod
    def of(cls, stage: Stage) -> "TaskPhase":
        """The phase a Driver stage puts its task in."""
        match stage:
            case Stage.PREPARE_GEOMETRY:
                return cls.GEOMETRY
            case Stage.PRE_MESH | Stage.MESH | Stage.POST_MESH:
                return cls.MESHING
            case Stage.BUILD_SIM | Stage.CONFIGURE_SIM | Stage.WRITE_SIF | Stage.RUN_SOLVER:
                return cls.SOLVING
            case Stage.MESH_STATS | Stage.PUBLISH | Stage.CLEANUP:
                return cls.FINISHING

    @property
    def word(self) -> str:
        """The phase as the task strip names it."""
        match self:
            case TaskPhase.GEOMETRY:
                return "Preparing Geometry"
            case TaskPhase.STOPPED:
                return "Cancelled"
            case _:
                return title(self.value)

    @property
    def glyph(self) -> str:
        """The mark ahead of the phase, so a state never reads by colour alone."""
        match self:
            case TaskPhase.QUEUED:
                return "○"
            case TaskPhase.DONE:
                return "●"
            case TaskPhase.REUSED:
                return "↺"
            case TaskPhase.FAILED:
                return "⊘"
            case TaskPhase.STALLED:
                return "⧖"
            case TaskPhase.STOPPED:
                return "–"
            case _:
                return "◐"

    @property
    def active(self) -> bool:
        """Whether a child process runs the task."""
        return self in (TaskPhase.STARTING, TaskPhase.GEOMETRY, TaskPhase.WAITING, TaskPhase.MESHING, TaskPhase.SOLVING, TaskPhase.FINISHING)

    @property
    def ended(self) -> bool:
        """Whether the task's last run is over."""
        return self in (TaskPhase.DONE, TaskPhase.REUSED, TaskPhase.FAILED, TaskPhase.STALLED, TaskPhase.STOPPED)


FOCUS_ORDER = (TaskPhase.SOLVING, TaskPhase.MESHING, TaskPhase.GEOMETRY, TaskPhase.WAITING, TaskPhase.FINISHING, TaskPhase.STARTING, TaskPhase.QUEUED)
"""The phases the collapsed strip leads with, most telling first."""


def ordinal(number: int) -> str:
    """`2nd`, `3rd`, `11th`: the number as a place in a line."""
    if 10 <= number % 100 <= 20:
        return f"{number}th"

    match number % 10:
        case 1:
            return f"{number}st"
        case 2:
            return f"{number}nd"
        case 3:
            return f"{number}rd"
        case _:
            return f"{number}th"


def clock(seconds: float | None) -> str:
    """A duration as the task strip shows it: `0.3s`, `12s`, `1m 12s`, `1h 02m`; a dash for None."""
    if seconds is None:
        return "—"

    tenths = round(seconds, 1)
    if tenths < 10:
        return f"{tenths:.1f}s"

    whole = round(seconds)
    if whole < 60:
        return f"{whole}s"

    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"

    return f"{minutes}m {secs:02d}s"


class TaskField(StrEnum):
    """The keys of a task row in the client state; each names a field or computed field of `TaskRow`."""

    LABEL = "label"
    TITLE = "title"
    PHASE = "phase"
    RUN_ID = "run_id"
    RUNS = "runs"
    SOLVES = "solves"
    REUSES = "reuses"
    SOLVER = "solver"
    PROGRESS = "progress"
    ITERATION = "iteration"
    MAX_ITERATIONS = "max_iterations"
    ELAPSED = "elapsed"
    ERROR = "error"
    TRACEBACK = "traceback"
    STALL = "stall"
    POSITION = "position"
    AGAIN = "again"
    REMOVED = "removed"
    STOPPING = "stopping"
    STATE = "state"
    NOTE = "note"
    GLYPH = "glyph"
    PERCENT = "percent"
    ITERATIONS = "iterations"
    CLOCK = "clock"
    ACTIVE = "active"
    BUSY = "busy"
    FAILED = "failed"
    STALLED = "stalled"
    CANCELLABLE = "cancellable"
    RETRYABLE = "retryable"


class TaskRow(BaseModel):
    """One task as the strip shows it: where it stands, the run it is on, and how its last run ended."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    label: str
    """The task's label, which keys the row across saves."""

    title: str
    """The label in Title Case."""

    phase: TaskPhase = TaskPhase.QUEUED
    """Where the task stands."""

    run_id: str | None = None
    """The run the task is on, or last finished on."""

    runs: int = 0
    """How many runs the task's current or last run evaluated."""

    solves: int = 0
    """How many of those runs solved rather than reused a bundle."""

    reuses: int = 0
    """How many of those runs reused a finished bundle."""

    solver: str | None = None
    """The solver the running solve last reported on."""

    progress: float | None = None
    """`f` `[-]`: The share of the running solve's convergence covered, never moving back within a stage; None when unknown."""

    iteration: int | None = None
    """The running linear solve's latest iteration, from its progress line; None before one."""

    max_iterations: int | None = None
    """The running linear solve's iteration cap; None when unknown."""

    elapsed: float | None = None
    """`t` `[s]`: Wall time of the task's current or last run; None before it first starts."""

    error: str | None = None
    """The failure's type and the first line of its message."""

    traceback: str | None = None
    """The failure's traceback, the process's last output lines when it left no outcome, or a stalled task's last log lines."""

    stall: str | None = None
    """Why the watchdog stopped a solve of the task's current or last run; None when none stalled."""

    position: int | None = None
    """The task's place in the queue, from 1; None when it is not queued."""

    again: bool = False
    """Whether a save queued the task again while it was running."""

    removed: bool = False
    """Whether a save dropped the task while it was running; the row leaves once the run ends."""

    stopping: bool = False
    """Whether a confirmed Cancel is stopping the running task."""

    @computed_field
    @property
    def state(self) -> str:
        """The phase word with its qualifier, as the strip reads it."""
        if self.stopping and self.phase.active:
            return "Cancelling"

        match self.phase:
            case TaskPhase.QUEUED if self.position is not None and self.position > 1:
                return f"Queued · {ordinal(self.position)} In Line"
            case TaskPhase.WAITING:
                return "Waiting · Another Process Is Solving It"
            case TaskPhase.SOLVING if self.solver:
                return f"Solving · {title(self.solver)}"
            case TaskPhase.DONE if self.runs > 1:
                return f"Done · {self.runs} Runs"
            case TaskPhase.FAILED if self.error:
                return f"Failed · {self.error}"
            case TaskPhase.STALLED if self.stall:
                return f"Stalled · {self.stall}"
            case _:
                return self.phase.word

    @computed_field
    @property
    def note(self) -> str:
        """What a save did to the running task, empty when nothing."""
        if self.removed:
            return "Removed From Script, Finishing"

        if self.again and self.phase.active:
            return "Previous Version, Runs Again Next"

        return ""

    @computed_field
    @property
    def glyph(self) -> str:
        """The phase's mark."""
        return self.phase.glyph

    @computed_field
    @property
    def percent(self) -> str:
        """The progress as a whole percent, empty when unknown."""
        return f"{self.progress * 100:.0f}%" if self.progress is not None else ""

    @computed_field
    @property
    def iterations(self) -> str:
        """The linear iteration against its cap, `It 412/5000`, empty before the first progress line."""
        if self.iteration is None:
            return ""

        cap = f"/{self.max_iterations}" if self.max_iterations is not None else ""

        return f"It {self.iteration}{cap}"

    @computed_field
    @property
    def clock(self) -> str:
        """The elapsed time as the strip shows it."""
        return clock(self.elapsed)

    @computed_field
    @property
    def active(self) -> bool:
        """Whether a child process runs the task."""
        return self.phase.active

    @computed_field
    @property
    def busy(self) -> bool:
        """Whether the task runs with no percent to show."""
        return self.phase.active and self.progress is None

    @computed_field
    @property
    def failed(self) -> bool:
        """Whether the task's last run failed or stalled, which the strip marks in the destructive colour."""
        return self.phase in (TaskPhase.FAILED, TaskPhase.STALLED)

    @computed_field
    @property
    def stalled(self) -> bool:
        """Whether the task's last run ended on a stalled solve, whose details are its log."""
        return self.phase is TaskPhase.STALLED

    @computed_field
    @property
    def cancellable(self) -> bool:
        """Whether Cancel applies: the task waits in the queue, or runs and no Cancel is stopping it yet."""
        return (self.phase.active and not self.stopping) or self.phase is TaskPhase.QUEUED

    @computed_field
    @property
    def retryable(self) -> bool:
        """Whether Retry applies: the task's last run failed, stalled or was cancelled."""
        return self.phase in (TaskPhase.FAILED, TaskPhase.STALLED, TaskPhase.STOPPED)

    def queued(self) -> "TaskRow":
        """The row queued afresh: no run, no clock and no failure until the task starts again."""
        update: dict[str, object] = {
            TaskField.PHASE: TaskPhase.QUEUED,
            TaskField.RUN_ID: None,
            TaskField.RUNS: 0,
            TaskField.SOLVES: 0,
            TaskField.REUSES: 0,
            TaskField.SOLVER: None,
            TaskField.PROGRESS: None,
            TaskField.ITERATION: None,
            TaskField.MAX_ITERATIONS: None,
            TaskField.ELAPSED: None,
            TaskField.ERROR: None,
            TaskField.TRACEBACK: None,
            TaskField.STALL: None,
            TaskField.AGAIN: False,
            TaskField.REMOVED: False,
            TaskField.STOPPING: False,
        }

        return self.model_copy(update=update)


def observe(row: TaskRow, record: RelayedRecord) -> TaskRow:
    """The row after one record of its task's child: a new run id starts a run, a stage or a claim wait moves the phase, a progress record moves the bar, a stall record says why."""
    extra = record["extra"]
    update: dict[str, object] = {}

    run_id = extra.get(LogKey.RUN_ID)
    if isinstance(run_id, str) and run_id != row.run_id:
        update |= {
            TaskField.RUN_ID: run_id,
            TaskField.RUNS: row.runs + 1,
            TaskField.PHASE: TaskPhase.STARTING,
            TaskField.PROGRESS: None,
            TaskField.SOLVER: None,
            TaskField.ITERATION: None,
            TaskField.MAX_ITERATIONS: None,
        }

    if LogKey.HOLDER in extra:
        update[TaskField.PHASE] = TaskPhase.WAITING

    if isinstance(extra.get(LogKey.ORIGIN), str):
        update[TaskField.REUSES] = row.reuses + 1

    if isinstance(extra.get(LogKey.RUN_DIR), str):
        update[TaskField.SOLVES] = row.solves + 1
        if row.phase is TaskPhase.WAITING:
            update[TaskField.PHASE] = TaskPhase.STARTING

    stage = extra.get(LogKey.STAGE)
    if isinstance(stage, str) and stage in tuple(Stage):
        phase = TaskPhase.of(Stage(stage))
        moved = phase is not row.phase
        kept = not moved and phase is TaskPhase.SOLVING
        update |= {
            TaskField.PHASE: phase,
            TaskField.PROGRESS: row.progress if kept else None,
            TaskField.ITERATION: row.iteration if kept else None,
            TaskField.MAX_ITERATIONS: row.max_iterations if kept else None,
        }

    phase = update.get(TaskField.PHASE, row.phase)
    progress = extra.get(LogKey.PROGRESS)
    if phase is TaskPhase.SOLVING and isinstance(progress, int | float):
        known = update.get(TaskField.PROGRESS, row.progress)
        update[TaskField.PROGRESS] = max(float(progress), known) if isinstance(known, float) else float(progress)

    solver = extra.get(LogKey.SOLVER)
    if phase is TaskPhase.SOLVING and isinstance(solver, str) and solver:
        update[TaskField.SOLVER] = solver

    iteration = extra.get(LogKey.ITERATION)
    if phase is TaskPhase.SOLVING and isinstance(iteration, int):
        cap = extra.get(LogKey.MAX_ITERATIONS)
        update |= {TaskField.ITERATION: iteration, TaskField.MAX_ITERATIONS: cap if isinstance(cap, int) else None}

    stall = extra.get(LogKey.STALL)
    if isinstance(stall, str) and stall in tuple(StallReason):
        match StallReason(stall):
            case StallReason.REPEATED:
                what = "Residual Flat"
            case StallReason.NO_PROGRESS:
                what = "Residual Not Improving"

        residual = extra.get(LogKey.RESIDUAL)
        at = f" at {residual:.2e}" if isinstance(residual, float) else ""
        by = f" by It {iteration}" if isinstance(iteration, int) else ""
        update[TaskField.STALL] = f"{what}{at}{by}"

    return row.model_copy(update=update) if update else row


class TaskJob(BaseModel):
    """What a task's child process is handed: the factory to list the plan with, the label to run, and the directory to import from."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    factory: str
    """The factory's `module:attr` target."""

    label: str
    """The label of the task to run."""

    path: str | None = None
    """The directory put first on the child's import path, so the script's own modules import; None leaves the path alone."""


class OutcomeKind(StrEnum):
    """How a task's child process ended."""

    DONE = "done"
    FAILED = "failed"


class TaskDone(BaseModel):
    """A task whose callable returned."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    status: Literal[OutcomeKind.DONE] = OutcomeKind.DONE
    """Tells the outcome kinds apart."""


class TaskFailed(BaseModel):
    """A task whose callable, or the import of its plan, raised."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    status: Literal[OutcomeKind.FAILED] = OutcomeKind.FAILED
    """Tells the outcome kinds apart."""

    error: str
    """The exception's type name."""

    message: str
    """The exception's message."""

    traceback: str
    """The formatted traceback."""


type TaskOutcome = Annotated[TaskDone | TaskFailed, Field(discriminator="status")]
"""The outcome file of a task's child process, told apart by its `status`."""


class TaskExit(IntEnum):
    """The task worker's exit codes."""

    OK = 0
    """The task returned."""

    FAILED = 1
    """The task raised; the outcome file says how."""

    USAGE = 2
    """The command line was wrong."""


@functools.cache
def task_outcomes() -> TypeAdapter[TaskDone | TaskFailed]:
    """The validator of a task's outcome file."""
    return TypeAdapter[TaskDone | TaskFailed](TaskOutcome)


def read_outcome(path: Path) -> TaskDone | TaskFailed | None:
    """The outcome a child process wrote, None when it wrote none or an unreadable one."""
    try:
        return task_outcomes().validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class TaskProcess(Protocol):
    """A task's child process as the board drives it."""

    @property
    def pid(self) -> int:
        """The process id."""
        ...

    def lines(self) -> AsyncIterator[str]:
        """Each merged output line, until the process closes its output."""
        ...

    async def wait(self) -> int:
        """Waits for the process to exit and returns its exit code."""
        ...

    def wait_for(self, timeout: float) -> bool:
        """Blocks until the process exits or `timeout` seconds pass; whether it exited."""
        ...

    def interrupt(self) -> bool:
        """Sends the break to the process group; False when it cannot be sent."""
        ...

    def kill(self) -> None:
        """Ends every process in the tree now."""
        ...

    def close(self) -> None:
        """Ends what is left of the tree and releases it; a second call does nothing."""
        ...


type Spawn = Callable[[TaskJob, Path], Awaitable[TaskProcess]]
"""Starts the child process of a job, whose files live in the given folder."""


@dataclass
class ChildTask:
    """A task worker started by `spawn_task`, in a process tree of its own."""

    process: asyncio.subprocess.Process
    tree: ProcessTree
    closed: bool = False

    @property
    def pid(self) -> int:
        """The worker's process id."""
        return self.process.pid

    async def lines(self) -> AsyncIterator[str]:
        """Each merged output line, decoded as UTF-8."""
        stdout = self.process.stdout
        if stdout is None:
            return

        async for raw in stdout:
            yield raw.decode("utf-8", errors="replace")

    async def wait(self) -> int:
        """The worker's exit code, once it exits."""
        return await self.process.wait()

    def wait_for(self, timeout: float) -> bool:
        """Whether the worker exited within `timeout` seconds."""
        try:
            _ = psutil.Process(self.pid).wait(timeout)
        except psutil.TimeoutExpired:
            return False
        except psutil.NoSuchProcess:
            return True

        return True

    def interrupt(self) -> bool:
        """Sends the break to the worker's group."""
        return self.tree.interrupt()

    def kill(self) -> None:
        """Ends the worker's whole tree."""
        if not self.closed:
            _ = self.tree.kill()

    def close(self) -> None:
        """Ends what is left of the tree and releases it, once."""
        if self.closed:
            return

        self.closed = True
        self.tree.close()


async def spawn_task(job: TaskJob, folder: Path) -> TaskProcess:
    """Starts `python -m femforge_workbench.task_worker` on the job in its own process group and tree, its job and outcome files in `folder`."""
    job_file = folder / JOB_FILE
    _ = job_file.write_text(job.model_dump_json(), encoding="utf-8")

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        WORKER_MODULE,
        str(job_file),
        str(folder / OUTCOME_FILE),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, **STREAM_ENV, "PYTHONUTF8": "1"},
        cwd=os.getcwd(),
        limit=LINE_LIMIT,
        creationflags=NEW_GROUP | CREATION_FLAGS,
        start_new_session=NEW_SESSION,
    )

    return ChildTask(process, ProcessTree.adopt(process.pid))


class TaskView(BaseModel):
    """What the task strip shows: every row, the summary line and the row it leads with."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    rows: list[TaskRow]
    """The tasks in plan order, then a dropped one still finishing."""

    summary: str
    """How many tasks are done, as the strip's first words."""

    count: str
    """The done count as a narrow strip shows it, `1/3`."""

    focus: TaskRow | None
    """The row the collapsed strip leads with: the most telling active one, else the last failure, else none."""

    failed: int
    """How many tasks failed their last run."""

    stalled: int
    """How many tasks ended their last run on a stalled solve."""

    error: str | None
    """Why the script's last save listed no tasks; the rows then stay as they were."""

    reloaded: str
    """When the plan was last submitted, as `HH:MM:SS`."""


class TaskHost(Protocol):
    """What a task board needs of the shell it runs in."""

    def marshal(self, fn: Callable[..., object], *args: object) -> None:
        """Runs `fn(*args)` on the server loop from any thread."""
        ...

    def show_tasks(self, view: TaskView) -> None:
        """Shows the rows on the client."""
        ...

    def follow_run(self, previous: str, current: str) -> None:
        """Opens `current` in a viewer that shows `previous`."""
        ...


@dataclass
class Running:
    """The task whose child process runs now."""

    label: str
    process: TaskProcess
    folder: Path
    started: float
    exited: asyncio.Event = field(default_factory=asyncio.Event)
    stopping: bool = False
    run_dir: Path | None = None
    """The bundle directory of the run being solved, whose claim a forced stop reaps."""
    messages: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_TAIL))
    """The child's last log messages, which a stalled task shows as its log."""


class TaskBoard:
    """Runs a script's plan one task at a time in child processes, re-submits it after every save, and publishes a row per task."""

    def __init__(self, app: TaskHost, factory: str, plan: Sequence[Task], *, path: str | None, roots: Sequence[Path], spawn: Spawn = spawn_task) -> None:
        """Queues the plan; `start` runs it once the server loop runs.

        Args:
            app: The shell the rows show in.
            factory: The `module:attr` target of the factory listing the plan, which every save and every child calls again.
            plan: The tasks the factory listed at startup.
            path: The directory each child puts first on its import path; None leaves the path alone.
            roots: The directories whose .py saves re-submit the plan.
            spawn: Starts a task's child process.
        """
        self.app = app
        self.factory = factory
        self.path = path
        self.roots = list(roots)
        self.spawn = spawn
        self.plan: tuple[Task, ...] = ()
        self.by_label: dict[str, TaskRow] = {}
        """Each task's row by label, in the order the strip lists them."""
        self.queue: list[str] = []
        self.running: Running | None = None
        self.last_runs: dict[str, str] = {}
        """The run each label last ended on, which a newer run of the same label takes over in the viewer."""
        self.error: str | None = None
        self.reloaded = ""
        self.reloader: Reloader[object] | None = None
        self.wake: asyncio.Event | None = None
        self.jobs: set[asyncio.Task[None]] = set()
        self.closed = False
        self.dirty = False
        self.submit(plan)

    def start(self) -> None:
        """Runs the queue and watches the roots, on the server loop."""
        self.wake = asyncio.Event()
        self.background(self.drain())
        self.background(self.tick())

        def failed(error: Exception) -> None:
            self.script_error(error)

        reloader: Reloader[object] = Reloader(self.factory, self.roots, self.apply, kind=object, marshal=self.app.marshal, failed=failed)
        reloader.start()
        self.reloader = reloader
        self.wake.set()

    def background(self, work: Awaitable[None]) -> None:
        """Runs a coroutine on the loop, kept alive until it ends."""

        async def run() -> None:
            await work

        job = asyncio.get_running_loop().create_task(run())
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)

    def apply(self, listed: object, seconds: float) -> None:
        """Submits the plan a save listed, or reports why it listed none and keeps the rows."""
        try:
            plan = plan_of(listed)
        except (TypeError, ValueError) as error:
            self.script_error(error)
            return

        self.error = None
        self.submit(plan)
        log.bind(tasks=[task.label for task in plan], seconds=round(seconds, 2)).info("Tasks submitted.")

    def script_error(self, error: Exception) -> None:
        """Shows why the script listed no tasks, keeping the rows of the last plan."""
        self.error = f"{type(error).__name__}: {error}"
        log.bind(error=self.error).warning("Tasks not submitted.")
        self.publish(now=True)

    def submit(self, plan: Sequence[Task]) -> None:
        """Queues every task of the plan in order; a running task finishes on its old code and runs once more, and a dropped one leaves once it ends."""
        self.plan = tuple(plan)
        self.reloaded = datetime.now().strftime("%H:%M:%S")
        running = self.running.label if self.running is not None else None

        rows: dict[str, TaskRow] = {}
        for task in self.plan:
            row = self.by_label.get(task.label) or TaskRow(label=task.label, title=shown_label(task.label))
            if task.label == running:
                rows[task.label] = row.model_copy(update={TaskField.AGAIN: True, TaskField.REMOVED: False})
            else:
                rows[task.label] = row.queued()

        if running is not None and running not in rows:
            rows[running] = self.by_label[running].model_copy(update={TaskField.AGAIN: False, TaskField.REMOVED: True})

        self.by_label = rows
        self.queue = [task.label for task in self.plan]
        self.publish(now=True)
        if self.wake is not None:
            self.wake.set()

    def retry(self, label: str) -> None:
        """Queues a failed or cancelled task of the plan again, after those already queued."""
        row = self.by_label.get(label)
        if label in self.queue or row is None or not row.retryable:
            return

        self.queue.append(label)
        self.by_label[label] = row.queued()
        self.publish(now=True)
        if self.wake is not None:
            self.wake.set()

    def cancel(self, label: str) -> None:
        """Takes a queued task out of the queue, or stops the running one, which then reads Cancelled."""
        running = self.running
        if running is not None and running.label == label:
            if not running.stopping:
                running.stopping = True
                self.by_label[label] = self.by_label[label].model_copy(update={TaskField.STOPPING: True})
                self.publish(now=True)
                self.background(self.stop(running))

            return

        if label not in self.queue:
            return

        self.queue.remove(label)
        self.by_label[label] = self.by_label[label].model_copy(update={TaskField.PHASE: TaskPhase.STOPPED})
        self.publish(now=True)

    def rows(self) -> list[TaskRow]:
        """The rows in order, the running one's elapsed time and each queued one's place brought up to date."""
        running = self.running
        if running is not None and running.label in self.by_label:
            self.by_label[running.label] = self.by_label[running.label].model_copy(update={TaskField.ELAPSED: time.monotonic() - running.started})

        rows: list[TaskRow] = []
        for label, row in self.by_label.items():
            queued = label in self.queue and not row.phase.active
            position = self.queue.index(label) + 1 if queued else None
            rows.append(row.model_copy(update={TaskField.POSITION: position}) if row.position != position else row)

        return rows

    def view(self) -> TaskView:
        """The rows, the summary and the focus row as the strip shows them."""
        rows = self.rows()
        shown = [row for row in rows if not row.removed]
        counts = {phase: sum(row.phase is phase for row in shown) for phase in (TaskPhase.DONE, TaskPhase.REUSED, TaskPhase.FAILED, TaskPhase.STALLED)}
        done = counts[TaskPhase.DONE] + counts[TaskPhase.REUSED]
        busy = any(row.phase.active or row.phase is TaskPhase.QUEUED for row in rows)

        tasks = "Task" if len(shown) == 1 else "Tasks"
        ended = [f"{count} {phase.word}" for phase, count in counts.items() if count] or [f"0 {TaskPhase.DONE.word}"]
        summary = f"{done} of {len(shown)} Done" if busy else " · ".join([f"{len(shown)} {tasks}", *ended])

        ranked = [row for phase in FOCUS_ORDER for row in rows if row.phase is phase]
        failures = [row for row in rows if row.failed]
        focus = ranked[0] if ranked else failures[-1] if failures else None

        return TaskView(
            rows=rows,
            summary=summary,
            count=f"{done}/{len(shown)}",
            focus=focus,
            failed=counts[TaskPhase.FAILED],
            stalled=counts[TaskPhase.STALLED],
            error=self.error,
            reloaded=self.reloaded,
        )

    def publish(self, *, now: bool = False) -> None:
        """Pushes the rows to the client now, or marks them for the next tick."""
        if self.closed:
            return

        if not now:
            self.dirty = True
            return

        self.dirty = False
        self.app.show_tasks(self.view())

    async def tick(self) -> None:
        """Pushes the rows once a period while a task runs or a record moved them."""
        while not self.closed:
            await asyncio.sleep(PUBLISH_PERIOD_S)
            if self.running is not None or self.dirty:
                self.publish(now=True)

    async def drain(self) -> None:
        """Runs the queued tasks one at a time until the board shuts down; a task that fails in any way fails its row, never the server."""
        wake = self.wake
        if wake is None:
            raise RuntimeError("start() the board before draining it")

        while not self.closed:
            if not self.queue:
                _ = await wake.wait()
                wake.clear()
                continue

            label = self.queue.pop(0)
            if label not in self.by_label:
                continue

            try:
                await self.run_one(label)
            except Exception as error:  # noqa: BLE001 - a broken task run fails its row, never the server
                log.opt(exception=True).bind(**{str(LogKey.TASK): label}).error("Task run failed.")
                self.end(label, phase=TaskPhase.FAILED, error=f"{type(error).__name__}: {error}")

    async def run_one(self, label: str) -> None:
        """Runs one task in its own child process, relaying its records and moving its row, then settles the row by its outcome."""
        self.by_label[label] = self.by_label[label].queued().model_copy(update={TaskField.PHASE: TaskPhase.STARTING, TaskField.ELAPSED: 0.0})
        folder = Path(tempfile.mkdtemp(prefix="femforge-task-"))
        started = time.monotonic()
        try:
            process = await self.spawn(TaskJob(factory=self.factory, label=label, path=self.path), folder)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

        running = Running(label, process, folder, started)
        self.running = running
        self.publish(now=True)
        log.bind(**{str(LogKey.TASK): label}, pid=process.pid).info("Task started.")

        tail: deque[str] = deque(maxlen=LOG_TAIL)
        try:
            async for line in process.lines():
                self.relay(running, line, tail)

            code = await process.wait()
        finally:
            running.exited.set()
            self.running = None
            process.close()

        outcome = read_outcome(folder / OUTCOME_FILE)
        shutil.rmtree(folder, ignore_errors=True)
        self.settle(running, code, outcome, tail)

    def relay(self, running: Running, line: str, tail: deque[str]) -> None:
        """Moves the row by one output line of its child and logs the line here, for the terminal only: the child stored its own records.

        Args:
            running: The task whose child printed the line.
            line: The output line.
            tail: The child's last plain lines, which a failure without an outcome shows.
        """
        text = line.rstrip("\r\n")
        if not text.strip():
            return

        bound: dict[str, object] = {TELEMETRY_ROUTE: RELAYED_ROUTE}
        record = parse_relayed(text)
        if record is None:
            tail.append(text)
            log.bind(**bound).debug(text)
            return

        running.messages.append(record["message"])
        run_dir = record["extra"].get(LogKey.RUN_DIR)
        if isinstance(run_dir, str):
            running.run_dir = Path(run_dir)

        if running.label in self.by_label:
            self.by_label[running.label] = observe(self.by_label[running.label], record)
            self.publish()

        emit_relayed(record, **bound)

    def settle(self, running: Running, code: int, outcome: TaskDone | TaskFailed | None, tail: Sequence[str]) -> None:
        """Ends the row by how the child exited: done or reused when it returned with no stall, else cancelled when stopped, else stalled with its log, else failed.

        Args:
            running: The task whose child exited.
            code: The child's exit code.
            outcome: The outcome file the child wrote; None when it wrote none.
            tail: The child's last plain lines.
        """
        label = running.label
        row = self.by_label.get(label)
        if row is None:
            return

        returned = isinstance(outcome, TaskDone)
        if isinstance(outcome, TaskFailed):
            get_console().print(outcome.traceback, markup=False, highlight=False)

        if returned and row.stall is None:
            reused = row.reuses > 0 and row.solves == 0
            self.end(label, phase=TaskPhase.REUSED if reused else TaskPhase.DONE)
        elif running.stopping and not returned:
            self.end(label, phase=TaskPhase.STOPPED)
        elif row.stall is not None:
            self.end(label, phase=TaskPhase.STALLED, traceback="\n".join(running.messages) or None)
        elif isinstance(outcome, TaskFailed):
            first = outcome.message.strip().splitlines()[0] if outcome.message.strip() else ""
            error = f"{outcome.error}: {first}" if first else outcome.error
            self.end(label, phase=TaskPhase.FAILED, error=error, traceback=outcome.traceback)
        else:
            self.end(label, phase=TaskPhase.FAILED, error=f"Task Process Exited ({code})", traceback="\n".join(tail) or None)

        if running.label in self.by_label:
            self.by_label[label] = self.by_label[label].model_copy(update={TaskField.ELAPSED: time.monotonic() - running.started})

    def end(self, label: str, *, phase: TaskPhase, error: str | None = None, traceback: str | None = None) -> None:
        """Sets a row's final phase, follows its newer run into the viewer, and drops a row a save removed.

        Args:
            label: The task whose run ended.
            phase: How it ended.
            error: The failure's one-line summary.
            traceback: The failure's traceback or last output lines.
        """
        row = self.by_label.get(label)
        if row is None:
            return

        update: dict[str, object] = {
            TaskField.PHASE: phase,
            TaskField.ERROR: error,
            TaskField.TRACEBACK: traceback,
            TaskField.PROGRESS: None,
            TaskField.AGAIN: False,
            TaskField.STOPPING: False,
        }
        ended = row.model_copy(update=update)
        log.bind(**{str(LogKey.TASK): label}, phase=str(phase), error=error).info("Task ended.")

        previous = self.last_runs.get(label)
        if ended.run_id is not None:
            if phase in (TaskPhase.DONE, TaskPhase.REUSED) and previous is not None and previous != ended.run_id:
                self.app.follow_run(previous, ended.run_id)

            self.last_runs[label] = ended.run_id

        if ended.removed:
            del self.by_label[label]
        else:
            self.by_label[label] = ended

        self.publish(now=True)

    async def stop(self, running: Running) -> None:
        """Breaks the running task until it exits, kills its tree when it is still there after the grace, then reaps the claim it left."""
        log.bind(**{str(LogKey.TASK): running.label}).info("Stopping the task.")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STOP_GRACE_S
        exited = False
        while not exited and running.process.interrupt() and loop.time() < deadline:
            try:
                _ = await asyncio.wait_for(running.exited.wait(), min(BREAK_REPEAT_S, deadline - loop.time()))
            except TimeoutError:
                continue

            exited = True

        if not exited:
            running.process.kill()
            _ = await running.exited.wait()

        await asyncio.to_thread(reap, running, gone=True)

    def shutdown(self, grace: float = STOP_GRACE_S) -> None:
        """Stops the board for good: empties the queue, breaks the running task until it exits or `grace` seconds pass, kills what is left, and reaps the claim it left."""
        if self.closed:
            return

        self.closed = True
        self.queue.clear()
        if self.reloader is not None:
            self.reloader.stop()

        for job in list(self.jobs):
            _ = job.cancel()

        running = self.running
        if running is None:
            return

        running.stopping = True
        exited = break_until_exit(running.process, grace)
        running.process.close()
        gone = exited or running.process.wait_for(KILL_WAIT_S)
        reap(running, gone=gone)
        outcome = read_outcome(running.folder / OUTCOME_FILE)
        shutil.rmtree(running.folder, ignore_errors=True)
        if running.label in self.by_label:
            phase = TaskPhase.DONE if isinstance(outcome, TaskDone) else TaskPhase.STOPPED
            self.by_label[running.label] = self.by_label[running.label].model_copy(update={TaskField.PHASE: phase})

        log.bind(**{str(LogKey.TASK): running.label}, graceful=exited).info("Task stopped with the workbench.")


def break_until_exit(process: TaskProcess, grace: float) -> bool:
    """Sends the break every `BREAK_REPEAT_S` until the process exits or `grace` seconds pass; whether it exited, False at once without a console."""
    deadline = time.monotonic() + grace
    while process.interrupt():
        left = deadline - time.monotonic()
        if process.wait_for(max(min(BREAK_REPEAT_S, left), 0.0)):
            return True

        if time.monotonic() >= deadline:
            return False

    return False


def reap(running: Running, *, gone: bool) -> None:
    """Breaks the claims a stopped task left on its runs, its own process counted dead once `gone`, so the next run need not wait for them to go stale."""
    if running.run_dir is None:
        return

    from femforge.run.reap import reap_runs

    def alive(pid: int) -> bool:
        if gone and pid == running.process.pid:
            return False

        return psutil.pid_exists(pid)

    reaped = reap_runs(running.run_dir.parent, alive=alive)
    log.bind(**{str(LogKey.TASK): running.label}, reaped=[run.run_id for run in reaped]).info("Claims of the stopped task reaped.")
