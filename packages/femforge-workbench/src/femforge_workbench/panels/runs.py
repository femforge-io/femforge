"""The run browser: the telemetry run index in a table, with one bundle's stages, results, files and retention beside it."""

import asyncio
import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, cast

import polars as pl
from trame.widgets import html, react
from trame_server.utils.asynchronous import create_task

from femforge.artifacts.cache import ArtifactCache
from femforge.artifacts.local import LocalArtifactStore
from femforge.common.files import RunFile
from femforge.common.log import log
from femforge.common.utils import title
from femforge.run.artifacts import rehydrate_bundle
from femforge.run.inspect import Run
from femforge.run.manifest import RunManifest
from femforge.run.purge import cleanup_bundle
from femforge.run.result import ManifestStatus, Result
from femforge.run.retention import BundleEntry, Cleanup, CleanupPlan, CleanupRecord, Preset, RetentionPolicy, bundle_inventory, plan_cleanup
from femforge.run.stages import StageEvent
from femforge.telemetry.paths import Telemetry
from femforge.telemetry.runs import RunQuery, live_runs
from femforge.telemetry.schemas import LiveRunColumn, RunColumn
from femforge_workbench.app import Hook
from femforge_workbench.feeds import Feed
from femforge_workbench.grid import Row
from femforge_workbench.panels import Cls, PanelComponent, Shared, chooser, labelled, select_run
from femforge_workbench.panels.artifacts import ANY, BUTTON_SIZE, KEY_CHARS, RefColumn, as_written, build_pairs, build_table, columns, open_folder, pair, ref_rows
from femforge_workbench.panels.table import TablePanel
from femforge_workbench.widgets.badge import Badge
from femforge_workbench.widgets.button import Button
from femforge_workbench.widgets.dialog import Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle
from femforge_workbench.widgets.input import Input


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


LIVE_PERIOD_S = 5.0
"""How often the browser re-queries while a shown run is claimed."""

WATCH_PERIOD_S = 1.0
"""How often the detail panel re-reads a run that is still solving."""

STARTING = "starting"
"""The status of a bundle directory whose manifest is not written yet."""

WATCHED: frozenset[str] = frozenset({"running", STARTING})
"""The statuses the detail panel keeps re-reading until the run ends."""

DEFAULT_LIMIT = 500
LIMITS = (200, 500, 2000)
"""How many of the newest runs the table holds; the index has thousands."""

LIVE_MARK = "●"
UNKNOWN_VARIANT = "outline"
"""The badge variant of a status no member names: neutral, so it cannot read as success."""

MINUTE_S = 60.0
HOUR_S = 3600.0
REF = "ref"
ROW = "row"


class StatusVariant(StrEnum):
    """The badge variant of each run status; the member name is the status it paints."""

    OK = "default"
    DIVERGED = "outline"
    ERROR = "destructive"
    RUNNING = "secondary"
    ABANDONED = "outline"

    @classmethod
    def of(cls, status: str) -> str:
        """The variant a status badge carries, neutral for an unknown or missing status."""
        found: StatusVariant | None = cls.__members__.get(status.upper())

        return found.value if found is not None else UNKNOWN_VARIANT


class StageDot(StrEnum):
    """The dot a timeline row draws, by how the stage ended; the class strings appear literally so the stylesheet build scans them."""

    DONE = "size-2 shrink-0 rounded-full bg-muted-foreground"
    RUNNING = "size-2 shrink-0 rounded-full bg-primary"
    CRASHED = "size-2 shrink-0 rounded-full bg-destructive"


class StageState(StrEnum):
    """How one pipeline stage of a run ended."""

    DONE = "done"
    RUNNING = "running"
    CRASHED = "crashed"

    @property
    def variant(self) -> str:
        """The badge variant a stage in this state carries."""
        match self:
            case StageState.DONE:
                return StatusVariant.OK.value
            case StageState.RUNNING:
                return StatusVariant.RUNNING.value
            case StageState.CRASHED:
                return StatusVariant.ERROR.value

    @property
    def dot(self) -> str:
        """The timeline dot a stage in this state draws."""
        return StageDot[self.name].value


class RunRow(StrEnum):
    """The run table's columns, in the order they are sent; the run id leads, so it is the pinned one."""

    RUN_ID = "run_id"
    LIVE = "live"
    STATUS = "status"
    STAGE = "stage"
    CREATED = "created"
    DESCRIPTION = "description"
    TAGS = "tags"
    PHYSICS = "physics"
    DIM = "dim"
    ELEMENTS = "elements"
    FORCE_Y = "force_y"
    T_MAX = "t_max"
    WALL = "wall"
    MB = "mb"


class StageField(StrEnum):
    """The keys of one stage timeline row."""

    STAGE = "stage"
    STATE = "state"
    VARIANT = "variant"
    DOT = "dot"
    STARTED = "started"
    SECONDS = "seconds"


class ComponentColumn(StrEnum):
    """The columns of one component force row."""

    COMPONENT = "component"
    FX = "fx"
    FY = "fy"
    FZ = "fz"
    FORCE = "f"
    TORQUE = "torque"


class CurrentColumn(StrEnum):
    """The columns of one terminal current row."""

    TERMINAL = "terminal"
    AMPS = "amps"


class BundleFileColumn(StrEnum):
    """The columns of one bundle file row."""

    PATH = "path"
    SIZE = "size"
    FILE_CLASS = "file_class"
    CACHED = "cached"


