"""The only logging config site; every Rich output path (RichSink, StepTimer, tree prints) must share the one stderr Console from get_console()."""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import sys
import threading
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

from loguru import logger as _logger
from rich.console import Console

from femforge.common.files import BundleFile
from femforge.common.rich_sink import RichSink


if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from loguru import Message, Record
    from pydantic import TypeAdapter

    from femforge.telemetry.sink import ParquetLogSink


__all__ = [
    "LOG_LEVEL_ENV",
    "RELAYED_ROUTE",
    "TELEMETRY_ENV",
    "TELEMETRY_ROUTE",
    "LogKey",
    "LogLevel",
    "RelayedRecord",
    "emit_relayed",
    "get_console",
    "log",
    "log_to_stdout_json",
    "parse_relayed",
    "relay_record",
    "run_log_context",
    "setup_logging",
    "telemetry_scope",
    "telemetry_sink",
]

TELEMETRY_ENV = "FEMFORGE_TELEMETRY"
"""The env var naming the telemetry root; unset and unpassed means telemetry is off and polars is never imported."""

LOG_LEVEL_ENV = "FEMFORGE_LOG_LEVEL"
"""The env var setting the terminal log level when ``setup_logging`` gets none."""

TELEMETRY_ROUTE = "telemetry_root"
"""The bound key routing a record to the telemetry root of the [`telemetry_scope`][femforge.common.log.telemetry_scope] it was logged in."""

RELAYED_ROUTE = "relayed"
"""A ``TELEMETRY_ROUTE`` value no sink stores: a relayed record was already stored by the process that logged it."""


class LogKey(StrEnum):
    """Bound keys that the code logging a record and the code reading it back must agree on."""

    RUN_ID = "run_id"
    """The run a record belongs to."""

    RUN_DIR = "run_dir"
    """The run's bundle directory."""

    STAGE = "stage"
    """The pipeline stage a stage record starts or ends."""

    PROGRESS = "progress"
    """The share of a solve's convergence covered so far, between 0 and 1."""

    SOLVER = "solver"
    """The solver a convergence or progress record reports on."""

    ORIGIN = "origin"
    """Where a reused bundle came from."""

    TASK = "task"
    """The workbench task whose child process logged a relayed record."""


class LogLevel(StrEnum):
    """Loguru severity names accepted by setup_logging's level and library_levels arguments."""

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# Chatty third-party stdlib loggers, gated at the source so their records never reach the intercept bridge
# (and thus neither the terminal nor run.log) regardless of the sink level.
DEFAULT_LIBRARY_LEVELS: dict[str, LogLevel] = {"build123d": LogLevel.WARNING}

# The terminal sink's fixed name:function column width; long paths are left-truncated to keep lines aligned.
_PATH_WIDTH = 36

_console: Console | None = None
_terminal: tuple[RichSink, int] | None = None
_terminal_level: str | None = None
_library_levels: dict[str, str] = {}
_telemetry: tuple[ParquetLogSink, int] | None = None
_telemetry_root: Path | None = None
_telemetry_process: str | None = None
_scoped_telemetry: dict[str, tuple[ParquetLogSink, int]] = {}
_scoped_lock: threading.Lock = threading.Lock()
_json_sink: int | None = None
_setup_lock: threading.RLock = threading.RLock()


class RelayedRecord(TypedDict):
    """One log record as the JSON line a child process writes to stdout for its parent to relay."""

    level: str
    """The loguru level name."""

    message: str
    """The formatted message."""

    name: str
    """The module that logged it."""

    function: str
    """The function that logged it."""

    line: int
    """The source line that logged it."""

    extra: dict[str, object]
    """The bound fields, with values JSON cannot hold written as strings."""


def get_console() -> Console:
    """Return the process-wide stderr Console shared by logging and all progress/tree rendering."""
    global _console
    if _console is None:
        _console = Console(stderr=True)
    return _console


