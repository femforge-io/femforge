"""Tests for common.log: setup_logging idempotency, stdlib/warnings interception, and run_log_context."""

from __future__ import annotations

import json
import logging
import warnings
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from rich.console import Console

from femforge.common.log import LOG_LEVEL_ENV, RelayedRecord, _reset_for_tests, gate_libraries, get_console, log, log_to_stdout_json, relay_record, run_log_context, setup_logging


if TYPE_CHECKING:
    from collections.abc import Iterator

    from loguru import Message, Record


@pytest.fixture
def clean_logging() -> Iterator[None]:
    """Snapshot stdlib root logging and restore it plus common.log module state around each test."""
    prev_handlers = logging.root.handlers[:]
    prev_level = logging.root.level
    _reset_for_tests()
    yield
    _reset_for_tests()
    logging.root.handlers[:] = prev_handlers
    logging.root.setLevel(prev_level)


def _console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, width=300), buf


@pytest.mark.usefixtures("clean_logging")
def test_setup_logging_installs_terminal_sink() -> None:
    """First call installs a RichSink that honors the requested level."""
    console, buf = _console()
    setup_logging(level="INFO", console=console)
    log.info("hello terminal")
    log.debug("too quiet")
    out = buf.getvalue()
    assert "hello terminal" in out
    assert "too quiet" not in out


@pytest.mark.usefixtures("clean_logging")
def test_setup_logging_idempotent() -> None:
    """A second call at the same level neither duplicates the sink nor replaces the console."""
    console, buf = _console()
    setup_logging(level="INFO", console=console)
    setup_logging(level="INFO", console=Console(file=StringIO()))
    log.info("exactly once")
    assert buf.getvalue().count("exactly once") == 1


@pytest.mark.usefixtures("clean_logging")
def test_setup_logging_adjusts_level() -> None:
    """A repeat call with a different level re-adds the same sink at the new level."""
    console, buf = _console()
    setup_logging(level="INFO", console=console)
    log.debug("hidden before")
    setup_logging(level="DEBUG")
    log.debug("visible after")
    out = buf.getvalue()
    assert "hidden before" not in out
    assert "visible after" in out


@pytest.mark.usefixtures("clean_logging")
def test_setup_logging_level_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no explicit level, FEMFORGE_LOG_LEVEL wins over the INFO default."""
    monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")
    console, buf = _console()
    setup_logging(console=console)
    log.debug("env level applied")
    assert "env level applied" in buf.getvalue()


@pytest.mark.usefixtures("clean_logging")
def test_setup_logging_default_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a level argument or env var the sink defaults to INFO."""
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    console, buf = _console()
    setup_logging(console=console)
    log.debug("filtered")
    log.info("passed")
    out = buf.getvalue()
    assert "filtered" not in out
    assert "passed" in out


@pytest.mark.usefixtures("clean_logging")
def test_first_log_call_auto_installs_terminal_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    """Logging without ever calling setup_logging installs the terminal sink with defaults."""
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    console, buf = _console()
    monkeypatch.setattr("femforge.common.log._console", console)
    log.info("auto configured")
    log.debug("below default level")
    out = buf.getvalue()
    assert "auto configured" in out
    assert "below default level" not in out


@pytest.mark.usefixtures("clean_logging")
def test_run_log_context_auto_setup_keeps_file_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """run_log_context before any setup triggers it first, so its file sink survives."""
    console, buf = _console()
    monkeypatch.setattr("femforge.common.log._console", console)
    with run_log_context("runA", tmp_path):
        log.info("both sinks")
    assert "both sinks" in buf.getvalue()
    assert "both sinks" in (tmp_path / "run.log").read_text(encoding="utf-8")


@pytest.mark.usefixtures("clean_logging")
def test_terminal_sink_hides_run_id() -> None:
    """The terminal sink suppresses the run_id extra while other extras still render."""
    console, buf = _console()
    setup_logging(level="INFO", console=console)
    log.bind(run_id="abc123", step="mesh").info("busy")
    out = buf.getvalue()
    assert "run_id:" not in out
    assert "abc123" not in out
    assert "step: 'mesh'" in out


