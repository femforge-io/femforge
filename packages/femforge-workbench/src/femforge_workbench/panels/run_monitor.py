"""The run monitor: the selected run's heartbeat, stage strip, convergence chart and a log tail, polled every second while visible."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, cast

import plotly.graph_objects as go
import polars as pl
from trame.widgets import client, html, react

from femforge.common.files import BundleFile
from femforge.common.utils import title
from femforge.run.manifest import RunManifest
from femforge.run.result import LifecycleStatus, ManifestStatus
from femforge.run.stages import StageEvent
from femforge.telemetry.convergence import ConvergenceTargetsFile, frame_schema, scan_convergence
from femforge.telemetry.paths import Telemetry
from femforge.telemetry.runs import RunQuery, live_runs
from femforge.telemetry.schemas import ConvergenceColumn, LiveRunColumn, RunColumn
from femforge.telemetry.tail import TextTail
from femforge_workbench.feeds import Feed
from femforge_workbench.grid import Row
from femforge_workbench.panels import Cls, ItemField, PanelComponent, Shared, labelled, picker
from femforge_workbench.panels.figure import FigurePanel
from femforge_workbench.panels.log import LogPanel
from femforge_workbench.panels.runs import StageField, StatusVariant, stage_rows
from femforge_workbench.widgets.badge import Badge
from femforge_workbench.widgets.tip import Tip
from femforge_workbench.widgets.toggle import ToggleGroup, ToggleGroupItem


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


PERIOD_S = 1.0
PICKER_LIMIT = 200
PICKER_EVERY = 5
"""Polls between picker refreshes: the index scan costs tens of milliseconds, the bundle stats under one."""

TAIL_LINES = 2000
CHART_SUFFIX = "chart"
TAIL_SUFFIX = "tail"
LIVE_MARK = "●"
GONE = "gone"
RUNNING: ManifestStatus = LifecycleStatus.RUNNING
TOLERANCE_DASH = "dash"
RELC_AXIS = "relc"
WALL_AXIS = "wall time [s]"
PICKER_WIDTH = "w-80"
TOGGLE_SIZE = "sm"
ROW = "row"


class RunVar(StrEnum):
    """The run monitor's per-panel state variables."""

    ITEMS = "items"
    HEARTBEAT = "heartbeat"
    STATUS = "status"
    STATUS_VARIANT = "status_variant"
    STAGES = "stages"
    TAIL = "tail"
    SAMPLES = "samples"


@dataclass
class Watch:
    """The cursors the monitor keeps on one bundle between polls."""

    run_id: str
    sim_dir: Path
    solver: TextTail
    run: TextTail
    manifest_mtime: float | None = None
    history: list[StageEvent] = field(default_factory=list)
    status: ManifestStatus = RUNNING
    convergence_size: int = -1
    targets: ConvergenceTargetsFile = field(default_factory=ConvergenceTargetsFile)

    @classmethod
    def on(cls, run_id: str, sim_dir: Path) -> "Watch":
        """A watch at the start of a bundle's solver and run logs."""
        return cls(run_id, sim_dir, TextTail(sim_dir / BundleFile.SOLVER_LOG), TextTail(sim_dir / BundleFile.RUN_LOG))


@dataclass(frozen=True)
class RunTick:
    """What one poll found; a part left None did not change."""

    run_id: str
    reset: bool = False
    items: list[Row] | None = None
    status: str | None = None
    stages: list[Row] | None = None
    heartbeat: str = ""
    convergence: pl.DataFrame | None = None
    targets: ConvergenceTargetsFile | None = None
    solver_lines: list[str] = field(default_factory=list)
    run_lines: list[str] = field(default_factory=list)


def run_items(frame: pl.DataFrame) -> list[Row]:
    """The picker's items: the run id, its status and a mark on runs whose lock owner still heartbeats."""
    ids = cast("list[str]", frame[RunColumn.RUN_ID].to_list())
    statuses = cast("list[str | None]", frame[RunColumn.STATUS].to_list())
    staleness = cast("list[bool | None]", frame[LiveRunColumn.STALE].to_list()) if LiveRunColumn.STALE in frame.columns else [None] * frame.height

    items: list[Row] = []
    for run_id, status, stale in zip(ids, statuses, staleness, strict=True):
        parts = [LIVE_MARK if stale is False else "", run_id, status or ""]
        items.append({ItemField.TITLE: " ".join(part for part in parts if part), ItemField.VALUE: run_id})

    return items


