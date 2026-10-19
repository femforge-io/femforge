"""The task worker: ``python -m femforge_workbench.task_worker JOB OUTCOME`` runs one labelled task of a script's plan and writes how it ended."""

import signal
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path
from types import FrameType

from femforge.common.log import LogKey, log, log_to_stdout_json
from femforge_workbench.registry import resolve
from femforge_workbench.tasks import WORKER_MODULE, TaskDone, TaskExit, TaskFailed, TaskJob, plan_of


if sys.platform == "win32":
    BREAK_SIGNUM = signal.SIGBREAK
    """The signal the board's break arrives as: CTRL_BREAK_EVENT is SIGBREAK on Windows."""
else:
    BREAK_SIGNUM = signal.SIGTERM


def interrupt(signum: int, frame: FrameType | None) -> None:
    """Turns the first break into KeyboardInterrupt, so the task's own cleanup releases its run; the breaks the board repeats after it are ignored."""
    _ = signal.signal(BREAK_SIGNUM, signal.SIG_IGN)
    raise KeyboardInterrupt


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the task the job file `JOB` names and writes the outcome file `OUTCOME`; a break ends it with no outcome."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(f"usage: python -m {WORKER_MODULE} JOB OUTCOME", file=sys.stderr)
        return TaskExit.USAGE

    job = TaskJob.model_validate_json(Path(args[0]).read_text(encoding="utf-8"))
    outcome = Path(args[1])
    log_to_stdout_json(process=job.label)
    _ = signal.signal(BREAK_SIGNUM, interrupt)
    served = [entry for entry in job.sys_path if entry not in sys.path and entry != job.path]
    sys.path[0:0] = [job.path, *served] if job.path is not None else served

    try:
        factory = resolve(job.factory)
        if not callable(factory):
            raise TypeError(f"{job.factory} is not callable")

        task = next((task for task in plan_of(factory()) if task.label == job.label), None)
        if task is None:
            raise LookupError(f"{job.factory}() lists no task labelled {job.label!r}")

        log.bind(**{str(LogKey.TASK): job.label}).info("Task running.")
        _ = task.run()
        _ = signal.signal(BREAK_SIGNUM, signal.SIG_IGN)
    except Exception as error:  # noqa: BLE001 - every failure becomes an outcome the board reads
        log.bind(**{str(LogKey.TASK): job.label}).error(f"Task failed: {type(error).__name__}: {error}")
        failed = TaskFailed(error=type(error).__name__, message=str(error), traceback=traceback.format_exc())
        _ = outcome.write_text(failed.model_dump_json(), encoding="utf-8")
        return TaskExit.FAILED

    _ = outcome.write_text(TaskDone().model_dump_json(), encoding="utf-8")
    return TaskExit.OK


if __name__ == "__main__":
    sys.exit(main())