@pytest.mark.usefixtures("clean_logging")
def test_stdlib_logging_intercepted() -> None:
    """Stdlib records route through the intercept handler into the loguru sink."""
    console, buf = _console()
    setup_logging(level="DEBUG", console=console)
    logging.getLogger("stdlib.test").warning("from stdlib")
    assert "from stdlib" in buf.getvalue()


@pytest.mark.usefixtures("clean_logging")
def test_stdlib_numeric_level_intercepted() -> None:
    """A level number unknown to loguru falls back to the numeric level."""
    console, buf = _console()
    setup_logging(level="DEBUG", console=console)
    logging.getLogger("stdlib.test").log(33, "odd level")
    assert "odd level" in buf.getvalue()


@pytest.mark.usefixtures("clean_logging")
def test_stdlib_exception_intercepted() -> None:
    """A stdlib record with exc_info renders its traceback through the sink."""
    console, buf = _console()
    setup_logging(level="DEBUG", console=console)
    try:
        raise ValueError("stdlib exc")
    except ValueError:
        logging.getLogger("stdlib.test").exception("stdlib failure")
    out = buf.getvalue()
    assert "stdlib failure" in out
    assert "ValueError" in out


@pytest.mark.usefixtures("clean_logging")
def test_stdlib_empty_exc_info_intercepted() -> None:
    """exc_info=True outside an except block yields (None, None, None) and must not crash loguru."""
    console, buf = _console()
    setup_logging(level="DEBUG", console=console)
    logging.getLogger("stdlib.test").warning("no active exception", exc_info=True)
    assert "no active exception" in buf.getvalue()


@pytest.mark.usefixtures("clean_logging")
def test_default_library_levels_gate_build123d() -> None:
    """build123d INFO is gated at its stdlib logger even with a DEBUG sink; WARNING still passes."""
    console, buf = _console()
    setup_logging(level="DEBUG", console=console)

    logging.getLogger("build123d.build_common").info("builder chatter")
    logging.getLogger("build123d").warning("builder problem")

    out = buf.getvalue()
    assert "builder chatter" not in out
    assert "builder problem" in out


@pytest.mark.usefixtures("clean_logging")
def test_library_levels_override_map() -> None:
    """A custom library_levels map replaces the default gates for that call."""
    console, buf = _console()
    setup_logging(level="DEBUG", console=console, library_levels={"noisy.lib": "ERROR"})

    logging.getLogger("noisy.lib").warning("suppressed")
    logging.getLogger("noisy.lib").error("kept")

    out = buf.getvalue()
    assert "suppressed" not in out
    assert "kept" in out


@pytest.mark.usefixtures("clean_logging")
def test_repeat_setup_applies_new_library_levels() -> None:
    """A repeat setup_logging call can extend the library gates."""
    console, buf = _console()
    setup_logging(level="DEBUG", console=console)

    logging.getLogger("late.lib").info("before the gate")
    setup_logging(level="DEBUG", library_levels={"late.lib": "WARNING"})
    logging.getLogger("late.lib").info("after the gate")

    out = buf.getvalue()
    assert "before the gate" in out
    assert "after the gate" not in out


@pytest.mark.usefixtures("clean_logging")
def test_gate_libraries_sets_stdlib_levels_without_touching_the_sinks() -> None:
    """gate_libraries silences a logger at its source whether or not setup_logging ran, and installs no sink."""
    gate_libraries({"chatty.lib": "WARNING"})
    assert logging.getLogger("chatty.lib").level == logging.WARNING

    console, buf = _console()
    setup_logging(level="DEBUG", console=console)
    logging.getLogger("chatty.lib").info("dropped")
    logging.getLogger("chatty.lib").warning("kept")

    assert "dropped" not in buf.getvalue() and "kept" in buf.getvalue()
    _reset_for_tests()
    assert logging.getLogger("chatty.lib").level == logging.NOTSET


