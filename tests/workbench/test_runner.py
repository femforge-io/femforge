"""ScriptRunner bookkeeping: the client rows, a name run twice, a process without a pipe and a stop of nothing."""

import asyncio
import os
import sys

import pytest

from femforge_workbench.runner import ScriptField, ScriptRunner, ScriptStatus


QUICK = [sys.executable, "-c", "print('hi')"]


def _runner() -> tuple[ScriptRunner, list[str]]:
    lines: list[str] = []

    return ScriptRunner(lambda _, line: lines.append(line), lambda _: None), lines


def test_a_finished_run_is_a_row_and_a_running_name_cannot_start_again() -> None:
    async def scenario() -> None:
        runner, lines = _runner()
        task = await runner.run("quick", QUICK)

        [row] = runner.rows()
        assert lines == ["hi"] and row[ScriptField.NAME] == "quick" and row[ScriptField.STATUS] == ScriptStatus.DONE and row[ScriptField.RETURNCODE] == 0

        task.status = ScriptStatus.RUNNING
        with pytest.raises(RuntimeError, match="already running"):
            _ = await runner.run("quick", QUICK)

    asyncio.run(scenario())


def test_a_process_without_an_output_pipe_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = asyncio.create_subprocess_exec

    pipes: list[asyncio.StreamReader | None] = []

    async def pipeless(*args: str, **kwargs: object) -> asyncio.subprocess.Process:
        process = await spawn(*args, **kwargs)  # pyright: ignore[reportArgumentType]  # forwarded as received
        pipes.append(process.stdout)
        process.stdout = None

        return process

    async def scenario() -> None:
        runner, _ = _runner()
        with pytest.raises(RuntimeError, match="no output pipe"):
            _ = await runner.run("pipeless", QUICK)

        task = runner.tasks["pipeless"]
        [piped] = pipes
        assert piped is not None and await piped.read() == b"hi" + os.linesep.encode()
        _ = await task.process.wait()
        task.tree.close()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", pipeless)
    asyncio.run(scenario())


def test_stopping_a_name_that_never_ran_or_already_ended_does_nothing() -> None:
    async def scenario() -> ScriptStatus:
        runner, _ = _runner()
        await runner.stop("missing")
        task = await runner.run("quick", QUICK)
        await runner.stop("quick")

        return task.status

    assert asyncio.run(scenario()) is ScriptStatus.DONE
