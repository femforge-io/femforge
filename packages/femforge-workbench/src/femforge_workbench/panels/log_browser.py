"""The log browser: the telemetry log frame narrowed in polars, and a live mode following this process's tap and every other process's journal."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, cast

import polars as pl
from trame.widgets import client, html, react

from femforge.common.log import LogLevel, log, telemetry_sink
from femforge.telemetry.hive import coerce
from femforge.telemetry.journal import journal_glob
from femforge.telemetry.paths import Telemetry, TelemetryDir
from femforge.telemetry.schemas import LOG_SCHEMA, LogColumn
from femforge.telemetry.sink import LogQuery, scan_logs
from femforge.telemetry.tail import TextTail
from femforge.telemetry.tap import LiveLogTap, LogRow
from femforge_workbench.feeds import Feed, QueueFeed
from femforge_workbench.grid import Row
from femforge_workbench.panels import Cls, PanelComponent, chooser, labelled
from femforge_workbench.panels.artifacts import ANY, as_written
from femforge_workbench.panels.outputs import EDITOR_OPTIONS
from femforge_workbench.panels.table import TablePanel
from femforge_workbench.widgets.code import Code
from femforge_workbench.widgets.input import Input
from femforge_workbench.widgets.switch import Switch


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


PERIOD_S = 1.0
LIVE_ROWS = 2000
"""How many of the newest records live mode keeps and shows."""

DEFAULT_LIMIT = 500
LIMITS = (200, 500, 2000)
TABLE_SUFFIX = "records"
TIME_FORMAT = "%m-%d %H:%M:%S%.3f"
JSON_LANGUAGE = "json"
ENTRY_WIDTH = "h-6 w-28"
SHOWN: tuple[LogColumn, ...] = (LogColumn.TIME, LogColumn.LEVEL, LogColumn.PROCESS, LogColumn.MESSAGE, LogColumn.RUN_ID, LogColumn.SWEEP_ID, LogColumn.POINT_KEY, LogColumn.HOST, LogColumn.PID)
"""The table's leading columns; the rest ride hidden so a row click can show them."""

HIDDEN: tuple[LogColumn, ...] = (LogColumn.DATE, LogColumn.LEVEL_NO, LogColumn.NAME, LogColumn.FUNCTION, LogColumn.LINE, LogColumn.THREAD, LogColumn.EXTRA, LogColumn.EXCEPTION)
IDENTITY: tuple[LogColumn, ...] = (LogColumn.TIME, LogColumn.HOST, LogColumn.PID, LogColumn.MESSAGE)
"""What tells one record from another when the tap and the journals both carry it."""


class LogVar(StrEnum):
    """The log browser's per-panel state variables."""

    LEVEL = "level"
    PROCESS = "process"
    RUN_ID = "run_id"
    SWEEP_ID = "sweep_id"
    SINCE = "since"
    UNTIL = "until"
    TEXT = "text"
    LIMIT = "limit"
    LIVE = "live"
    COUNT = "count"
    DETAIL = "detail"
    EDITOR = "editor"


FILTER_VARS: tuple[LogVar, ...] = (LogVar.LEVEL, LogVar.PROCESS, LogVar.RUN_ID, LogVar.SWEEP_ID, LogVar.SINCE, LogVar.UNTIL, LogVar.TEXT, LogVar.LIMIT, LogVar.LIVE)
"""The variables whose change re-queries."""