class CleanupColumn(StrEnum):
    """The columns of one retention pass row."""

    APPLIED = "applied"
    TRIGGER = "trigger"
    FILES = "files"
    MB = "mb"
    CLASSES = "classes"


class BrowserVar(StrEnum):
    """The run browser's per-panel state variables."""

    TEXT = "text"
    STATUS = "status"
    TAG = "tag"
    PHYSICS = "physics"
    LIMIT = "limit"
    COUNT = "count"


class DetailVar(StrEnum):
    """The run detail's per-panel state variables."""

    TITLE = "title"
    STATUS = "status"
    STATUS_VARIANT = "status_variant"
    PATH = "path"
    META = "meta"
    STAGES = "stages"
    SCALARS = "scalars"
    COMPONENTS = "components"
    COMPONENT_COLUMNS = "component_columns"
    CURRENTS = "currents"
    CURRENT_COLUMNS = "current_columns"
    REFS = "refs"
    FILES = "files"
    FILE_COLUMNS = "file_columns"
    CLEANUPS = "cleanups"
    CLEANUP_COLUMNS = "cleanup_columns"
    FIELDS_NOTE = "fields_note"
    HAS_FIELDS = "has_fields"


class PurgeVar(StrEnum):
    """The purge dialog's state variables; one rule and one planned size per retention class ride beside them."""

    DIALOG = "purge_dialog"
    PRESET = "purge_preset"
    TOTAL = "purge_total"
    PLANNED = "purge_planned"


POLICY_CLASSES: tuple[RunFile, ...] = tuple(file_class for file_class, _ in RetentionPolicy.default().rules())
"""The retention classes a policy names, in policy order; the purge dialog offers a rule for each."""

RUN_SCHEMA = pl.Schema(
    {
        RunRow.RUN_ID: pl.String,
        RunRow.LIVE: pl.String,
        RunRow.STATUS: pl.String,
        RunRow.STAGE: pl.String,
        RunRow.CREATED: pl.String,
        RunRow.DESCRIPTION: pl.String,
        RunRow.TAGS: pl.String,
        RunRow.PHYSICS: pl.String,
        RunRow.DIM: pl.Int32,
        RunRow.ELEMENTS: pl.Int64,
        RunRow.FORCE_Y: pl.Float64,
        RunRow.T_MAX: pl.Float64,
        RunRow.WALL: pl.String,
        RunRow.MB: pl.Float64,
    }
)
"""The table frame's columns, so an empty listing still builds the same table."""


def format_wall_time(seconds: float | None) -> str:
    """A solve's wall time as a short human string, blank when the run recorded none."""
    if seconds is None:
        return ""

    if seconds < MINUTE_S:
        return f"{seconds:.1f} s"

    if seconds < HOUR_S:
        return f"{int(seconds // MINUTE_S)}m {int(seconds % MINUTE_S):02d}s"

    return f"{int(seconds // HOUR_S)}h {int(seconds % HOUR_S // MINUTE_S):02d}m"


def tag_pairs(tags: object) -> list[str]:
    """The ``key=value`` labels of a run's metadata tags, read from the index's JSON column."""
    if not isinstance(tags, str) or not tags:
        return []

    try:
        payload = cast("object", json.loads(tags))
    except ValueError:
        return []

    if not isinstance(payload, dict):
        return []

    return [f"{key}={value}" for key, value in cast("dict[str, object]", payload).items()]


def live_ids(frame: pl.DataFrame) -> set[str]:
    """The run ids of the frame's rows whose lock owner is still heartbeating."""
    if LiveRunColumn.STALE not in frame.columns:
        return set()

    claimed: pl.DataFrame = frame.filter(pl.col(LiveRunColumn.STALE).fill_null(value=True).not_())

    return set(cast("list[str]", claimed[RunColumn.RUN_ID].to_list()))


def run_rows(frame: pl.DataFrame, live: Collection[str] = ()) -> list[Row]:
    """The table rows of a run index frame; a run id in ``live`` carries the claim mark."""

    def number(value: object, digits: int) -> float | None:
        return round(float(cast("float", value)), digits) if isinstance(value, (int, float)) else None

    def moment(value: object) -> str:
        return value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime) else ""

    def size(value: object) -> float | None:
        counted: float | None = number(value, 0)

        return round(counted / 1e6, 1) if counted is not None else None

    rows: list[Row] = []
    for row in cast("list[dict[str, object]]", frame.rows(named=True)):
        run_id = str(row[RunColumn.RUN_ID])
        rows.append(
            {
                RunRow.RUN_ID: run_id,
                RunRow.LIVE: LIVE_MARK if run_id in live else "",
                RunRow.STATUS: title(str(row[RunColumn.STATUS] or "")),
                RunRow.STAGE: title(str(row[RunColumn.CURRENT_STAGE] or "")),
                RunRow.CREATED: moment(row[RunColumn.CREATED_AT]),
                RunRow.DESCRIPTION: str(row[RunColumn.DESCRIPTION] or ""),
                RunRow.TAGS: " ".join(tag_pairs(row[RunColumn.TAGS])),
                RunRow.PHYSICS: title(str(row[RunColumn.PHYSICS] or "")),
                RunRow.DIM: row[RunColumn.MODEL_DIM],
                RunRow.ELEMENTS: row[RunColumn.ELEMENT_COUNT],
                RunRow.FORCE_Y: number(row[RunColumn.FORCE_Y], 3),
                RunRow.T_MAX: number(row[RunColumn.TEMPERATURE_MAX], 1),
                RunRow.WALL: format_wall_time(number(row[RunColumn.WALL_TIME], 2)),
                RunRow.MB: size(row[RunColumn.BUNDLE_BYTES]),
            }
        )

    return rows