def heartbeat_text(claims: pl.DataFrame, run_id: str) -> str:
    """One run's claim as a phrase: the holder's pid and how long since it beat, marked stale past the lock's limit; empty when unclaimed."""
    mine = claims.filter(pl.col(LiveRunColumn.RUN_ID) == run_id)
    if mine.height == 0:
        return ""

    ages = cast("list[float]", mine[LiveRunColumn.HEARTBEAT_AGE_S].to_list())
    pids = cast("list[int]", mine[LiveRunColumn.PID].to_list())
    staleness = cast("list[bool]", mine[LiveRunColumn.STALE].to_list())
    phrase = f"heartbeat {ages[0]:.0f} s ago · pid {pids[0]}"

    return f"{phrase} · stale" if staleness[0] else phrase


def convergence_figure(frame: pl.DataFrame, targets: ConvergenceTargetsFile, *, revision: str = "") -> go.Figure:
    """Relative change over wall time on a log axis: one trace per (solver, loop), a dashed line at every authored tolerance.

    Args:
        frame: The convergence samples: each solver loop's relative change by wall time.
        targets: The authored tolerances.
        revision: The figure's uirevision, which keeps zoom and legend across updates of one run, and its export title.
    """
    figure = go.Figure()
    pairs = frame.select(ConvergenceColumn.SOLVER, ConvergenceColumn.LOOP).unique(maintain_order=True)
    solvers = cast("list[str]", pairs[ConvergenceColumn.SOLVER].to_list())
    loops = cast("list[str]", pairs[ConvergenceColumn.LOOP].to_list())
    for solver, loop in zip(solvers, loops, strict=True):
        sampled = (pl.col(ConvergenceColumn.SOLVER) == solver) & (pl.col(ConvergenceColumn.LOOP) == loop) & pl.col(ConvergenceColumn.RELC).is_not_null()
        part = frame.filter(sampled)
        wall = cast("list[float]", part[ConvergenceColumn.WALL_TIME].to_list())
        relc = cast("list[float]", part[ConvergenceColumn.RELC].to_list())
        _ = figure.add_trace(go.Scatter(x=wall, y=relc, mode="lines+markers", name=f"{solver} {loop}"))

    walls = cast("list[float | None]", frame[ConvergenceColumn.WALL_TIME].to_list())
    end = max((wall for wall in walls if wall is not None), default=0.0)
    span = [0.0, end if end > 0.0 else 1.0]
    for target in targets.loops:
        if target.tolerance is None:
            continue

        _ = figure.add_trace(
            go.Scatter(
                x=span,
                y=[target.tolerance, target.tolerance],
                mode="lines",
                line={"dash": TOLERANCE_DASH, "width": 1},
                name=f"{target.solver} {target.loop} tolerance",
                showlegend=False,
                hoverinfo="name+y",
            )
        )

    _ = figure.update_layout(
        yaxis={"type": "log", "title": {"text": RELC_AXIS}},
        xaxis={"title": {"text": WALL_AXIS}},
        legend={"orientation": "h"},
        uirevision=revision,
        meta={"title": f"convergence {revision}".strip()},
    )

    return figure