def parse_moment(text: str) -> datetime | None:
    """An ISO-8601 timestamp typed into a filter, read as UTC when it names no zone; None for anything else."""
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        return None

    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class LogFilters:
    """What the toolbar narrows the log frame by."""

    levels: tuple[str, ...] = ()
    processes: tuple[str, ...] = ()
    run_id: str = ""
    sweep_id: str = ""
    since: datetime | None = None
    until: datetime | None = None
    text: str = ""
    limit: int = DEFAULT_LIMIT

    def apply(self, query: LogQuery) -> LogQuery:
        """The query narrowed by every active filter."""
        narrowed = query
        if self.levels:
            narrowed = narrowed.levels(*self.levels)

        if self.processes:
            narrowed = narrowed.processes(*self.processes)

        if self.run_id:
            narrowed = narrowed.run(self.run_id)

        if self.sweep_id:
            narrowed = narrowed.sweep(self.sweep_id)

        if self.since is not None:
            narrowed = narrowed.since(self.since)

        if self.until is not None:
            narrowed = narrowed.until(self.until)

        if self.text:
            narrowed = narrowed.text(self.text)

        return narrowed

    def rows(self, frame: pl.DataFrame, limit: int) -> pl.DataFrame:
        """The newest ``limit`` rows of an in-memory frame the filters keep, oldest first."""
        return self.apply(LogQuery(frame.lazy())).tail(limit)


def rows_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    """Log records (journal lines parsed, or the tap's rows) as a typed frame."""
    if not rows:
        return pl.DataFrame(schema=LOG_SCHEMA)

    return coerce(pl.from_dicts(rows, infer_schema_length=None).lazy(), LOG_SCHEMA).collect()


def journal_rows(lines: list[str]) -> pl.DataFrame:
    """The records among journal lines; a line that is not a JSON object is skipped."""
    rows: list[dict[str, object]] = []
    for line in lines:
        try:
            payload = cast("object", json.loads(line))
        except ValueError:
            continue

        if not isinstance(payload, dict):
            continue

        rows.append(cast("dict[str, object]", payload))

    return rows_frame(rows)


def display_rows(frame: pl.DataFrame) -> pl.DataFrame:
    """The table frame: the time as a clock reading, every other column as it is."""
    return frame.with_columns(pl.col(LogColumn.TIME).dt.strftime(TIME_FORMAT).alias(LogColumn.TIME))


def detail_json(row: Row) -> str:
    """One record's bound extras and its traceback, as the JSON the viewer shows."""
    extra_text = str(row.get(LogColumn.EXTRA) or "")
    extra: object = extra_text
    if extra_text:
        try:
            extra = cast("object", json.loads(extra_text))
        except ValueError:
            extra = extra_text

    exception = str(row.get(LogColumn.EXCEPTION) or "")

    return json.dumps({LogColumn.EXTRA: extra, LogColumn.EXCEPTION: exception.splitlines()}, indent=2)


def own_journal() -> str:
    """The filename of this process's own log journal, which the tap already covers; empty when it writes none."""
    sink = telemetry_sink()

    return sink.journal.path.name if sink is not None else ""


def line_time(line: str) -> datetime | None:
    """The record time a journal line carries, None when the line is not a record."""
    try:
        payload = cast("object", json.loads(line))
    except ValueError:
        return None

    if not isinstance(payload, dict):
        return None

    raw = cast("dict[str, object]", payload).get(LogColumn.TIME)
    if not isinstance(raw, str):
        return None

    return parse_moment(raw)


def last_line_time(path: Path) -> datetime | None:
    """The time of a journal's last record, None for an empty or absent journal."""
    if not path.is_file():
        return None

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not lines:
        return None

    return line_time(lines[-1])


def journal_identity(path: Path) -> tuple[str, int] | None:
    """The (host, pid) a log journal's name carries, None for a name that is not one."""
    prefix = f"{TelemetryDir.LOGS}-"
    if not path.stem.startswith(prefix):
        return None

    host, _, pid = path.stem[len(prefix) :].rpartition("-")
    if not host or not pid.isdigit():
        return None

    return host, int(pid)


@dataclass(frozen=True)
class Folded:
    """A journal that vanished between two reads: its process checkpointed and closed it, so its last lines are only in the store."""

    host: str
    pid: int
    since: datetime | None
    """The time of the last line read from it, None when none was."""