class _InterceptHandler(logging.Handler):
    """Stdlib logging handler that forwards every record into loguru with caller depth corrected."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        exc = record.exc_info
        if exc is not None and exc[0] is None:
            exc = None
        _logger.opt(depth=depth, exception=exc).log(level, record.getMessage())


def _add_terminal_sink(sink: RichSink, level: str) -> int:
    return _logger.add(
        sink,
        format="{message}",
        colorize=False,
        backtrace=True,
        diagnose=True,
        level=level,
    )


def _apply_library_levels(levels: Mapping[str, str]) -> None:
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)
        _library_levels[name] = level


def gate_libraries(levels: Mapping[str, LogLevel | str]) -> None:
    """Gates stdlib loggers at the source, leaving the sinks and the telemetry root as they are."""
    _apply_library_levels(levels)


def telemetry_sink() -> ParquetLogSink | None:
    """The installed parquet sink, or None when this process has no telemetry root."""
    return _telemetry[0] if _telemetry is not None else None


def _route(root: Path) -> str:
    """The routing key of a telemetry root: its resolved path, so two spellings of one folder share a sink."""
    return str(root.resolve())


def _close_telemetry_sink() -> None:
    global _telemetry, _telemetry_root
    if _telemetry is not None:
        sink, sink_id = _telemetry
        _logger.remove(sink_id)
        sink.close()

    _telemetry = None
    _telemetry_root = None


def _close_scoped_telemetry_sinks() -> None:
    with _scoped_lock:
        closing = list(_scoped_telemetry.values())
        _scoped_telemetry.clear()

    for sink, sink_id in closing:
        _logger.remove(sink_id)
        sink.close()


def _install_telemetry(explicit: Path | None, process: str | None) -> None:
    """Point the parquet sink at the resolved telemetry root, swapping the sink when the root moves; a no-op when telemetry is off."""
    global _telemetry, _telemetry_root, _telemetry_process
    if process is not None:
        _telemetry_process = process
        open_sinks: list[ParquetLogSink] = [sink for sink, _ in _scoped_telemetry.values()] + ([_telemetry[0]] if _telemetry is not None else [])

        # Open sinks only exist once femforge.telemetry is imported, so this import is never the first.
        if open_sinks:
            from femforge.telemetry.sink import sanitize_process

            for sink in open_sinks:
                sink.process = sanitize_process(process)

    if explicit is None and not os.environ.get(TELEMETRY_ENV):
        return

    # Imported here so femforge.telemetry (which logs, and pulls in polars) can depend on this module without a cycle.
    from femforge.telemetry.paths import Telemetry, resolve_root
    from femforge.telemetry.sink import ParquetLogSink

    root = resolve_root(explicit)
    if root is None or root == _telemetry_root:
        return

    _close_telemetry_sink()
    route: str = _route(root)

    # One sink per folder: a scope that already writes this root hands it over to the default sink.
    with _scoped_lock:
        scoped = _scoped_telemetry.pop(route, None)

    if scoped is not None:
        _logger.remove(scoped[1])
        scoped[0].close()

    def unrouted_or_mine(record: Record) -> bool:
        target: object = record["extra"].get(TELEMETRY_ROUTE)
        return target is None or target == route

    # DEBUG regardless of the terminal level: the parquet store is the searchable record, the terminal only the live view.
    sink = ParquetLogSink(Telemetry(root), process=process)
    sink_id = _logger.add(
        sink,
        format="{message}",
        colorize=False,
        level=LogLevel.DEBUG,
        filter=unrouted_or_mine,
    )

    _telemetry = (sink, sink_id)
    _telemetry_root = root


def setup_logging(
    *,
    level: LogLevel | str | None = None,
    console: Console | None = None,
    library_levels: Mapping[str, LogLevel | str] | None = None,
    telemetry: Path | None = None,
    process: str | None = None,
) -> None:
    """Install the terminal RichSink plus stdlib/warnings interception; repeat calls only adjust the sink level, library gates and telemetry root.

    Args:
        level: Terminal sink threshold; ``None`` falls back to the ``FEMFORGE_LOG_LEVEL`` env var, then ``INFO``.
        console: Console the sink renders into; ``None`` uses the shared stderr console from ``get_console()``.
        library_levels: Stdlib logger name to minimum level, gating chatty libraries at the source; ``None`` applies ``DEFAULT_LIBRARY_LEVELS``.
        telemetry: Telemetry root the parquet sink writes to; ``None`` falls back to ``FEMFORGE_TELEMETRY``, and telemetry stays off when that is unset too.
        process: Name the telemetry rows are partitioned under; ``None`` uses the sanitized ``sys.argv[0]`` stem.
    """
    global _terminal, _terminal_level
    resolved = (level or os.environ.get(LOG_LEVEL_ENV) or "INFO").upper()

    if _terminal is not None:
        if resolved != _terminal_level:
            sink, sink_id = _terminal
            _logger.remove(sink_id)
            _terminal = (sink, _add_terminal_sink(sink, resolved))
            _terminal_level = resolved
        _apply_library_levels(library_levels if library_levels is not None else DEFAULT_LIBRARY_LEVELS)
        _install_telemetry(telemetry, process)

        return

    sink = RichSink(
        console if console is not None else get_console(),
        path_width=_PATH_WIDTH,
        hide_keys=frozenset({LogKey.RUN_ID, LogKey.RUN_DIR, LogKey.STAGE, LogKey.PROGRESS, TELEMETRY_ROUTE}),
    )
    _logger.remove()
    _terminal = (sink, _add_terminal_sink(sink, resolved))
    _terminal_level = resolved

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    logging.captureWarnings(True)
    _apply_library_levels(library_levels if library_levels is not None else DEFAULT_LIBRARY_LEVELS)

    _logger.level(LogLevel.TRACE, icon="🔍")
    _logger.level(LogLevel.DEBUG, icon="🐛")
    _logger.level(LogLevel.INFO, icon="ℹ️")
    _logger.level(LogLevel.SUCCESS, icon="✅")
    _logger.level(LogLevel.WARNING, icon="⚠️")
    _logger.level(LogLevel.ERROR, icon="❌")
    _logger.level(LogLevel.CRITICAL, icon="🚨")

    # After _logger.remove(), which would otherwise drop the sink again.
    _install_telemetry(telemetry, process)


def _ensure_setup() -> None:
    if _terminal is not None or _json_sink is not None:
        return

    # Two threads logging first at once would otherwise both set up, leaving two terminal sinks.
    with _setup_lock:
        if _terminal is None and _json_sink is None:
            setup_logging()


def _write_json_record(message: Message) -> None:
    record: Record = message.record
    relayed: RelayedRecord = {
        "level": record["level"].name,
        "message": record["message"],
        "name": record["name"] or "",
        "function": record["function"],
        "line": record["line"],
        "extra": dict(record["extra"]),
    }
    _ = sys.stdout.write(json.dumps(relayed, default=str) + "\n")
    sys.stdout.flush()


def log_to_stdout_json(level: LogLevel | str = LogLevel.DEBUG, *, process: str | None = None) -> None:
    """Write every record to stdout as one JSON line for a parent to relay with [`relay_record`][femforge.common.log.relay_record]; ``FEMFORGE_TELEMETRY`` still stores them, under ``process``."""
    global _json_sink
    _close_telemetry_sink()
    _logger.remove()
    _json_sink = _logger.add(_write_json_record, level=level, format="{message}")

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    logging.captureWarnings(True)
    _apply_library_levels(DEFAULT_LIBRARY_LEVELS)
    _install_telemetry(None, process)


@functools.cache
def _relayed_records() -> TypeAdapter[RelayedRecord]:
    from pydantic import TypeAdapter

    return TypeAdapter(RelayedRecord)


def parse_relayed(line: str) -> RelayedRecord | None:
    """The [`log_to_stdout_json`][femforge.common.log.log_to_stdout_json] record a child-process output line holds; None for any other line."""
    try:
        return _relayed_records().validate_json(line.rstrip("\r\n"))
    except ValueError:
        return None


def emit_relayed(record: RelayedRecord, **bound: object) -> None:
    """Log a child's record in this process at its own level and origin, with the ``bound`` fields over its own."""

    def origin(relayed: Record) -> None:
        relayed["name"] = record["name"]
        relayed["function"] = record["function"]
        relayed["line"] = record["line"]

    log.patch(origin).bind(**{**record["extra"], **bound}).log(record["level"], record["message"])


