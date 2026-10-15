"""The sweep monitor: a sweep's progress, its points grouped by state with dead workers marked stalled, and the failures with their tracebacks."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, cast

import polars as pl
import psutil
from trame.widgets import client, html, react

from femforge.telemetry.hive import HOST
from femforge.telemetry.paths import Telemetry
from femforge.telemetry.schemas import SWEEP_EVENT_SCHEMA, SweepColumn, SweepEventColumn, SweepState
from femforge.telemetry.sweep import SweepQuery, SweepSummary, sweep_runs
from femforge_workbench.app import guard
from femforge_workbench.feeds import Feed
from femforge_workbench.grid import Row, group_rows
from femforge_workbench.panels import Cls, ItemField, PanelComponent, labelled, picker
from femforge_workbench.panels.outputs import EDITOR_OPTIONS
from femforge_workbench.panels.runs import format_wall_time
from femforge_workbench.panels.table import TablePanel
from femforge_workbench.widgets.code import Code
from femforge_workbench.widgets.progress import Progress


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


PERIOD_S = 2.0
TABLE_SUFFIX = "points"
STALLED = "stalled"
"""The state shown for a running point whose worker process is gone."""

NO_ETA = "—"
PICKER_WIDTH = "w-96"
CLOCK = "%H:%M:%S"
PLAIN_TEXT = "plaintext"
PERCENT = 100.0
BAR = "h-2 min-w-40 flex-1"
FAILURE = "failure"

type Alive = Callable[[int], bool]


class SweepVar(StrEnum):
    """The sweep monitor's per-panel state variables."""

    SWEEP = "sweep"
    ITEMS = "items"
    PERCENT = "percent"
    COUNTS = "counts"
    PACE = "pace"
    FAILURES = "failures"
    TRACEBACK = "traceback"
    EDITOR = "editor"


class PointRow(StrEnum):
    """The point table's columns; the state comes first so the group labels read from it."""

    STATE = "state"
    POINT = "point"
    KEY = "key"
    AT = "at"
    HOST = "host"
    PID = "pid"
    DURATION = "duration_s"
    ORIGIN = "origin"


class FailureField(StrEnum):
    """The keys of one failure list entry."""

    KEY = "key"
    NAME = "name"
    AT = "at"


POINT_SCHEMA = pl.Schema(
    {
        PointRow.STATE: pl.String,
        PointRow.POINT: pl.String,
        PointRow.KEY: pl.String,
        PointRow.AT: pl.String,
        PointRow.HOST: pl.String,
        PointRow.PID: pl.Int32,
        PointRow.DURATION: pl.Float64,
        PointRow.ORIGIN: pl.String,
    }
)
"""The table frame's columns, so an empty sweep still builds the same table."""

STATE_ORDER: tuple[str, ...] = (STALLED, SweepState.RUNNING, SweepState.QUEUED, SweepState.FAILED, SweepState.DONE, SweepState.SKIPPED)
"""The order the state groups are shown in: what needs attention first."""

RANK = "__rank"


@dataclass(frozen=True)
class Progression:
    """The progress strip: how far the sweep is and how fast it moves."""

    percent: float
    counts: str
    pace: str


@dataclass(frozen=True)
class SweepTick:
    """What one poll found."""

    items: list[Row]
    sweep_id: str = ""
    progress: Progression = Progression(0.0, "", "")
    points: pl.DataFrame = field(default_factory=lambda: group_rows(pl.DataFrame(schema=POINT_SCHEMA), PointRow.STATE))
    failures: list[Row] = field(default_factory=list)
    latest: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema=SWEEP_EVENT_SCHEMA))