@pytest.mark.usefixtures("clean_logging")
def test_reset_clears_library_levels() -> None:
    """_reset_for_tests returns gated loggers to NOTSET."""
    setup_logging(level="DEBUG", console=_console()[0])
    assert logging.getLogger("build123d").level == logging.WARNING
    _reset_for_tests()
    assert logging.getLogger("build123d").level == logging.NOTSET


@pytest.mark.usefixtures("clean_logging")
def test_capture_warnings_routed() -> None:
    """warnings.warn is captured into logging and lands in the loguru sink."""
    console, buf = _console()
    setup_logging(level="INFO", console=console)
    warnings.simplefilter("always")
    warnings.warn("scary warning", UserWarning, stacklevel=1)
    assert "scary warning" in buf.getvalue()


def test_run_log_context_writes_filtered_log(tmp_path: Path) -> None:
    """run.log receives this run's records only, and the sink is removed on exit."""
    with run_log_context("run123", tmp_path):
        log.info("inside the run")
        log.bind(run_id="other").info("foreign run")
    log_file = tmp_path / "run.log"
    text = log_file.read_text(encoding="utf-8")
    assert "inside the run" in text
    assert "foreign run" not in text
    with log.contextualize(run_id="run123"):
        log.info("after close")
    assert log_file.read_text(encoding="utf-8") == text


def test_run_log_context_removes_sink_on_error(tmp_path: Path) -> None:
    """The file sink is removed even when the body raises."""
    with pytest.raises(RuntimeError, match="boom"):
        with run_log_context("run456", tmp_path):
            log.info("before the crash")
            raise RuntimeError("boom")
    text = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "before the crash" in text
    with log.contextualize(run_id="run456"):
        log.info("too late")
    assert (tmp_path / "run.log").read_text(encoding="utf-8") == text


@pytest.mark.usefixtures("clean_logging")
def test_get_console_singleton() -> None:
    """get_console returns one lazily created stderr Console."""
    first = get_console()
    assert first is get_console()
    assert first.stderr


@pytest.mark.usefixtures("clean_logging")
def test_log_to_stdout_json_writes_each_record_as_one_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    log_to_stdout_json()

    log.bind(step=3, path=Path("model.step")).warning("Entity {} is unnamed.")
    logging.getLogger("femforge.probe").info("from stdlib")

    records: list[RelayedRecord] = [cast("RelayedRecord", json.loads(line)) for line in capsys.readouterr().out.splitlines()]
    assert [(record["level"], record["message"]) for record in records] == [("WARNING", "Entity {} is unnamed."), ("INFO", "from stdlib")]
    assert records[0]["extra"] == {"step": 3, "path": "model.step"}
    assert records[0]["function"] == "test_log_to_stdout_json_writes_each_record_as_one_json_line"


@pytest.mark.usefixtures("clean_logging")
def test_relay_record_logs_a_child_record_at_its_level_and_origin_and_other_lines_at_debug() -> None:
    console, _ = _console()
    setup_logging(level="DEBUG", console=console)
    relayed: list[Record] = []

    def keep(message: Message) -> None:
        relayed.append(message.record)

    sink_id: int = log.add(keep, level="DEBUG")
    child: RelayedRecord = {
        "level": "WARNING",
        "message": "Entity {} is unnamed.",
        "name": "femforge.meshing.mesher",
        "function": "generate",
        "line": 120,
        "extra": {"unnamed": [3]},
    }

    try:
        relay_record(json.dumps(child) + "\n", worker="mesh")
        relay_record("Traceback (most recent call last):\r\n", worker="mesh")
        relay_record("   \n", worker="mesh")
    finally:
        log.remove(sink_id)

    assert [(record["level"].name, record["message"]) for record in relayed] == [("WARNING", "Entity {} is unnamed."), ("DEBUG", "Traceback (most recent call last):")]
    assert (relayed[0]["name"], relayed[0]["function"], relayed[0]["line"]) == ("femforge.meshing.mesher", "generate", 120)
    assert relayed[0]["extra"] == {"unnamed": [3], "worker": "mesh"}
    assert relayed[1]["extra"] == {"worker": "mesh"}