def relay_record(line: str, **bound: object) -> None:
    """Log one child-process output line with the ``bound`` fields: a [`log_to_stdout_json`][femforge.common.log.log_to_stdout_json] record at its own level and origin, other lines at DEBUG."""
    text: str = line.rstrip("\r\n")
    if not text.strip():
        return

    record: RelayedRecord | None = parse_relayed(text)
    if record is None:
        log.bind(**bound).debug(text)
        return

    emit_relayed(record, **bound)


class _AutoSetupLogger:
    """Attribute-forwarding wrapper around the loguru logger that runs setup_logging() before the first record."""

    __slots__ = ()

    def __getattr__(self, name: str) -> object:
        _ensure_setup()
        attr: object = getattr(_logger, name)  # pyright: ignore[reportAny]
        return attr


# Type checkers see the real loguru Logger API; at runtime the wrapper guarantees a script that never
# calls setup_logging still gets the RichSink terminal instead of loguru's bare default.
if TYPE_CHECKING:
    log = _logger
    """The femforge logger: loguru's API, with [`setup_logging`][femforge.common.log.setup_logging] run before its first record."""
else:
    log = _AutoSetupLogger()


@contextmanager
def telemetry_scope(root: str | Path | None) -> Iterator[None]:
    """Route the records logged inside to ``root``'s log store instead of the process default; ``None`` leaves routing alone."""
    if root is None:
        yield
        return

    # Imported here so femforge.telemetry (which logs, and pulls in polars) can depend on this module without a cycle.
    from femforge.telemetry.paths import Telemetry
    from femforge.telemetry.sink import ParquetLogSink

    _ensure_setup()
    folder: Path = Path(root)
    route: str = _route(folder)

    def routed_here(record: Record) -> bool:
        target: object = record["extra"].get(TELEMETRY_ROUTE)
        return target == route

    # One sink per folder for the life of the process (closed at exit): the default sink when it already writes this root.
    with _scoped_lock:
        owned_by_default: bool = _telemetry_root is not None and _route(_telemetry_root) == route

        if not owned_by_default and route not in _scoped_telemetry:
            sink = ParquetLogSink(Telemetry(folder), process=_telemetry_process)
            sink_id = _logger.add(
                sink,
                format="{message}",
                colorize=False,
                level=LogLevel.DEBUG,
                filter=routed_here,
            )
            _scoped_telemetry[route] = (sink, sink_id)

    with _logger.contextualize(**{TELEMETRY_ROUTE: route}):
        yield