class RunMonitorPanel(PanelComponent):
    """Follows the shared run selection: its claim and status, a badge per stage, the convergence chart and a tail of elmersolver.log or run.log."""

    def __init__(self, app: "Workbench", id: str, title: str, *, telemetry: Telemetry) -> None:
        """Builds the monitor, its hosted chart and tail, and the poll feed.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            telemetry: The telemetry root whose run index the picker lists and locates bundles in.
        """
        self.telemetry = telemetry
        self.watched = ""
        self.watch: Watch | None = None
        self.since_items = PICKER_EVERY
        self.convergence: pl.DataFrame = pl.DataFrame(schema=frame_schema(with_progress=True))
        self.solver_lines: deque[str] = deque(maxlen=TAIL_LINES)
        self.run_lines: deque[str] = deque(maxlen=TAIL_LINES)
        # Their own templates, hosted inside this panel's; built outside the layout so their roots are not also children here.
        self.chart = FigurePanel(app, f"{id}-{CHART_SUFFIX}", "Convergence", mode_bar=False)
        self.tail = LogPanel(app, f"{id}-{TAIL_SUFFIX}", "Tail", scrollback=TAIL_LINES)
        self.feed: Feed[RunTick] = Feed(self, PERIOD_S, self.poll, self.publish)

        super().__init__(app, id, title)

    def build_picker(self) -> None:
        """The run select bound to the shared selection, built into the app bar, so a pick here and a row click in the run browser move together."""
        with labelled("Run"):
            picker(str(Shared.SELECTED_RUN_ID), self.key(RunVar.ITEMS), placeholder="No Run Selected", width=PICKER_WIDTH)

    def build(self) -> None:
        """Builds the status bar, the stage strip, the chart, the log toggle and the tail."""
        with html.Div(classes=str(Cls.TOOLBAR)):
            with react.If(self.key(RunVar.STATUS)):
                Badge([react.Bind(self.key(RunVar.STATUS))], variant=react.Bind(self.key(RunVar.STATUS_VARIANT)))

            html.Span([react.Bind(self.key(RunVar.HEARTBEAT))], classes=str(Cls.CAPTION))
            html.Span(classes=str(Cls.SPACER))
            html.Span([react.Bind(self.key(RunVar.SAMPLES))], classes=str(Cls.STATUS))

        with html.Div(classes=str(Cls.COLUMN)):
            with html.Div(classes=str(Cls.STRIP)):
                with react.For(items=self.key(RunVar.STAGES), name=ROW), Tip(content=react.Bind(f"{ROW}.{StageField.STATE}")):
                    Badge(
                        [react.Bind(f"{ROW}.{StageField.STAGE}"), " · ", react.Bind(f"{ROW}.{StageField.SECONDS}"), " s"],
                        variant=react.Bind(f"{ROW}.{StageField.VARIANT}"),
                    )

                with react.If(f"!{self.key(RunVar.STAGES)}.length"):
                    html.Span("No Stages Yet", classes=str(Cls.CAPTION))

            with html.Div(classes=str(Cls.FILL)):
                client.ServerTemplate(name=self.chart.template_name)

            with html.Div(classes=str(Cls.STRIP)), ToggleGroup(model=self.key(RunVar.TAIL), type_="single", size=TOGGLE_SIZE, variant="outline"):
                ToggleGroupItem(str(BundleFile.SOLVER_LOG), value=str(BundleFile.SOLVER_LOG))
                ToggleGroupItem(str(BundleFile.RUN_LOG), value=str(BundleFile.RUN_LOG))

            with html.Div(classes=str(Cls.FILL)):
                client.ServerTemplate(name=self.tail.template_name)

        _ = self.state.setdefault(Shared.SELECTED_RUN_ID, "")
        self.state.update(
            {
                self.key(RunVar.ITEMS): self.items(),
                self.key(RunVar.HEARTBEAT): "",
                self.key(RunVar.STATUS): "",
                self.key(RunVar.STATUS_VARIANT): StatusVariant.of(""),
                self.key(RunVar.STAGES): [],
                self.key(RunVar.TAIL): str(BundleFile.SOLVER_LOG),
                self.key(RunVar.SAMPLES): "",
            }
        )
        _ = self.listen(self.key(RunVar.TAIL))(self.on_tail)

    def items(self) -> list[Row]:
        """The newest indexed runs with their live claims, for the picker; a selected run older than the limit is appended, so its trigger is never blank."""
        found = run_items(RunQuery(self.telemetry.runs.scan()).with_live().newest(PICKER_LIMIT).frame())
        selected = str(self.state[Shared.SELECTED_RUN_ID] or "")
        if selected and all(item[ItemField.VALUE] != selected for item in found):
            found.append({ItemField.TITLE: selected, ItemField.VALUE: selected})

        return found

    def locate(self, run_id: str) -> Watch | None:
        """A fresh watch on the bundle the index files under ``run_id``, None when the index does not know it."""
        if not run_id:
            return None

        found: pl.DataFrame = RunQuery(self.telemetry.runs.scan()).where(pl.col(RunColumn.RUN_ID) == run_id).frame()
        if found.height == 0:
            return None

        directories = cast("list[str]", found[RunColumn.RUNS_DIR].to_list())

        return Watch.on(run_id, Path(directories[0]) / run_id)

    def poll(self) -> RunTick:
        """Everything that moved since the last poll, read off the loop: picker rows on schedule, the manifest by mtime, the claim, the log by size, the tails."""
        run_id = str(self.state[Shared.SELECTED_RUN_ID] or "")
        self.since_items += 1
        items: list[Row] | None = None
        if self.since_items >= PICKER_EVERY:
            self.since_items = 0
            items = self.items()

        reset = run_id != self.watched
        if reset:
            self.watched = run_id
            self.watch = self.locate(run_id)

        watch = self.watch
        if watch is None or not watch.sim_dir.is_dir():
            return RunTick(run_id, reset=reset, items=items, status=GONE if run_id else "", stages=[] if reset else None)

        status: str | None = None
        stages: list[Row] | None = None
        mtime = (watch.sim_dir / BundleFile.MANIFEST).stat().st_mtime
        if mtime != watch.manifest_mtime:
            manifest = RunManifest.load(watch.sim_dir)
            watch.manifest_mtime = mtime
            watch.history = manifest.stage_history
            watch.status = manifest.status
            status = manifest.status

        if status is not None or watch.status == RUNNING:
            stages = stage_rows(watch.history, datetime.now(timezone.utc), status=watch.status)

        convergence: pl.DataFrame | None = None
        targets: ConvergenceTargetsFile | None = None
        samples = watch.sim_dir / BundleFile.CONVERGENCE
        size = samples.stat().st_size if samples.is_file() else 0
        if size != watch.convergence_size:
            watch.convergence_size = size
            watch.targets = ConvergenceTargetsFile.load(watch.sim_dir)
            convergence = scan_convergence(watch.sim_dir)
            targets = watch.targets

        return RunTick(
            run_id,
            reset=reset,
            items=items,
            status=status,
            stages=stages,
            heartbeat=heartbeat_text(live_runs(watch.sim_dir.parent), run_id),
            convergence=convergence,
            targets=targets,
            solver_lines=watch.solver.read_new(),
            run_lines=watch.run.read_new(),
        )

    def publish(self, tick: RunTick) -> None:
        """Writes one tick into the state, the chart and the tail; a run change clears them first."""
        if tick.items is not None:
            self.state[self.key(RunVar.ITEMS)] = tick.items

        if tick.reset:
            self.reset()

        if tick.status is not None:
            self.state[self.key(RunVar.STATUS)] = title(tick.status)
            self.state[self.key(RunVar.STATUS_VARIANT)] = StatusVariant.of(tick.status)

        if tick.stages is not None:
            self.state[self.key(RunVar.STAGES)] = tick.stages

        self.state[self.key(RunVar.HEARTBEAT)] = tick.heartbeat

        if tick.convergence is not None and tick.targets is not None:
            self.convergence = tick.convergence
            self.chart.update(convergence_figure(tick.convergence, tick.targets, revision=tick.run_id))
            self.state[self.key(RunVar.SAMPLES)] = f"{tick.convergence.height} samples"

        self.solver_lines.extend(tick.solver_lines)
        self.run_lines.extend(tick.run_lines)
        fresh = tick.solver_lines if self.shown_tail() is BundleFile.SOLVER_LOG else tick.run_lines
        for line in fresh:
            self.tail.write(line)

    def shown_tail(self) -> BundleFile:
        """Which log the toggle shows."""
        return BundleFile.RUN_LOG if str(self.state[self.key(RunVar.TAIL)]) == BundleFile.RUN_LOG else BundleFile.SOLVER_LOG

    def shown_lines(self) -> deque[str]:
        """The buffered lines of the log the toggle shows."""
        return self.run_lines if self.shown_tail() is BundleFile.RUN_LOG else self.solver_lines

    def reset(self) -> None:
        """Clears what the previous run left in the tails, the chart and the strip."""
        self.solver_lines.clear()
        self.run_lines.clear()
        self.tail.clear()
        self.convergence = pl.DataFrame(schema=frame_schema(with_progress=True))
        self.chart.update(go.Figure())
        self.state.update({self.key(RunVar.STAGES): [], self.key(RunVar.STATUS): "", self.key(RunVar.SAMPLES): ""})

    def on_tail(self, **_: object) -> None:
        """Refills the terminal with the log the toggle now names."""
        self.tail.clear()
        for line in self.shown_lines():
            self.tail.write(line)

    def on_layout_changed(self) -> None:
        """Fits the tail after the dock re-laid out."""
        self.tail.fit()

    def on_visible(self, visible: bool) -> None:
        """Polls only while the panel is on screen."""
        if visible:
            self.tail.fit()
            self.feed.start()
            return

        self.feed.stop()

    def dispose(self) -> None:
        """Stops polling, then releases the chart, the tail and the panel."""
        self.feed.stop()
        self.chart.dispose()
        self.tail.dispose()
        super().dispose()