def progress(summary: SweepSummary, total: int) -> Progression:
    """The settled share of ``total`` as a percentage, the per-state counts, and the rate, ETA and elapsed time as text."""
    settled = summary.done + summary.failed + summary.skipped
    # A journal that carries more settled points than the launch declared would otherwise slide the bar out of its box.
    percent = min(PERCENT, PERCENT * settled / total) if total > 0 else 0.0
    counts = f"{settled}/{total} settled · {summary.done} done · {summary.failed} failed · {summary.skipped} skipped · {summary.running} running · {summary.queued} queued"
    eta = format_wall_time(summary.eta_s) if summary.eta_s is not None else NO_ETA
    pace = f"{summary.rate_per_h:.1f}/h · ETA {eta} · elapsed {format_wall_time(summary.elapsed_s)}"

    return Progression(round(percent, 1), counts, pace)


def point_rows(latest: pl.DataFrame, *, alive: Alive = psutil.pid_exists, host: str = HOST) -> pl.DataFrame:
    """One table row per point grouped by state, a running point whose worker on this host is gone shown as stalled.

    Args:
        latest: Every point's last sweep event.
        alive: Whether a process id is alive on this host.
        host: This host's name; only its workers can be checked.
    """

    def state_of(row: dict[str, object]) -> str:
        state = str(row[SweepEventColumn.STATE])
        pid = row[SweepEventColumn.PID]
        if state == SweepState.RUNNING and row[SweepEventColumn.HOST] == host and isinstance(pid, int) and not alive(pid):
            return STALLED

        return state

    def clock(value: object) -> str:
        return value.strftime(CLOCK) if isinstance(value, datetime) else ""

    def seconds(value: object) -> float | None:
        return round(float(cast("float", value)), 1) if isinstance(value, (int, float)) else None

    rows: list[Row] = []
    for row in cast("list[dict[str, object]]", latest.rows(named=True)):
        state = state_of(row)
        pid = row[SweepEventColumn.PID]
        rows.append(
            {
                PointRow.STATE: state,
                PointRow.POINT: str(row[SweepEventColumn.POINT_NAME] or row[SweepEventColumn.POINT_KEY]),
                PointRow.KEY: str(row[SweepEventColumn.POINT_KEY]),
                PointRow.AT: clock(row[SweepEventColumn.AT]),
                PointRow.HOST: str(row[SweepEventColumn.HOST] or ""),
                PointRow.PID: pid if isinstance(pid, int) else None,
                PointRow.DURATION: seconds(row[SweepEventColumn.DURATION_S]),
                PointRow.ORIGIN: str(row[SweepEventColumn.ORIGIN] or ""),
                RANK: STATE_ORDER.index(state) if state in STATE_ORDER else len(STATE_ORDER),
            }
        )

    frame = pl.DataFrame(rows, schema=pl.Schema({**POINT_SCHEMA, RANK: pl.Int8}))
    ordered = frame.sort([RANK, PointRow.AT]).drop(RANK)

    return group_rows(ordered, PointRow.STATE)


def failure_items(latest: pl.DataFrame) -> list[Row]:
    """The failed points, newest first, for the failure list."""
    failed = latest.filter(pl.col(SweepEventColumn.STATE) == SweepState.FAILED).sort(SweepEventColumn.AT, descending=True)
    keys = cast("list[str]", failed[SweepEventColumn.POINT_KEY].to_list())
    names = cast("list[str | None]", failed[SweepEventColumn.POINT_NAME].to_list())
    moments = cast("list[datetime | None]", failed[SweepEventColumn.AT].to_list())

    items: list[Row] = []
    for key, name, at in zip(keys, names, moments, strict=True):
        items.append({FailureField.KEY: key, FailureField.NAME: name or key, FailureField.AT: at.strftime(CLOCK) if at is not None else ""})

    return items


def traceback_of(latest: pl.DataFrame, key: str) -> str:
    """The error a point's last transition recorded, empty when it has none."""
    mine = latest.filter(pl.col(SweepEventColumn.POINT_KEY) == key)
    if mine.height == 0:
        return ""

    errors = cast("list[str | None]", mine[SweepEventColumn.ERROR].to_list())

    return errors[0] or ""


