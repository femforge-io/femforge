"""Script runs on the server loop: each catalogue script as a subprocess whose lines stream as they arrive and whose process tree dies on stop."""

import asyncio
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from femforge.common.log import log
from femforge_workbench.process_tree import CREATION_FLAGS, NEW_SESSION, ProcessTree


LINE_LIMIT = 1 << 20
"""Bytes one streamed line may hold before the reader gives up on it."""

STREAM_ENV = {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "COLUMNS": "200"}
"""Makes the child flush every line as it is written, in UTF-8 whatever the console code page, laid out for a wide terminal."""


class TaskStatus(StrEnum):
    """Where a script run stands."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"


class TaskField(StrEnum):
    """The keys of a task row in the client state."""

    NAME = "name"
    PID = "pid"
    STARTED = "started"
    STATUS = "status"
    RETURNCODE = "returncode"


type LineHandler = Callable[[str, str], None]
"""Receives the task name and one output line without its newline."""

type TaskHandler = Callable[["Task"], None]
"""Receives the task after it started and again after it exited."""


@dataclass
class Task:
    """One script run: its process, when it started and how it ended."""

    name: str
    process: asyncio.subprocess.Process
    tree: ProcessTree
    started: datetime = field(default_factory=datetime.now)
    status: TaskStatus = TaskStatus.RUNNING
    returncode: int | None = None
    stopping: bool = False

    @property
    def pid(self) -> int:
        """The process id."""
        return self.process.pid

    def row(self) -> dict[str, object]:
        """The task as a row of the client state."""
        return {
            TaskField.NAME: self.name,
            TaskField.PID: self.pid,
            TaskField.STARTED: self.started.isoformat(timespec="seconds"),
            TaskField.STATUS: str(self.status),
            TaskField.RETURNCODE: self.returncode,
        }


class ScriptRunner:
    """Starts named commands, streaming their output lines and reporting each status change."""

    def __init__(self, on_line: LineHandler, on_change: TaskHandler) -> None:
        """Takes the handlers every task's lines and status changes go to."""
        self.on_line = on_line
        self.on_change = on_change
        self.tasks: dict[str, Task] = {}

    def running(self, name: str) -> bool:
        """Whether the named task runs."""
        task = self.tasks.get(name)

        return task is not None and task.status is TaskStatus.RUNNING

    def rows(self) -> list[dict[str, object]]:
        """Every task as a row of the client state."""
        return [task.row() for task in self.tasks.values()]

    async def run(self, name: str, command: Sequence[str], *, cwd: Path | None = None) -> Task:
        """Runs the command to completion, streaming its lines; a name still running is an error.

        Args:
            name: The task name, unique among the running tasks.
            command: The command and its arguments.
            cwd: The directory the command runs in; None keeps the server's.
        """
        if self.running(name):
            raise RuntimeError(f"{name} is already running")

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, **STREAM_ENV},
            limit=LINE_LIMIT,
            creationflags=CREATION_FLAGS,
            start_new_session=NEW_SESSION,
        )
        tree = ProcessTree.adopt(process.pid)
        task = Task(name, process, tree)
        self.tasks[name] = task
        log.bind(task=name, pid=task.pid, command=list(command)).info("Script started.")
        self.on_change(task)

        stdout = process.stdout
        if stdout is None:
            raise RuntimeError(f"{name} has no output pipe")

        try:
            async for raw in stdout:
                self.on_line(name, raw.decode("utf-8", errors="replace").rstrip("\r\n"))

            task.returncode = await process.wait()
        finally:
            tree.close()

        if task.stopping:
            task.status = TaskStatus.STOPPED
        else:
            task.status = TaskStatus.DONE if task.returncode == 0 else TaskStatus.FAILED

        log.bind(task=name, returncode=task.returncode, status=str(task.status)).info("Script exited.")
        self.on_change(task)

        return task

    async def stop(self, name: str) -> None:
        """Kills the task's process tree; its run then ends as stopped."""
        task = self.tasks.get(name)
        if task is None or task.status is not TaskStatus.RUNNING:
            return

        task.stopping = True
        signalled = await asyncio.to_thread(task.tree.kill)
        log.bind(task=name, processes=signalled).info("Script stopped.")