def stage_rows(history: Sequence[StageEvent], now: datetime, *, status: ManifestStatus = "running") -> list[Row]:
    """One timeline row per pipeline stage: done with its duration, still running, or crashed where the pipeline stopped.

    Args:
        history: The run's stage events, in pipeline order.
        now: The time an unfinished stage's duration runs up to.
        status: The run's manifest status; an unfinished last stage reads as running only while the run is.
    """

    def elapsed(start: datetime, end: datetime) -> float:
        started = start if start.tzinfo is not None else start.replace(tzinfo=timezone.utc)
        finished = end if end.tzinfo is not None else end.replace(tzinfo=timezone.utc)

        return (finished - started).total_seconds()

    rows: list[Row] = []
    for index, event in enumerate(history):
        unfinished: bool = event.finished_at is None
        last: bool = index == len(history) - 1
        state = StageState.DONE
        if unfinished:
            state = StageState.RUNNING if last and status == "running" else StageState.CRASHED

        seconds: float = elapsed(event.started_at, event.finished_at if event.finished_at is not None else now)
        rows.append(
            {
                StageField.STAGE: title(event.stage.value),
                StageField.STATE: title(state.value),
                StageField.VARIANT: state.variant,
                StageField.DOT: state.dot,
                StageField.STARTED: event.started_at.strftime("%H:%M:%S"),
                StageField.SECONDS: round(seconds, 2),
            }
        )

    return rows


def component_rows(run: Run) -> list[Row]:
    """One row per sif Component with its lumped force and, where the solve exported one, its torque magnitude."""
    torques = run.component_torques

    rows: list[Row] = []
    for name, force in run.component_forces.items():
        vector = force.magnitude
        torque = torques.get(name)
        rows.append(
            {
                ComponentColumn.COMPONENT: name,
                ComponentColumn.FX: round(vector.x, 4),
                ComponentColumn.FY: round(vector.y, 4),
                ComponentColumn.FZ: round(vector.z, 4),
                ComponentColumn.FORCE: round(vector.magnitude(), 4),
                ComponentColumn.TORQUE: round(torque.magnitude.magnitude(), 6) if torque is not None else None,
            }
        )

    return rows


def scalar_rows(result: Result) -> list[Row]:
    """The typed result scalars as label/value pairs, pint quantities reduced to plain numbers."""
    rows: list[Row] = [pair("status", title(result.status)), pair("wall time", format_wall_time(result.wall_time))]
    if result.residual is not None:
        rows.append(pair("residual", f"{result.residual:.3g}"))

    if result.flux_density_max is not None:
        rows.append(pair("|B| max [T]", round(float(result.flux_density_max.magnitude), 4)))

    if result.temperature_max is not None:
        rows.append(pair("T max [K]", round(float(result.temperature_max.magnitude), 2)))

    if result.force is not None:
        force = result.force.magnitude
        rows.append(pair("force [N]", f"({force.x:.4g}, {force.y:.4g}, {force.z:.4g})"))

    rows.append(pair("frame", title(result.frame.label)))
    rows.append(pair("elmer", result.elmer_version or ""))
    rows.append(pair("raw scalars", len(result.raw)))

    return rows


def current_rows(run: Run) -> list[Row]:
    """The terminal current of each named electrode, then of each phase."""
    electrodes: list[Row] = [{CurrentColumn.TERMINAL: name, CurrentColumn.AMPS: round(amps, 4)} for name, amps in run.electrode_currents.items()]
    phases: list[Row] = [{CurrentColumn.TERMINAL: f"phase {index}", CurrentColumn.AMPS: round(amps, 4)} for index, amps in run.phase_currents.items()]

    return [*electrodes, *phases]


def file_rows(entries: Sequence[BundleEntry], cached: Collection[str]) -> list[Row]:
    """One row per bundle file: its size, its retention class, and whether the store can re-fetch it."""
    return [
        {
            BundleFileColumn.PATH: entry.relative,
            BundleFileColumn.SIZE: entry.size,
            BundleFileColumn.FILE_CLASS: entry.file_class.value,
            BundleFileColumn.CACHED: "cached" if entry.relative in cached else "",
        }
        for entry in entries
    ]


def cleanup_rows(records: Sequence[CleanupRecord]) -> list[Row]:
    """One row per retention pass over the bundle, newest last."""
    return [
        {
            CleanupColumn.APPLIED: record.applied_at.strftime("%Y-%m-%d %H:%M"),
            CleanupColumn.TRIGGER: record.trigger.value,
            CleanupColumn.FILES: len(record.removed),
            CleanupColumn.MB: round(record.bytes_freed / 1e6, 1),
            CleanupColumn.CLASSES: ", ".join(sorted({entry.file_class.value for entry in record.removed})),
        }
        for record in records
    ]


def megabytes(size: int) -> str:
    """A byte count as megabytes, blank when nothing would move."""
    return f"{size / 1e6:.1f} MB" if size else ""


def counted(number: int, thing: str) -> str:
    """``number`` of ``thing``, pluralised, so a status line never reads "1 paths"."""
    return f"{number} {thing}" if number == 1 else f"{number} {thing}s"