def sweep_items(frame: pl.DataFrame) -> list[Row]:
    """The picker's items, newest sweep first, an unfinished one marked running."""
    ids = cast("list[str]", frame[SweepColumn.SWEEP_ID].to_list())
    descriptions = cast("list[str | None]", frame[SweepColumn.DESCRIPTION].to_list())
    totals = cast("list[int | None]", frame[SweepColumn.TOTAL].to_list())
    finishes = cast("list[datetime | None]", frame[SweepColumn.FINISHED_AT].to_list())

    items: list[Row] = []
    for sweep_id, description, total, finished in zip(ids, descriptions, totals, finishes, strict=True):
        parts = [sweep_id, description or "", f"{total} points" if total is not None else "", "running" if finished is None else ""]
        items.append({ItemField.TITLE: " · ".join(part for part in parts if part), ItemField.VALUE: sweep_id})

    return items


def default_sweep(frame: pl.DataFrame) -> str:
    """The sweep to open first: the newest unfinished one, else the newest, else none."""
    if frame.height == 0:
        return ""

    unfinished = frame.filter(pl.col(SweepColumn.FINISHED_AT).is_null())
    chosen = unfinished if unfinished.height else frame
    ids = cast("list[str]", chosen[SweepColumn.SWEEP_ID].to_list())

    return ids[0]