class JournalTails:
    """A tail on every other process's live log journal, journals that appear later picked up on the next read and journals that vanish reported once."""

    def __init__(self, live: Path, *, skip: str) -> None:
        """Tails the journals under `live`, all but the one named `skip`."""
        self.live = live
        self.skip = skip
        self.tails: dict[Path, TextTail] = {}
        self.seen: dict[Path, datetime | None] = {}
        """The time of the last line read from each journal, where a fold's recovery starts."""

    def journals(self) -> list[Path]:
        """The live log journals to tail, without the skipped one."""
        if not self.live.is_dir():
            return []

        return [path for path in sorted(self.live.glob(journal_glob(TelemetryDir.LOGS))) if path.name != self.skip]

    def seek_end(self) -> None:
        """Forgets every cursor and starts each journal at its current end, so a seeded backlog is not read twice."""
        self.tails = {}
        self.seen = {}
        for path in self.journals():
            tail = TextTail(path)
            tail.offset = path.stat().st_size
            self.tails[path] = tail
            self.seen[path] = last_line_time(path)

    def read_new(self) -> list[str]:
        """Every complete line appended to any journal since the last read."""
        lines: list[str] = []
        for path in self.journals():
            tail = self.tails.get(path)
            if tail is None:
                tail = TextTail(path)
                self.tails[path] = tail

            fresh = tail.read_new()
            if fresh:
                self.seen[path] = line_time(fresh[-1]) or self.seen.get(path)

            lines.extend(fresh)

        return lines

    def folded(self) -> list[Folded]:
        """The tailed journals that vanished since the last read, each forgotten once reported."""
        gone = [path for path in self.tails if not path.is_file()]

        reported: list[Folded] = []
        for path in gone:
            del self.tails[path]
            since = self.seen.pop(path, None)
            identity = journal_identity(path)
            if identity is None:
                continue

            host, pid = identity
            reported.append(Folded(host, pid, since))

        return reported