@dataclass(frozen=True)
class RunFilters:
    """What the browser's toolbar narrows the run index by."""

    text: str = ""
    statuses: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    physics: tuple[str, ...] = ()
    limit: int = DEFAULT_LIMIT

    def apply(self, query: RunQuery) -> RunQuery:
        """The query narrowed by every active filter; the tag pairs match as alternatives, the rest as conjunctions."""
        narrowed: RunQuery = query
        if self.statuses:
            narrowed = narrowed.status(*self.statuses)

        if self.physics:
            narrowed = narrowed.where(pl.col(RunColumn.PHYSICS).is_in(list(self.physics)))

        if self.tags:
            matches = [pl.col(RunColumn.TAGS).str.json_path_match(f"$.{key}") == value for key, _, value in (pair.partition("=") for pair in self.tags)]
            narrowed = narrowed.where(pl.any_horizontal(matches).fill_null(value=False))

        if self.text.strip():
            narrowed = narrowed.text(self.text.strip())

        return narrowed.newest(self.limit).with_live()


@dataclass(frozen=True)
class RunView:
    """Everything the detail panel shows about one run bundle."""

    title: str = ""
    status: str = ""
    path: str = ""
    meta: list[Row] = field(default_factory=list)
    stages: list[Row] = field(default_factory=list)
    scalars: list[Row] = field(default_factory=list)
    components: list[Row] = field(default_factory=list)
    currents: list[Row] = field(default_factory=list)
    refs: list[Row] = field(default_factory=list)
    files: list[Row] = field(default_factory=list)
    cleanups: list[Row] = field(default_factory=list)
    fields_note: str = ""
    has_fields: bool = False


class RunBrowserPanel(TablePanel):
    """The run index as a virtual table: text, status, tag and physics filters narrow it in polars, a row click selects the run."""

    def __init__(self, app: "Workbench", id: str, title: str, *, telemetry: Telemetry) -> None:
        """Builds the browser over a telemetry root's run index.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            telemetry: The telemetry root whose run index the table lists.
        """
        self.telemetry = telemetry
        self.narrowing = RunFilters()
        self.live: set[str] = set()
        self.directories: set[str] = set()
        self.feed: Feed[pl.DataFrame | None] = Feed(self, LIVE_PERIOD_S, self.poll, self.publish)

        super().__init__(app, id, title, self.table_frame(self.index()), filterable=True, virtual=True, pick=self.on_pick)

    def query(self) -> RunQuery:
        """The run index query before the toolbar's filters."""
        return RunQuery(self.telemetry.runs.scan())

    def index(self) -> pl.DataFrame:
        """The narrowed index frame, with the live claims joined on."""
        return self.narrowing.apply(self.query()).frame()

    def table_frame(self, frame: pl.DataFrame) -> pl.DataFrame:
        """One index frame as the table's rows, remembering which of its runs are claimed and where their bundles live."""
        self.live = live_ids(frame)
        self.directories = set(cast("list[str]", frame[RunColumn.RUNS_DIR].unique().to_list()))

        return pl.DataFrame(run_rows(frame, self.live), schema=RUN_SCHEMA)

    def vocabulary(self, column: RunColumn) -> list[str]:
        """The distinct non-null values of one index column, for a filter's choices."""
        found: pl.DataFrame = self.telemetry.runs.scan().select(pl.col(column).cast(pl.String)).drop_nulls().unique().sort(column).collect()

        return cast("list[str]", found[column].to_list())

    def tag_vocabulary(self) -> list[str]:
        """Every ``key=value`` pair the indexed runs carry, deduplicated."""
        found: pl.DataFrame = self.telemetry.runs.scan().select(RunColumn.TAGS).drop_nulls().unique().collect()
        labels = {label for tags in cast("list[str]", found[RunColumn.TAGS].to_list()) for label in tag_pairs(tags)}

        return sorted(labels)

    def build(self) -> None:
        """Builds the filter toolbar over the run table and listens to the filters."""
        with html.Div(classes=str(Cls.TOOLBAR)):
            Input(model=self.key(BrowserVar.TEXT), placeholder="Search", aria_label="Search the runs", classes="h-6 w-40")
            with labelled("Status"):
                chooser(self.key(BrowserVar.STATUS), [ANY, *self.vocabulary(RunColumn.STATUS)])

            with labelled("Tag"):
                chooser(self.key(BrowserVar.TAG), [ANY, *self.tag_vocabulary()], width="w-48", label=as_written)

            with labelled("Physics"):
                chooser(self.key(BrowserVar.PHYSICS), [ANY, *self.vocabulary(RunColumn.PHYSICS)], width="w-44")

            with labelled("Newest"):
                chooser(self.key(BrowserVar.LIMIT), [str(limit) for limit in LIMITS], width="w-20")

            html.Span([react.Bind(self.key(BrowserVar.COUNT))], classes=str(Cls.STATUS))

        with html.Div(classes=str(Cls.FILL)):
            super().build()

        self.state.update(
            {
                self.key(BrowserVar.TEXT): "",
                self.key(BrowserVar.STATUS): ANY,
                self.key(BrowserVar.TAG): ANY,
                self.key(BrowserVar.PHYSICS): ANY,
                self.key(BrowserVar.LIMIT): str(DEFAULT_LIMIT),
                self.key(BrowserVar.COUNT): self.count(),
            }
        )
        _ = self.listen(
            self.key(BrowserVar.TEXT),
            self.key(BrowserVar.STATUS),
            self.key(BrowserVar.TAG),
            self.key(BrowserVar.PHYSICS),
            self.key(BrowserVar.LIMIT),
        )(self.on_narrowing)

    def count(self) -> str:
        """The status text: how many runs the table holds and how many are live."""
        return f"{self.frame.height} runs · {len(self.live)} live"

    def chosen(self, name: BrowserVar) -> tuple[str, ...]:
        """The one value a filter names, or nothing while it names ``any``."""
        value = str(self.state[self.key(name)] or ANY)

        return () if value == ANY else (value,)

    def on_narrowing(self, **_: object) -> None:
        """Re-queries the index after a toolbar filter changed."""
        self.narrowing = replace(
            self.narrowing,
            text=cast("str | None", self.state[self.key(BrowserVar.TEXT)]) or "",
            statuses=self.chosen(BrowserVar.STATUS),
            tags=self.chosen(BrowserVar.TAG),
            physics=self.chosen(BrowserVar.PHYSICS),
            limit=int(str(self.state[self.key(BrowserVar.LIMIT)])),
        )
        self.publish(self.index())

    def poll(self) -> pl.DataFrame | None:
        """Refreshed rows while a run is claimed or a claim just came or went, else None: an idle table costs one directory glob."""
        claims: set[str] = {run_id for directory in self.directories for run_id in live_ids(live_runs(directory))}
        if not claims and claims == self.live:
            return None

        return self.index()

    def publish(self, frame: pl.DataFrame | None) -> None:
        """Writes one index frame into the table."""
        if frame is None:
            return

        self.set_rows(self.table_frame(frame))
        self.state[self.key(BrowserVar.COUNT)] = self.count()

    def on_pick(self, row: Row) -> None:
        """Publishes the clicked run for the detail panel."""
        run_id = str(row.get(RunRow.RUN_ID, ""))
        if not run_id:
            return

        select_run(self.state, run_id)

        log.bind(panel=self.id, run_id=run_id).info("Run selected.")

    def on_visible(self, visible: bool) -> None:
        """Runs the live refresh only while the panel is on screen."""
        if visible:
            self.feed.start()
            return

        self.feed.stop()

    def dispose(self) -> None:
        """Stops the live refresh, then releases the panel."""
        self.feed.stop()
        super().dispose()