class SweepMonitorPanel(PanelComponent):
    """Shows one sweep as it runs: the progress strip, the point table grouped by state, and the failures with the traceback of the one picked."""

    def __init__(self, app: "Workbench", id: str, title: str, *, telemetry: Telemetry) -> None:
        """Builds the monitor, its hosted point table and the poll feed.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            telemetry: The telemetry root whose sweep journals the monitor reads.
        """
        self.telemetry = telemetry
        self.latest: pl.DataFrame = pl.DataFrame(schema=SWEEP_EVENT_SCHEMA)
        # Its own template, hosted inside this panel's; built outside the layout so its root is not also a child here.
        self.table = TablePanel(
            app,
            f"{id}-{TABLE_SUFFIX}",
            "Points",
            SweepTick([]).points,
            grouped=True,
            filterable=True,
            lead=[PointRow.STATE, PointRow.POINT],
            pick=self.pick,
        )
        self.feed: Feed[SweepTick] = Feed(self, PERIOD_S, self.poll, self.publish)

        super().__init__(app, id, title)

    def build_picker(self) -> None:
        """The sweep select, built into the app bar."""
        with labelled("Sweep"):
            picker(self.key(SweepVar.SWEEP), self.key(SweepVar.ITEMS), placeholder="No Sweeps", width=PICKER_WIDTH)

    def build(self) -> None:
        """Builds the progress strip, the point table and the failure list beside the traceback viewer."""
        with html.Div(classes=str(Cls.TOOLBAR)):
            html.Span([react.Bind(self.key(SweepVar.COUNTS))], classes=str(Cls.CAPTION))
            html.Span(classes=str(Cls.SPACER))
            html.Span([react.Bind(self.key(SweepVar.PACE))], classes=str(Cls.STATUS))

        with html.Div(classes=str(Cls.COLUMN)):
            with html.Div(classes=str(Cls.STRIP)):
                Progress(value=react.Bind(self.key(SweepVar.PERCENT)), max_=PERCENT, classes=BAR)

            with html.Div(classes=str(Cls.FILL)):
                client.ServerTemplate(name=self.table.template_name)

            with html.Div(classes=str(Cls.PANE_ROW)):
                self.build_failures()
                with html.Div(classes=str(Cls.PANE)):
                    Code(
                        value=react.Bind(self.key(SweepVar.TRACEBACK)),
                        language=PLAIN_TEXT,
                        theme=self.app.palette.monaco_theme,
                        options=react.Bind(self.key(SweepVar.EDITOR)),
                        classes=str(Cls.FILL),
                    )

        sweeps = sweep_runs(self.telemetry)
        self.state.update(
            {
                self.key(SweepVar.SWEEP): default_sweep(sweeps),
                self.key(SweepVar.ITEMS): sweep_items(sweeps),
                self.key(SweepVar.PERCENT): 0.0,
                self.key(SweepVar.COUNTS): "",
                self.key(SweepVar.PACE): "",
                self.key(SweepVar.FAILURES): [],
                self.key(SweepVar.TRACEBACK): "",
                self.key(SweepVar.EDITOR): EDITOR_OPTIONS,
            }
        )
        _ = self.listen(self.key(SweepVar.SWEEP))(self.on_sweep)

    def build_failures(self) -> None:
        """The failed points as a list; a click puts the point's traceback in the viewer."""
        with html.Div(classes=str(Cls.PANE)):
            html.Div("Failures", classes=str(Cls.SECTION))
            with html.Ul(classes=str(Cls.LIST)):
                with react.For(items=self.key(SweepVar.FAILURES), name=FAILURE):
                    with html.Li(classes=str(Cls.LIST_ITEM), on_click=self.callback(self.show_failure, args=f"[{FAILURE}.{FailureField.KEY}]")):
                        html.Div([react.Bind(f"{FAILURE}.{FailureField.NAME}")])
                        html.Div(
                            [react.Bind(f"{FAILURE}.{FailureField.AT}"), " · ", react.Bind(f"{FAILURE}.{FailureField.KEY}")],
                            classes=str(Cls.CAPTION),
                        )

                with react.If(f"!{self.key(SweepVar.FAILURES)}.length"):
                    html.Li("none", classes=f"{Cls.LIST_ITEM} {Cls.CAPTION}")

    def poll(self) -> SweepTick:
        """The sweep list and, for the selected sweep, its summary, every point's last state and its failures, read off the loop."""
        sweeps = sweep_runs(self.telemetry)
        items = sweep_items(sweeps)
        sweep_id = str(self.state[self.key(SweepVar.SWEEP)] or "")
        if not sweep_id:
            return SweepTick(items)

        launch = sweeps.filter(pl.col(SweepColumn.SWEEP_ID) == sweep_id)
        totals = cast("list[int | None]", launch[SweepColumn.TOTAL].to_list())
        total = (totals[0] or 0) if totals else 0
        query = SweepQuery(self.telemetry, sweep_id)
        latest = query.latest()

        return SweepTick(items, sweep_id, progress(query.summary(), total), point_rows(latest), failure_items(latest), latest)

    def publish(self, tick: SweepTick) -> None:
        """Writes one tick into the strip, the table (only when its rows changed) and the failure list."""
        self.latest = tick.latest
        self.state.update(
            {
                self.key(SweepVar.ITEMS): tick.items,
                self.key(SweepVar.PERCENT): tick.progress.percent,
                self.key(SweepVar.COUNTS): tick.progress.counts,
                self.key(SweepVar.PACE): tick.progress.pace,
                self.key(SweepVar.FAILURES): tick.failures,
            }
        )
        if not tick.points.equals(self.table.frame):
            self.table.set_rows(tick.points)

    @guard
    def show_failure(self, key: str) -> None:
        """Puts a failed point's traceback in the viewer."""
        self.state[self.key(SweepVar.TRACEBACK)] = traceback_of(self.latest, key)

    def pick(self, row: Row) -> None:
        """A clicked failed point shows its traceback; any other point clears the viewer."""
        failed = str(row.get(PointRow.STATE, "")) == SweepState.FAILED
        with self.state:
            self.state[self.key(SweepVar.TRACEBACK)] = traceback_of(self.latest, str(row.get(PointRow.KEY, ""))) if failed else ""

    def on_sweep(self, **_: object) -> None:
        """A different sweep starts with an empty traceback viewer; the next poll fills the rest."""
        self.state[self.key(SweepVar.TRACEBACK)] = ""

    def on_visible(self, visible: bool) -> None:
        """Polls only while the panel is on screen."""
        if visible:
            self.feed.start()
            return

        self.feed.stop()

    def dispose(self) -> None:
        """Stops polling, then releases the point table and the panel."""
        self.feed.stop()
        self.table.dispose()
        super().dispose()