@contextmanager
def run_log_context(run_id: str, run_dir: Path) -> Iterator[None]:
    """Bind ``run_id`` into the log context and write every record at DEBUG or above that carries it to ``run_dir/run.log``."""
    # Setup must precede the file sink: a first-time setup_logging() calls _logger.remove(), which
    # would silently drop a file sink added before it.
    _ensure_setup()

    def _same_run(record: Record) -> bool:
        bound: object = record["extra"].get(LogKey.RUN_ID)
        return bound == run_id

    # Explicit format: loguru's default omits {extra}, and the run_id (plus any future worker/batch ids) must
    # survive in the file for post-hoc aggregation across runs. The telemetry route is a local path, and bundles are published.
    def line(record: Record) -> str:
        bound: dict[str, object] = cast("dict[str, object]", record["extra"])
        shown: dict[str, object] = {key: value for key, value in bound.items() if key != TELEMETRY_ROUTE}
        literal: str = repr(shown).replace("{", "{{").replace("}", "}}")
        return "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message} | " + literal + "\n{exception}"

    sink_id = _logger.add(
        run_dir / BundleFile.RUN_LOG,
        level=LogLevel.DEBUG,
        encoding="utf-8",
        filter=_same_run,
        format=line,
    )
    try:
        with _logger.contextualize(**{str(LogKey.RUN_ID): run_id}):
            yield
    finally:
        _logger.remove(sink_id)


def _reset_for_tests() -> None:
    """Undo setup_logging's and log_to_stdout_json's process-global effects so tests can exercise them repeatedly in any order."""
    global _console, _terminal, _terminal_level, _telemetry_process, _json_sink
    _close_telemetry_sink()
    _close_scoped_telemetry_sinks()
    _telemetry_process = None

    if _terminal is not None or _json_sink is not None:
        _logger.remove()
        _logger.add(sys.stderr)
    _console = None
    _terminal = None
    _terminal_level = None
    _json_sink = None

    for name in _library_levels:
        logging.getLogger(name).setLevel(logging.NOTSET)
    _library_levels.clear()

    logging.captureWarnings(False)