class RunDetailPanel(PanelComponent):
    """One run bundle: its manifest summary, stage timeline, results, artifact inputs, files and retention."""

    def __init__(self, app: "Workbench", id: str, title: str, *, telemetry: Telemetry, store: LocalArtifactStore) -> None:
        """Builds the detail panel, which follows the shared run selection.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            telemetry: The telemetry root whose run index locates a run's bundle.
            store: The artifact store a bundle's inputs resolve in and rehydrate from.
        """
        self.telemetry = telemetry
        self.store = store
        self.run: Run | None = None
        self.run_id = ""
        """The run the panel shows, which the watch re-reads while it solves."""
        self.plan: CleanupPlan | None = None
        self.watch: Feed[tuple[str, RunView]] = Feed(self, WATCH_PERIOD_S, self.reread, self.refresh)
        self.showing: asyncio.Task[None] | None = None
        """The show a rebuild started, held until it finishes."""

        super().__init__(app, id, title)

    def build(self) -> None:
        """Builds the toolbar, the bundle's sections and the purge dialog, then follows the shared run selection."""
        with html.Div(classes=str(Cls.TOOLBAR)):
            Button(
                "Fields",
                size=BUTTON_SIZE,
                variant="secondary",
                disabled=react.Bind(f"!{self.key(DetailVar.HAS_FIELDS)}"),
                title=react.Bind(self.key(DetailVar.FIELDS_NOTE)),
                on_click=self.callback(self.open_fields),
            )
            Button("Folder", size=BUTTON_SIZE, variant="secondary", on_click=self.callback(self.reveal))
            Button("Rehydrate", size=BUTTON_SIZE, variant="ghost", on_click=self.callback(self.rehydrate))
            Button("Purge", size=BUTTON_SIZE, variant="ghost", on_click=self.callback(self.open_purge))
            html.Span([react.Bind(f"{self.key(DetailVar.TITLE)} || 'No Run Selected'")], classes=str(Cls.STATUS))

        with html.Div(classes=f"{Cls.COLUMN} {Cls.SCROLL}"):
            self.build_header()
            self.build_stages()
            self.build_scalars()
            self.build_tables()

        self.build_purge_dialog()

        self.state.update(
            {
                Shared.SELECTED_RUN_ID: self.state.setdefault(Shared.SELECTED_RUN_ID, ""),
                self.key(DetailVar.COMPONENT_COLUMNS): columns(
                    (ComponentColumn.COMPONENT, "Component"),
                    (ComponentColumn.FX, "Fx [N]", True),
                    (ComponentColumn.FY, "Fy [N]", True),
                    (ComponentColumn.FZ, "Fz [N]", True),
                    (ComponentColumn.FORCE, "|F| [N]", True),
                    (ComponentColumn.TORQUE, "|M| [N·m]", True),
                ),
                self.key(DetailVar.CURRENT_COLUMNS): columns((CurrentColumn.TERMINAL, "Terminal"), (CurrentColumn.AMPS, "I [A]", True)),
                self.key(DetailVar.FILE_COLUMNS): columns(
                    (BundleFileColumn.PATH, "File"),
                    (BundleFileColumn.SIZE, "Bytes", True),
                    (BundleFileColumn.FILE_CLASS, "Class"),
                    (BundleFileColumn.CACHED, "Cached"),
                ),
                self.key(DetailVar.CLEANUP_COLUMNS): columns(
                    (CleanupColumn.APPLIED, "Applied"),
                    (CleanupColumn.TRIGGER, "Trigger"),
                    (CleanupColumn.FILES, "Files", True),
                    (CleanupColumn.MB, "MB", True),
                    (CleanupColumn.CLASSES, "Classes"),
                ),
            }
        )
        self.publish(RunView())
        _ = self.listen(str(Shared.SELECTED_RUN_ID))(self.on_selected)

    def build_header(self) -> None:
        """Builds the title, the status badge, the bundle path and the manifest summary."""
        with html.Div(classes=str(Cls.SECTION)):
            html.Span([react.Bind(self.key(DetailVar.TITLE))])
            with react.If(self.key(DetailVar.STATUS)):
                Badge([react.Bind(self.key(DetailVar.STATUS))], variant=react.Bind(self.key(DetailVar.STATUS_VARIANT)))

        html.Div([react.Bind(self.key(DetailVar.PATH))], classes=f"{Cls.MONO} {Cls.CAPTION} px-2")
        build_pairs(self.key(DetailVar.META))

    def build_stages(self) -> None:
        """Builds the stage timeline, shown once the run has stages."""
        with react.If(f"{self.key(DetailVar.STAGES)}.length"):
            html.Div("Stages", classes=str(Cls.SECTION))
            with html.Ol(classes=str(Cls.TIMELINE)), react.For(items=self.key(DetailVar.STAGES), name=ROW):
                with html.Li(classes=str(Cls.TIMELINE_ITEM)):
                    html.Span(classes=react.Bind(f"{ROW}.{StageField.DOT}"))
                    html.Span([react.Bind(f"{ROW}.{StageField.STAGE}")])
                    html.Span([react.Bind(f"{ROW}.{StageField.SECONDS}"), " s · ", react.Bind(f"{ROW}.{StageField.STATE}")], classes=str(Cls.CAPTION))

    def build_scalars(self) -> None:
        """Builds the result scalars, shown once the run has a result."""
        with react.If(f"{self.key(DetailVar.SCALARS)}.length"):
            html.Div("Result", classes=str(Cls.SECTION))
            build_pairs(self.key(DetailVar.SCALARS))

    def build_tables(self) -> None:
        """Builds the component forces, currents, inputs, files and retention passes, each shown once it has rows."""

        def table(label: str, rows: DetailVar, spec: DetailVar) -> None:
            with react.If(f"{self.key(rows)}.length"):
                html.Div(label, classes=str(Cls.SECTION))
                build_table(self.key(spec), self.key(rows))

        table("Component Forces", DetailVar.COMPONENTS, DetailVar.COMPONENT_COLUMNS)
        table("Currents", DetailVar.CURRENTS, DetailVar.CURRENT_COLUMNS)

        with react.If(f"{self.key(DetailVar.REFS)}.length"):
            html.Div("Store-Sourced Inputs", classes=str(Cls.SECTION))
            with html.Div(classes=str(Cls.CHIPS)), react.For(items=self.key(DetailVar.REFS), name=REF):
                Badge(
                    [react.Bind(f"{REF}.{RefColumn.KIND}"), "/", react.Bind(f"{REF}.{RefColumn.NAME}")],
                    variant="outline",
                    classes="cursor-pointer",
                    title=react.Bind(f"{REF}.{RefColumn.PATH}"),
                    on_click=self.callback(self.open_ref, args=f"[{REF}.{RefColumn.PATH}]"),
                )

        table("Files", DetailVar.FILES, DetailVar.FILE_COLUMNS)
        table("Retention", DetailVar.CLEANUPS, DetailVar.CLEANUP_COLUMNS)

    def build_purge_dialog(self) -> None:
        """Builds the purge dialog: a preset, a rule per retention class, and the preview and delete actions."""
        with Dialog(open_=react.Bind(self.key(PurgeVar.DIALOG)), on_open_change=self.callback(self.on_dialog, args="[$event]")), DialogContent(classes="max-w-2xl"):
            with DialogHeader():
                DialogTitle("Purge This Bundle")

            with html.Div(classes=str(Cls.COLUMN)):
                with labelled("Preset"):
                    chooser(self.key(PurgeVar.PRESET), [preset.value for preset in Preset])

                with html.Div(classes=str(Cls.RULES)):
                    for file_class in POLICY_CLASSES:
                        html.Span(file_class.value, classes=str(Cls.CAPTION))
                        chooser(self.rule_key(file_class), [rule.value for rule in Cleanup])
                        html.Span([react.Bind(self.bytes_key(file_class))], classes=str(Cls.MONO))

                html.Div([react.Bind(self.key(PurgeVar.TOTAL))], classes=str(Cls.CAPTION))

            with DialogFooter():
                Button("Close", size="sm", variant="secondary", on_click=self.callback(self.close_purge))
                Button("Preview", size="sm", variant="secondary", on_click=self.callback(self.preview_purge))
                Button(
                    "Delete",
                    size="sm",
                    variant="destructive",
                    disabled=react.Bind(f"!{self.key(PurgeVar.PLANNED)}"),
                    on_click=self.callback(self.apply_purge),
                )

        self.state[self.key(PurgeVar.DIALOG)] = False
        self.state[self.key(PurgeVar.PRESET)] = Preset.DEFAULT.value
        self.load_preset(Preset.DEFAULT)
        _ = self.listen(self.key(PurgeVar.PRESET))(self.on_preset)

    def rule_key(self, file_class: RunFile) -> str:
        """The state variable holding one retention class's rule in the purge dialog."""
        return self.key(f"rule_{file_class.value}")

    def bytes_key(self, file_class: RunFile) -> str:
        """The state variable holding one retention class's planned size in the purge dialog."""
        return self.key(f"bytes_{file_class.value}")

    def load_preset(self, preset: Preset) -> None:
        """Fills every per-class rule from a preset and clears the last preview."""
        policy: RetentionPolicy = RetentionPolicy.from_preset(preset)
        self.plan = None
        self.state.update({self.rule_key(file_class): rule.value for file_class, rule in policy.rules()})
        self.state.update({self.bytes_key(file_class): "" for file_class in POLICY_CLASSES})
        self.state.update({self.key(PurgeVar.PLANNED): False, self.key(PurgeVar.TOTAL): "preview to see what would go"})

    def policy(self) -> RetentionPolicy:
        """The policy the dialog's per-class choices describe."""
        rules = {file_class.value: Cleanup(str(self.state[self.rule_key(file_class)])) for file_class in POLICY_CLASSES}

        return RetentionPolicy(**rules)

    def on_preset(self, **_: object) -> None:
        """Fills the per-class rules from the preset just chosen."""
        self.load_preset(Preset(str(self.state[self.key(PurgeVar.PRESET)])))

    def locate(self, run_id: str) -> Path | None:
        """The bundle directory of an indexed run, or None when the index does not know it."""
        found: pl.DataFrame = RunQuery(self.telemetry.runs.scan()).where(pl.col(RunColumn.RUN_ID) == run_id).frame()
        if found.height == 0:
            return None

        directories = cast("list[str]", found[RunColumn.RUNS_DIR].to_list())

        return Path(directories[0]) / run_id

    def load(self, run_id: str) -> RunView:
        """Read one bundle into what the panel shows; the whole read happens off the server loop."""
        sim_dir: Path | None = self.locate(run_id)
        if sim_dir is None or not sim_dir.is_dir():
            self.run = None

            return RunView(title=run_id, status="gone", path=str(sim_dir or ""), fields_note="the bundle directory is gone")

        run = Run(sim_dir)
        self.run = run
        try:
            manifest: RunManifest = run.manifest
        except FileNotFoundError:
            return RunView(title=run_id, status=STARTING, path=str(sim_dir), fields_note="the run has not written its manifest yet")

        purged: CleanupRecord | None = run.purged(RunFile.FIELDS)
        note: str = f"fields purged on {purged.applied_at:%Y-%m-%d} by {purged.trigger.value}" if purged is not None else "open the solved fields"

        return RunView(
            title=manifest.run_id,
            status=manifest.status,
            path=str(sim_dir),
            meta=self.summary(manifest),
            stages=stage_rows(manifest.stage_history, datetime.now(timezone.utc), status=manifest.status),
            scalars=scalar_rows(run.result) if manifest.status != "running" else [],
            components=component_rows(run),
            currents=current_rows(run),
            refs=ref_rows(manifest.artifacts, self.store),
            files=file_rows(bundle_inventory(sim_dir), manifest.cached_inputs()),
            cleanups=cleanup_rows(manifest.cleanups),
            fields_note=note,
            has_fields=run.vtu_path is not None,
        )

    def summary(self, manifest: RunManifest) -> list[Row]:
        """The manifest's identity and provenance as label/value pairs."""
        meta = manifest.config.meta
        stats = manifest.mesh_stats

        return [
            pair("created", f"{manifest.created_at:%Y-%m-%d %H:%M:%S} UTC"),
            pair("physics", title(manifest.physics.value)),
            pair("description", meta.description or ""),
            pair("tags", ", ".join(f"{key}={value}" for key, value in meta.tags.items())),
            pair("cache key", manifest.cache_key[:KEY_CHARS]),
            pair("mesh", f"{manifest.mesh_name} ({manifest.mesh_key[:KEY_CHARS]})"),
            pair("elements", stats.element_count if stats is not None else ""),
            pair("elmer", manifest.elmer_version or ""),
            pair("code", manifest.git_sha or ""),
            pair("published", manifest.published.name if manifest.published is not None else "no"),
        ]

    def publish(self, view: RunView) -> None:
        """Writes one view into the panel's state variables."""
        self.state.update(
            {
                self.key(DetailVar.TITLE): view.title,
                self.key(DetailVar.STATUS): title(view.status),
                self.key(DetailVar.STATUS_VARIANT): StatusVariant.of(view.status),
                self.key(DetailVar.PATH): view.path,
                self.key(DetailVar.META): list(view.meta),
                self.key(DetailVar.STAGES): list(view.stages),
                self.key(DetailVar.SCALARS): list(view.scalars),
                self.key(DetailVar.COMPONENTS): list(view.components),
                self.key(DetailVar.CURRENTS): list(view.currents),
                self.key(DetailVar.REFS): list(view.refs),
                self.key(DetailVar.FILES): list(view.files),
                self.key(DetailVar.CLEANUPS): list(view.cleanups),
                self.key(DetailVar.FIELDS_NOTE): view.fields_note,
                self.key(DetailVar.HAS_FIELDS): view.has_fields,
            }
        )

    async def on_selected(self, **_: object) -> None:
        """Shows the run the shared selection names."""
        run_id = str(self.state[Shared.SELECTED_RUN_ID] or "")
        if not run_id:
            return

        await self.show(run_id)

    async def show(self, run_id: str) -> None:
        """Loads a bundle off the server loop and publishes it, re-reading it while it solves; a failed read lands in the status line."""
        self.run_id = run_id
        self.watch.stop()
        try:
            view: RunView = await asyncio.to_thread(self.load, run_id)
        except Exception as error:
            log.opt(exception=True).warning("Run bundle could not be read.")
            self.status(f"{type(error).__name__}: {error}")
            return

        if run_id != self.run_id:
            return

        with self.state:
            self.publish(view)
            self.load_preset(Preset(str(self.state[self.key(PurgeVar.PRESET)])))

        if view.status in WATCHED:
            self.watch.start()

    def reread(self) -> tuple[str, RunView]:
        """The shown run read again, named so a reply that outlives its selection is dropped."""
        run_id = self.run_id

        return run_id, self.load(run_id)

    def refresh(self, reply: tuple[str, RunView]) -> None:
        """Publishes a re-read of the shown run, and ends the watch once the run has."""
        run_id, view = reply
        if run_id != self.run_id:
            return

        self.publish(view)
        if view.status not in WATCHED:
            self.watch.stop()
            log.bind(panel=self.id, run_id=run_id, status=view.status).info("Run watch ended.")

    def selected(self) -> Run | None:
        """The run the panel shows, reporting in the status line when there is none."""
        if self.run is None:
            self.status("select a run first")

        return self.run

    def open_fields(self) -> None:
        """Hands the bundle to the field viewer, or says so when no viewer is registered."""
        run: Run | None = self.selected()
        if run is None:
            return

        hook = self.ctrl[Hook.FIELD_OPEN]
        if not hook.exists():
            self.status("no field viewer in this workbench")
            return

        _ = hook(run.path)

    def open_ref(self, path: str) -> None:
        """Selects a store-sourced input, so the artifact detail follows."""
        self.state[Shared.SELECTED_ARTIFACT_PATH] = path

    def reveal(self) -> None:
        """Opens the bundle's directory in the file manager."""
        run: Run | None = self.selected()
        if run is None:
            return

        open_folder(run.path)

    def rehydrate(self) -> None:
        """Fetches the bundle's store-sourced inputs back into its directory."""
        run: Run | None = self.selected()
        if run is None:
            return

        try:
            handles = rehydrate_bundle(run.path, ArtifactCache(self.store))
        except Exception as error:
            log.opt(exception=True).warning("Rehydrate failed.")
            self.status(f"{type(error).__name__}: {error}")
            return

        names = [name for handle in handles for name in handle.files]
        self.status(f"rehydrated {counted(len(names), 'file')} into {run.path.name}")

    def open_purge(self) -> None:
        """Opens the purge dialog when the panel shows a run."""
        if self.selected() is None:
            return

        self.state[self.key(PurgeVar.DIALOG)] = True

    def close_purge(self) -> None:
        """Closes the purge dialog."""
        self.state[self.key(PurgeVar.DIALOG)] = False

    def on_dialog(self, open_: object) -> None:
        """Follows the dialog the client closed with Escape or the backdrop."""
        self.state[self.key(PurgeVar.DIALOG)] = bool(open_)

    def preview_purge(self) -> None:
        """Plans the dialog's policy against the bundle and shows the bytes each class would free."""
        run: Run | None = self.selected()
        if run is None:
            return

        plan: CleanupPlan = plan_cleanup(run.path, self.policy(), status=run.manifest.status, cached=run.manifest.cached_inputs())
        self.plan = plan
        sizes = plan.bytes_by_class()
        self.state.update({self.bytes_key(file_class): megabytes(sizes.get(file_class, 0)) for file_class in POLICY_CLASSES})
        self.state.update(
            {
                self.key(PurgeVar.PLANNED): True,
                self.key(PurgeVar.TOTAL): f"{counted(len(plan.entries), 'path')} · {megabytes(plan.total_bytes)} would go",
            }
        )

        log.bind(run_id=run.path.name, paths=len(plan.entries), bytes=plan.total_bytes).info("Purge previewed.")

    def apply_purge(self) -> None:
        """Applies the previewed policy to the bundle and reloads the panel from what is left."""
        run: Run | None = self.selected()
        if run is None or self.plan is None:
            self.status("preview the purge first")
            return

        report = cleanup_bundle(run.path, self.plan.policy, dry_run=False, cached=run.manifest.cached_inputs())
        self.state[self.key(PurgeVar.DIALOG)] = False
        self.status(f"{counted(len(report.removed), 'path')} removed · {megabytes(report.bytes_freed)}")
        self.plan = None
        self.publish(self.load(run.path.name))

        log.bind(run_id=run.path.name, paths=len(report.removed), bytes=report.bytes_freed).info("Bundle purged.")

    def succeed(self, previous: PanelComponent) -> None:
        """Shows the selected run again, which a fresh build would leave blank."""
        run_id = str(self.state[Shared.SELECTED_RUN_ID] or "")
        if not run_id:
            return

        self.showing = create_task(self.show(run_id))

    def dispose(self) -> None:
        """Stops the watch, then releases the panel."""
        self.watch.stop()
        super().dispose()