class LogBrowserPanel(PanelComponent):
    """Filters the log store in a table; live mode pins it to the tail as records arrive from the tap and the journals."""

    def __init__(self, app: "Workbench", id: str, title: str, *, telemetry: Telemetry, tap: LiveLogTap | None = None) -> None:
        """Builds the browser over a telemetry root's log store and adds the tap to loguru.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            telemetry: The telemetry root whose log store and live journals the browser reads.
            tap: The loguru sink that carries this process's records live; None makes one.
        """
        self.telemetry = telemetry
        self.tap = tap if tap is not None else LiveLogTap()
        self.filters = LogFilters()
        self.visible = False
        self.live_mode = False
        self.live: pl.DataFrame = pl.DataFrame(schema=LOG_SCHEMA)
        self.journals = JournalTails(telemetry.live, skip=own_journal())
        # Its own template, hosted inside this panel's; built outside the layout so its root is not also a child here.
        self.table = TablePanel(
            app,
            f"{id}-{TABLE_SUFFIX}",
            "Records",
            display_rows(self.live),
            filterable=True,
            virtual=True,
            lead=SHOWN,
            hidden=HIDDEN,
            pick=self.pick,
        )
        self.feed: Feed[pl.DataFrame | None] = Feed(self, PERIOD_S, self.poll, self.append)
        self.stream: QueueFeed[LogRow] = QueueFeed(self, self.tap.subscribe, self.tap.unsubscribe, self.on_tap)
        self.sink_id = log.add(self.tap, format="{message}", colorize=False, level=LogLevel.DEBUG.value)

        super().__init__(app, id, title)

        _ = app.ctrl.on_server_exited.add(self.detach)

    def processes(self) -> list[str]:
        """Every process name the store holds, for the filter's choices."""
        found: pl.DataFrame = scan_logs(self.telemetry).select(LogColumn.PROCESS).drop_nulls().unique().sort(LogColumn.PROCESS).collect()

        return cast("list[str]", found[LogColumn.PROCESS].to_list())

    def build(self) -> None:
        """Builds the filters over the record table and the detail view, then listens to the filters."""

        def entry(name: LogVar, placeholder: str) -> None:
            Input(model=self.key(name), placeholder=placeholder, aria_label=placeholder, classes=ENTRY_WIDTH)

        with html.Div(classes=str(Cls.FILTERS)):
            with labelled("Level"):
                chooser(self.key(LogVar.LEVEL), [ANY, *(level.value for level in LogLevel)], width="w-28")

            with labelled("Process"):
                chooser(self.key(LogVar.PROCESS), [ANY, *self.processes()], width="w-40", label=as_written)

            entry(LogVar.RUN_ID, "run_id")
            entry(LogVar.SWEEP_ID, "sweep_id")
            entry(LogVar.TEXT, "text")
            entry(LogVar.SINCE, "since (ISO)")
            entry(LogVar.UNTIL, "until (ISO)")
            with labelled("Newest"):
                chooser(self.key(LogVar.LIMIT), [str(limit) for limit in LIMITS], width="w-20")

            with labelled("Live"):
                Switch(model=self.key(LogVar.LIVE), size="sm")

            html.Span([react.Bind(self.key(LogVar.COUNT))], classes=str(Cls.STATUS))

        with html.Div(classes=str(Cls.COLUMN)):
            with html.Div(classes=str(Cls.FILL)):
                client.ServerTemplate(name=self.table.template_name)

            with html.Div(classes=str(Cls.PANE_ROW)), html.Div(classes=str(Cls.PANE)):
                Code(
                    value=react.Bind(self.key(LogVar.DETAIL)),
                    language=JSON_LANGUAGE,
                    options=react.Bind(self.key(LogVar.EDITOR)),
                    classes=str(Cls.FILL),
                )

        self.state.update(
            {
                self.key(LogVar.LEVEL): ANY,
                self.key(LogVar.PROCESS): ANY,
                self.key(LogVar.RUN_ID): "",
                self.key(LogVar.SWEEP_ID): "",
                self.key(LogVar.SINCE): "",
                self.key(LogVar.UNTIL): "",
                self.key(LogVar.TEXT): "",
                self.key(LogVar.LIMIT): str(DEFAULT_LIMIT),
                self.key(LogVar.LIVE): False,
                self.key(LogVar.COUNT): "",
                self.key(LogVar.DETAIL): "{}",
                self.key(LogVar.EDITOR): EDITOR_OPTIONS,
            }
        )
        _ = self.listen(*(self.key(name) for name in FILTER_VARS))(self.on_change)

    def read_filters(self) -> LogFilters:
        """The filters the toolbar currently describes."""

        def chosen(name: LogVar) -> tuple[str, ...]:
            value = str(self.state[self.key(name)] or ANY)

            return () if value == ANY else (value,)

        def typed(name: LogVar) -> str:
            return str(self.state[self.key(name)] or "").strip()

        return LogFilters(
            levels=chosen(LogVar.LEVEL),
            processes=chosen(LogVar.PROCESS),
            run_id=typed(LogVar.RUN_ID),
            sweep_id=typed(LogVar.SWEEP_ID),
            since=parse_moment(typed(LogVar.SINCE)),
            until=parse_moment(typed(LogVar.UNTIL)),
            text=typed(LogVar.TEXT),
            limit=int(typed(LogVar.LIMIT) or DEFAULT_LIMIT),
        )

    async def on_change(self, **_: object) -> None:
        """A filter or the live switch moved: live mode seeds its buffer or stops, then the rows are shown again."""
        self.filters = self.read_filters()
        live = bool(self.state[self.key(LogVar.LIVE)])
        if live != self.live_mode:
            self.live_mode = live
            if live:
                self.live = await asyncio.to_thread(self.seed)

            self.follow()

        await self.reload()

    async def reload(self) -> None:
        """Shows the rows the filters keep: from the live buffer, or from the store off the loop."""
        if self.live_mode:
            self.show(self.filters.rows(self.live, LIVE_ROWS))
            return

        try:
            frame = await asyncio.to_thread(self.query)
        except Exception as error:
            log.opt(exception=True).warning("Log query failed.")
            self.status(f"{type(error).__name__}: {error}")
            return

        self.show(frame)

    def query(self) -> pl.DataFrame:
        """The newest matching records in the store."""
        return self.filters.apply(LogQuery.over(self.telemetry)).tail(self.filters.limit)

    def seed(self) -> pl.DataFrame:
        """Live mode's starting buffer: the store's tail merged with the tap's memory, the journals then read from their ends."""
        stored = LogQuery.over(self.telemetry).tail(LIVE_ROWS)
        remembered = rows_frame(self.tap.snapshot())
        self.journals.seek_end()
        merged = pl.concat([stored, remembered]).unique(subset=list(IDENTITY), keep="first", maintain_order=True)

        return merged.sort(LogColumn.TIME).tail(LIVE_ROWS)

    def show(self, frame: pl.DataFrame) -> None:
        """Puts a frame in the table, pinned to its last row in live mode."""
        with self.state:
            self.table.set_rows(display_rows(frame))
            self.state[self.key(LogVar.COUNT)] = f"{frame.height} records"

        if self.live_mode:
            self.table.scroll_to_end()

    def recover(self, folded: Folded) -> pl.DataFrame:
        """A folded journal's records from the store, from the last line read on; the ones its process wrote after the last poll."""
        narrowed = (pl.col(LogColumn.HOST) == folded.host) & (pl.col(LogColumn.PID) == folded.pid)
        if folded.since is not None:
            narrowed = narrowed & (pl.col(LogColumn.TIME) >= folded.since)

        return LogQuery.over(self.telemetry).where(narrowed).tail(LIVE_ROWS)

    def poll(self) -> pl.DataFrame | None:
        """The records other processes journalled since the last poll, a journal folded meanwhile read back from the store; None when there are none."""
        fresh = journal_rows(self.journals.read_new())
        recovered = [self.recover(folded) for folded in self.journals.folded()]

        frame = pl.concat([fresh, *recovered])
        if frame.height == 0:
            return None

        return frame

    def append(self, frame: pl.DataFrame | None) -> None:
        """Adds records to the live buffer, once each and in time order, keeping the newest, and shows the buffer again."""
        if frame is None or frame.height == 0:
            return

        merged = pl.concat([self.live, frame]).unique(subset=list(IDENTITY), keep="first", maintain_order=True)
        self.live = merged.sort(LogColumn.TIME).tail(LIVE_ROWS)
        self.show(self.filters.rows(self.live, LIVE_ROWS))

    def on_tap(self, rows: list[LogRow]) -> None:
        """This process's own records, straight from the loguru sink."""
        self.append(rows_frame(rows))

    def pick(self, row: Row) -> None:
        """Shows a clicked record's extras and traceback."""
        with self.state:
            self.state[self.key(LogVar.DETAIL)] = detail_json(row)

    def follow(self) -> None:
        """Runs the live sources exactly while the panel is visible in live mode."""
        if self.visible and self.live_mode:
            self.feed.start()
            self.stream.start()
            return

        self.feed.stop()
        self.stream.stop()

    def on_visible(self, visible: bool) -> None:
        """Runs the live sources only while the panel is on screen in live mode."""
        self.visible = visible
        self.follow()

    def detach(self, **_: object) -> None:
        """Removes the tap from loguru and stops the live sources when the server exits."""
        self.visible = False
        self.follow()
        log.remove(self.sink_id)

    def dispose(self) -> None:
        """Removes the tap, then releases the hosted table and the panel."""
        self.ctrl.on_server_exited.discard(self.detach)
        self.detach()
        self.table.dispose()
        super().dispose()
