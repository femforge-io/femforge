"""The artifact browser: the telemetry artifact index in a table, with one artifact's record, files and spec beside it."""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, cast

import polars as pl
from trame.widgets import html, react

from femforge.artifacts.cache import ArtifactCache
from femforge.artifacts.local import LocalArtifactStore, StoredArtifact, store_root_of
from femforge.artifacts.sidecar import ArtifactRecord, ArtifactRef
from femforge.common.files import BundleFile
from femforge.common.log import log
from femforge.common.utils import title
from femforge.run.artifacts import rehydrate_bundle
from femforge.run.manifest import RunManifest
from femforge.telemetry.artifacts import ArtifactQuery
from femforge.telemetry.paths import Telemetry
from femforge.telemetry.schemas import ArtifactColumn, RunColumn
from femforge_workbench.grid import Row, group_rows
from femforge_workbench.panels import Cls, PanelComponent, Shared, chooser, labelled, select_run
from femforge_workbench.panels.outputs import EDITOR_OPTIONS
from femforge_workbench.panels.table import TablePanel
from femforge_workbench.widgets.badge import Badge
from femforge_workbench.widgets.button import Button
from femforge_workbench.widgets.code import Code
from femforge_workbench.widgets.dialog import Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle
from femforge_workbench.widgets.icon import Icon
from femforge_workbench.widgets.input import Input
from femforge_workbench.widgets.switch import Switch
from femforge_workbench.widgets.table import ColumnSpec, Table


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


KEY_CHARS = 12
"""How much of a content key a listing shows; the detail panel shows all of it."""

SPEC_FIELDS = 3
"""How many scalar spec fields the table's summary column carries."""

SHA_CHARS = 12
CHIP_LIMIT = 24
"""How many "used by" run badges are rendered before the count stands in for the rest."""

MATERIALIZE_DIR = "materialized"
ANY = "any"
"""The choice a single-value filter offers for "do not narrow by this"."""


def as_written(value: str) -> str:
    """A filter choice that is data, such as a tag pair or a process name, read as written; only the catch-all takes Title Case."""
    return title(value) if value == ANY else value


BUTTON_SIZE = "xs"
ICON_SIZE = "icon-xs"
PAIR = "pair"
USE = "run_id"
JSON_LANGUAGE = "json"
SPEC_HEIGHT = "h-64"


class ArtifactRow(StrEnum):
    """The table's columns; ``path`` rides along hidden so a row click can name the artifact."""

    KIND = "kind"
    NAME = "name"
    KEY = "key12"
    CREATED = "created"
    MB = "MB"
    FILES = "files"
    HOST = "host"
    SPEC = "spec"
    PATH = "path"


class PairField(StrEnum):
    """The keys of a label/value row; every detail panel renders its pair lists through these."""

    LABEL = "label"
    VALUE = "value"


class FileColumn(StrEnum):
    """The columns of one artifact file row."""

    NAME = "name"
    SIZE = "size"
    SHA = "sha"


class RefColumn(StrEnum):
    """The columns of one store-sourced input row, in the artifact detail and in the run detail alike."""

    KIND = "kind"
    NAME = "name"
    KEY = "key"
    ORIGIN = "origin"
    FILES = "files"
    PATH = "path"


class BrowserVar(StrEnum):
    """The artifact browser's per-panel state variables."""

    KIND = "kind"
    TEXT = "text"
    COUNT = "count"


class DetailVar(StrEnum):
    """The artifact detail's per-panel state variables."""

    TITLE = "title"
    KEY = "key"
    PATH = "path"
    META = "meta"
    FILES = "files"
    FILE_COLUMNS = "file_columns"
    SPEC = "spec"
    EDITOR = "editor"
    REFS = "refs"
    REF_COLUMNS = "ref_columns"
    USES = "uses"
    RUN_ID = "run_id"
    DIALOG = "dialog"
    INTO = "into"
    VERIFY = "verify"


def spec_summary(spec_type: str, spec: str) -> str:
    """The spec model's bare class name followed by its first scalar fields, as one table cell."""
    name: str = spec_type.rsplit(":", 1)[-1]
    try:
        payload = cast("object", json.loads(spec))
    except ValueError:
        return name

    if not isinstance(payload, dict):
        return name

    def shown(value: object) -> str:
        return f"{value:g}" if isinstance(value, float) else str(value)

    fields = cast("dict[str, object]", payload).items()
    scalars = [f"{key}={shown(value)}" for key, value in fields if isinstance(value, (str, int, float, bool))]
    if not scalars:
        return name

    return f"{name}  {', '.join(scalars[:SPEC_FIELDS])}"


def artifact_rows(frame: pl.DataFrame) -> list[Row]:
    """The listing rows of an artifact index frame, in frame order."""

    def moment(value: object) -> str:
        return value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime) else ""

    def megabytes(value: object) -> float:
        return round(float(cast("float", value)) / 1e6, 1) if isinstance(value, (int, float)) else 0.0

    rows: list[Row] = []
    for row in cast("list[dict[str, object]]", frame.rows(named=True)):
        kind = str(row[ArtifactColumn.KIND])
        name = str(row[ArtifactColumn.NAME])
        rows.append(
            {
                ArtifactRow.KIND: kind,
                ArtifactRow.NAME: name,
                ArtifactRow.KEY: str(row[ArtifactColumn.KEY])[:KEY_CHARS],
                ArtifactRow.CREATED: moment(row[ArtifactColumn.CREATED_AT]),
                ArtifactRow.MB: megabytes(row[ArtifactColumn.TOTAL_BYTES]),
                ArtifactRow.FILES: row[ArtifactColumn.FILE_COUNT],
                ArtifactRow.HOST: str(row[ArtifactColumn.PRODUCER_HOST] or ""),
                ArtifactRow.SPEC: spec_summary(str(row[ArtifactColumn.SPEC_TYPE] or ""), str(row[ArtifactColumn.SPEC] or "")),
                ArtifactRow.PATH: str(Path(str(row[ArtifactColumn.STORE_ROOT])) / kind / name),
            }
        )

    return rows


def runs_using(runs: pl.LazyFrame, key: str) -> list[str]:
    """The ids of the runs whose manifest names ``key`` among its store-sourced inputs or as its mesh."""
    consumed = pl.col(RunColumn.ARTIFACT_KEYS).list.contains(key).fill_null(value=False)
    meshed = (pl.col(RunColumn.MESH_KEY) == key).fill_null(value=False)
    found: pl.DataFrame = runs.filter(consumed | meshed).select(RunColumn.RUN_ID).unique().sort(RunColumn.RUN_ID).collect()

    return cast("list[str]", found[RunColumn.RUN_ID].to_list())


def open_folder(path: Path) -> None:
    """Shows a path in the desktop's file manager; a failure is logged, never raised into wslink."""
    target: Path = path if path.is_dir() else path.parent
    try:
        if sys.platform == "win32":
            os.startfile(target)
            return

        opener = "open" if sys.platform == "darwin" else "xdg-open"
        _ = subprocess.Popen([opener, str(target)])
    except OSError as error:
        log.bind(path=str(target), error=str(error)).warning("Folder could not be opened.")


@dataclass(frozen=True)
class ArtifactFilters:
    """What the browser's toolbar narrows the artifact index by."""

    kinds: tuple[str, ...] = ()
    text: str = ""

    def apply(self, query: ArtifactQuery) -> ArtifactQuery:
        """The query narrowed by the selected kinds and by the needle, matched against name, key, spec type and spec JSON."""
        narrowed: ArtifactQuery = query.kind(*self.kinds) if self.kinds else query
        needle: str = self.text.strip().casefold()
        if not needle:
            return narrowed

        haystack = pl.concat_str(
            [
                pl.col(ArtifactColumn.NAME),
                pl.col(ArtifactColumn.KEY),
                pl.col(ArtifactColumn.SPEC_TYPE).fill_null(""),
                pl.col(ArtifactColumn.SPEC).fill_null(""),
            ],
            separator=" ",
        )

        return narrowed.where(haystack.str.to_lowercase().str.contains(needle, literal=True))


@dataclass(frozen=True)
class ArtifactView:
    """Everything the detail panel shows about one stored artifact."""

    title: str = ""
    key: str = ""
    path: str = ""
    meta: list[Row] = field(default_factory=list)
    files: list[Row] = field(default_factory=list)
    spec: str = ""
    refs: list[Row] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    run_id: str = ""


def pair(label: str, value: object) -> Row:
    """One label/value row of a detail panel's pair list."""
    return {PairField.LABEL: title(label), PairField.VALUE: value}


def provenance(record: ArtifactRecord) -> list[Row]:
    """The record's label/value pairs, in the order the detail header reads them."""
    tools = record.tools

    return [
        pair("created", f"{record.created_at:%Y-%m-%d %H:%M:%S} UTC"),
        pair("producer", f"{record.producer_host} pid {record.producer_pid}"),
        pair("spec type", record.spec_type),
        pair("producer version", str(record.producer_version)),
        pair("files", f"{len(record.files)} · {record.total_bytes / 1e6:.1f} MB"),
        pair("tools", f"femforge {tools.femforge} · code {tools.code_sha} · gmsh {tools.gmsh} · elmer {tools.elmer}"),
    ]


def file_rows(record: ArtifactRecord) -> list[Row]:
    """One row per file of the artifact: name, bytes and the head of its hash."""
    return [{FileColumn.NAME: entry.name, FileColumn.SIZE: entry.size, FileColumn.SHA: entry.sha256[:SHA_CHARS]} for entry in record.files]


def ref_rows(refs: Sequence[ArtifactRef], store: LocalArtifactStore) -> list[Row]:
    """A bundle's store-sourced inputs, each resolved to the store directory that still holds it."""
    rows: list[Row] = []
    for ref in refs:
        matches: list[StoredArtifact] = store.locate(ref.key)
        rows.append(
            {
                RefColumn.KIND: ref.kind,
                RefColumn.NAME: ref.name,
                RefColumn.KEY: ref.key[:KEY_CHARS],
                RefColumn.ORIGIN: ref.origin.value,
                RefColumn.FILES: len(ref.files),
                RefColumn.PATH: str(matches[0].path) if matches else "not in store",
            }
        )

    return rows


def columns(*specs: tuple[str, str] | tuple[str, str, bool]) -> list[ColumnSpec]:
    """Table columns from ``(field, title)`` pairs; a third element marks the column numeric, so it sorts and filters as one."""

    def column(spec: tuple[str, str] | tuple[str, str, bool]) -> ColumnSpec:
        if len(spec) == 2:
            return ColumnSpec(field=spec[0], title=title(spec[1]))

        return ColumnSpec(field=spec[0], title=title(spec[1]), numeric=spec[2])

    return [column(spec) for spec in specs]


def build_pairs(items: str) -> None:
    """Renders a label/value grid from the state variable ``items`` into the layout under construction."""
    with html.Div(classes=str(Cls.PAIRS)), react.For(items=items, name=PAIR):
        html.Span([react.Bind(f"{PAIR}.{PairField.LABEL}")], classes=str(Cls.CAPTION))
        html.Span([react.Bind(f"{PAIR}.{PairField.VALUE}")])


def build_table(columns_key: str, rows_key: str) -> None:
    """Renders a detail section's table, bound to the state variables holding its columns and rows."""
    with html.Div(classes=str(Cls.TABLE_BOX)):
        Table(columns=react.Bind(columns_key), rows=react.Bind(rows_key))


def copy_handler(key: str) -> react.Callback:
    """A client handler copying a state variable's text; the clipboard is only reachable from the browser."""
    return react.Callback(f"navigator.clipboard.writeText(trame.state.get({json.dumps(key)}))")


REF_COLUMNS: tuple[tuple[str, str], ...] = (
    (RefColumn.KIND, "Kind"),
    (RefColumn.NAME, "Name"),
    (RefColumn.KEY, "Key"),
    (RefColumn.ORIGIN, "Origin"),
    (RefColumn.FILES, "Files"),
    (RefColumn.PATH, "Store Path"),
)
"""The store-sourced input columns both detail panels show."""

ROW_SCHEMA = pl.Schema(
    {
        ArtifactRow.KIND: pl.String,
        ArtifactRow.NAME: pl.String,
        ArtifactRow.KEY: pl.String,
        ArtifactRow.CREATED: pl.String,
        ArtifactRow.MB: pl.Float64,
        ArtifactRow.FILES: pl.Int32,
        ArtifactRow.HOST: pl.String,
        ArtifactRow.SPEC: pl.String,
        ArtifactRow.PATH: pl.String,
    }
)
"""The table frame's columns, so an empty listing still builds the same table."""


class ArtifactBrowserPanel(TablePanel):
    """The artifact index as a table: a kind choice and a text filter narrow it in polars, a row click selects the artifact."""

    def __init__(self, app: "Workbench", id: str, title: str, *, telemetry: Telemetry, store: LocalArtifactStore) -> None:
        """Builds the browser over a telemetry root's artifact index.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            telemetry: The telemetry root whose artifact index the table lists.
            store: The artifact store the index is queried against.
        """
        self.telemetry = telemetry
        self.store = store
        self.narrowing = ArtifactFilters()

        super().__init__(
            app,
            id,
            title,
            self.rows(),
            grouped=True,
            filterable=True,
            virtual=True,
            lead=[ArtifactRow.KIND],
            hidden=[ArtifactRow.PATH],
            pick=self.on_pick,
        )

    def kinds(self) -> list[str]:
        """Every kind the index holds, for the choice."""
        found: pl.DataFrame = self.telemetry.artifacts.scan().select(ArtifactColumn.KIND).unique().sort(ArtifactColumn.KIND).collect()

        return cast("list[str]", found[ArtifactColumn.KIND].to_list())

    def query(self) -> ArtifactQuery:
        """The artifact index query before the toolbar's filters."""
        return ArtifactQuery(self.telemetry.artifacts.scan(), self.store)

    def rows(self) -> pl.DataFrame:
        """The filtered index as table rows, labelled for the kind grouping."""
        frame: pl.DataFrame = self.narrowing.apply(self.query()).frame()
        rows = pl.DataFrame(artifact_rows(frame), schema=ROW_SCHEMA)

        return group_rows(rows, ArtifactRow.KIND, max_=[ArtifactRow.MB])

    def build(self) -> None:
        """Builds the kind and text filters over the artifact table and listens to them."""
        with html.Div(classes=str(Cls.TOOLBAR)):
            with labelled("Kind"):
                chooser(self.key(BrowserVar.KIND), [ANY, *self.kinds()])

            Input(model=self.key(BrowserVar.TEXT), placeholder="Filter", aria_label="Filter the artifacts", classes="h-6 w-40")
            html.Span([react.Bind(self.key(BrowserVar.COUNT))], classes=str(Cls.STATUS))

        with html.Div(classes=str(Cls.FILL)):
            super().build()

        self.state.update({self.key(BrowserVar.KIND): ANY, self.key(BrowserVar.TEXT): "", self.key(BrowserVar.COUNT): self.count()})
        _ = self.listen(self.key(BrowserVar.KIND), self.key(BrowserVar.TEXT))(self.on_narrowing)

    def count(self) -> str:
        """The status text: how many artifacts the table holds."""
        return f"{self.frame.height} artifacts"

    def on_narrowing(self, **_: object) -> None:
        """Re-queries the index after the kind or text filter changed."""
        kind = str(self.state[self.key(BrowserVar.KIND)] or ANY)
        text = cast("str | None", self.state[self.key(BrowserVar.TEXT)]) or ""
        self.narrowing = replace(self.narrowing, kinds=() if kind == ANY else (kind,), text=text)
        self.set_rows(self.rows())
        self.state[self.key(BrowserVar.COUNT)] = self.count()

    def on_pick(self, row: Row) -> None:
        """Publishes the clicked artifact's store directory for the detail panel."""
        path = str(row.get(ArtifactRow.PATH, ""))
        if not path:
            return

        with self.state:
            self.state[Shared.SELECTED_ARTIFACT_PATH] = path

        log.bind(panel=self.id, path=path).info("Artifact selected.")


class ArtifactDetailPanel(PanelComponent):
    """One artifact read from the store: its key, provenance, files, spec, inputs, and the runs that used it."""

    def __init__(self, app: "Workbench", id: str, title: str, *, telemetry: Telemetry, store: LocalArtifactStore) -> None:
        """Builds the detail panel, which follows the shared artifact selection.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            telemetry: The telemetry root whose run index lists the runs that used an artifact.
            store: The artifact store the artifacts are read, materialized and rehydrated from.
        """
        self.telemetry = telemetry
        self.store = store
        self.artifact: StoredArtifact | None = None

        super().__init__(app, id, title)

    def build(self) -> None:
        """Builds the toolbar, the artifact's sections and the materialize dialog, then follows the shared artifact selection."""
        has_run = self.key(DetailVar.RUN_ID)
        with html.Div(classes=str(Cls.TOOLBAR)):
            Button("Folder", size=BUTTON_SIZE, variant="secondary", on_click=self.callback(self.reveal))
            Button("Materialize", size=BUTTON_SIZE, variant="secondary", on_click=self.callback(self.open_dialog))
            Button("Rehydrate", size=BUTTON_SIZE, variant="ghost", disabled=react.Bind(f"!{has_run}"), on_click=self.callback(self.rehydrate))
            Button("Open Run", size=BUTTON_SIZE, variant="ghost", disabled=react.Bind(f"!{has_run}"), on_click=self.callback(self.open_run))
            html.Span([react.Bind(f"{self.key(DetailVar.TITLE)} || 'No Artifact Selected'")], classes=str(Cls.STATUS))

        with html.Div(classes=f"{Cls.COLUMN} {Cls.SCROLL}"):
            self.build_header()
            build_pairs(self.key(DetailVar.META))
            self.build_uses()
            html.Div("Files", classes=str(Cls.SECTION))
            build_table(self.key(DetailVar.FILE_COLUMNS), self.key(DetailVar.FILES))
            with react.If(f"{self.key(DetailVar.REFS)}.length"):
                html.Div("Store-sourced inputs", classes=str(Cls.SECTION))
                build_table(self.key(DetailVar.REF_COLUMNS), self.key(DetailVar.REFS))

            self.build_spec()

        self.build_dialog()

        self.state.update(
            {
                Shared.SELECTED_ARTIFACT_PATH: self.state.setdefault(Shared.SELECTED_ARTIFACT_PATH, ""),
                self.key(DetailVar.FILE_COLUMNS): columns((FileColumn.NAME, "File"), (FileColumn.SIZE, "Bytes", True), (FileColumn.SHA, "sha256")),
                self.key(DetailVar.REF_COLUMNS): columns(*REF_COLUMNS),
                self.key(DetailVar.EDITOR): EDITOR_OPTIONS,
                self.key(DetailVar.DIALOG): False,
                self.key(DetailVar.INTO): str(Path.cwd() / MATERIALIZE_DIR),
                self.key(DetailVar.VERIFY): True,
            }
        )
        self.publish(ArtifactView())
        _ = self.listen(str(Shared.SELECTED_ARTIFACT_PATH))(self.on_selected)

    def build_header(self) -> None:
        """Builds the title with its copy button, the full key and the store path."""
        with html.Div(classes=str(Cls.SECTION)):
            html.Span([react.Bind(self.key(DetailVar.TITLE))])
            with Button(
                size=ICON_SIZE,
                variant="ghost",
                title="Copy the full key",
                aria_label="Copy the full key",
                on_click=copy_handler(self.key(DetailVar.KEY)),
            ):
                Icon("copy")

        html.Div([react.Bind(self.key(DetailVar.KEY))], classes=f"{Cls.MONO} {Cls.CAPTION} px-2")
        html.Div([react.Bind(self.key(DetailVar.PATH))], classes=f"{Cls.MONO} {Cls.CAPTION} px-2")

    def build_uses(self) -> None:
        """Builds a badge per run that used the artifact."""
        html.Div("Used by Runs", classes=str(Cls.SECTION))
        with html.Div(classes=str(Cls.CHIPS)):
            with react.For(items=self.key(DetailVar.USES), name=USE):
                Badge([react.Bind(USE)], variant="outline", classes="cursor-pointer", on_click=self.callback(self.open_use, args=f"[{USE}]"))

            with react.If(f"!{self.key(DetailVar.USES)}.length"):
                html.Span("None", classes=str(Cls.CAPTION))

    def build_spec(self) -> None:
        """Builds the read-only JSON view of the artifact's spec."""
        html.Div("Spec", classes=str(Cls.SECTION))
        with html.Div(classes=f"{SPEC_HEIGHT} shrink-0 px-2 pb-2"):
            Code(
                value=react.Bind(self.key(DetailVar.SPEC)),
                language=JSON_LANGUAGE,
                theme=self.app.palette.monaco_theme,
                options=react.Bind(self.key(DetailVar.EDITOR)),
            )

    def build_dialog(self) -> None:
        """Builds the materialize dialog: the target directory and the hash check."""
        with Dialog(open_=react.Bind(self.key(DetailVar.DIALOG)), on_open_change=self.callback(self.on_dialog, args="[$event]")), DialogContent(classes="max-w-xl"):
            with DialogHeader():
                DialogTitle("Materialize the artifact")

            with html.Div(classes=str(Cls.COLUMN)):
                with labelled("Into"):
                    Input(model=self.key(DetailVar.INTO), classes="h-7 w-96")

                with labelled("Verify every sha256"):
                    Switch(model=self.key(DetailVar.VERIFY), size="sm")

            with DialogFooter():
                Button("Cancel", size="sm", variant="secondary", on_click=self.callback(self.close_dialog))
                Button("Materialize", size="sm", on_click=self.callback(self.materialize))

    def load(self, path: Path) -> ArtifactView:
        """Read one artifact's directory into what the panel shows; a directory with no readable sidecar clears the panel."""
        artifact: StoredArtifact | None = self.store.read(path)
        self.artifact = artifact
        if artifact is None:
            return ArtifactView(title=f"{path.name}: no artifact.json")

        record: ArtifactRecord = artifact.record
        store = LocalArtifactStore(store_root_of(path) or self.store.root)
        manifest: RunManifest | None = RunManifest.load(path) if (path / BundleFile.MANIFEST).is_file() else None

        return ArtifactView(
            title=f"{artifact.kind}/{artifact.name}",
            key=record.key,
            path=str(path),
            meta=provenance(record),
            files=file_rows(record),
            spec=json.dumps(record.spec, indent=2, default=repr),
            refs=ref_rows(manifest.artifacts, store) if manifest is not None else [],
            uses=runs_using(self.telemetry.runs.scan(), record.key)[:CHIP_LIMIT],
            run_id=manifest.run_id if manifest is not None else "",
        )

    def publish(self, view: ArtifactView) -> None:
        """Writes one view into the panel's state variables."""
        self.state.update(
            {
                self.key(DetailVar.TITLE): view.title,
                self.key(DetailVar.KEY): view.key,
                self.key(DetailVar.PATH): view.path,
                self.key(DetailVar.META): view.meta,
                self.key(DetailVar.FILES): view.files,
                self.key(DetailVar.SPEC): view.spec,
                self.key(DetailVar.REFS): view.refs,
                self.key(DetailVar.USES): view.uses,
                self.key(DetailVar.RUN_ID): view.run_id,
            }
        )

    async def on_selected(self, **_: object) -> None:
        """Shows the artifact the shared selection names."""
        path = str(self.state[Shared.SELECTED_ARTIFACT_PATH] or "")
        if not path:
            return

        await self.show(Path(path))

    async def show(self, path: Path) -> None:
        """Loads an artifact off the server loop and publishes it; a failed read lands in the status line."""
        try:
            view: ArtifactView = await asyncio.to_thread(self.load, path)
        except Exception as error:
            log.opt(exception=True).warning("Artifact could not be read.")
            self.status(f"{type(error).__name__}: {error}")
            return

        with self.state:
            self.publish(view)

    def selected(self) -> StoredArtifact | None:
        """The artifact the panel shows, reporting in the status line when there is none."""
        if self.artifact is None:
            self.status("select an artifact first")

        return self.artifact

    def reveal(self) -> None:
        """Opens the artifact's directory in the file manager."""
        artifact: StoredArtifact | None = self.selected()
        if artifact is None:
            return

        open_folder(artifact.path)

    def open_dialog(self) -> None:
        """Opens the materialize dialog once an artifact is shown."""
        if self.selected() is None:
            return

        self.state[self.key(DetailVar.DIALOG)] = True

    def close_dialog(self) -> None:
        """Closes the materialize dialog."""
        self.state[self.key(DetailVar.DIALOG)] = False

    def on_dialog(self, open_: object) -> None:
        """Follows the dialog the client closed with Escape or the backdrop."""
        self.state[self.key(DetailVar.DIALOG)] = bool(open_)

    def materialize(self) -> None:
        """Copies the artifact's files into the chosen directory, checking their hashes when asked."""
        artifact: StoredArtifact | None = self.selected()
        if artifact is None:
            return

        into = Path(str(self.state[self.key(DetailVar.INTO)] or "")).expanduser()
        verify = bool(self.state[self.key(DetailVar.VERIFY)])
        try:
            copied: list[Path] = self.store.materialize(artifact, into, verify=verify)
        except Exception as error:
            log.opt(exception=True).warning("Materialize failed.")
            self.status(f"{type(error).__name__}: {error}")
            return

        self.state[self.key(DetailVar.DIALOG)] = False
        self.status(f"{len(copied)} files into {into}")

        log.bind(artifact=artifact.name, into=str(into), files=len(copied), verify=verify).info("Artifact materialized.")

    def rehydrate(self) -> None:
        """Fetches the store-sourced inputs of a run-bundle artifact back beside its copy."""
        artifact: StoredArtifact | None = self.selected()
        if artifact is None:
            return

        try:
            handles = rehydrate_bundle(artifact.path, ArtifactCache(self.store))
        except Exception as error:
            log.opt(exception=True).warning("Rehydrate failed.")
            self.status(f"{type(error).__name__}: {error}")
            return

        names = [name for handle in handles for name in handle.files]
        self.status(f"rehydrated {len(names)} files into {artifact.path.name}")

    def open_run(self) -> None:
        """Selects the run of a run-bundle artifact, so the run browser and detail follow."""
        run_id = str(self.state[self.key(DetailVar.RUN_ID)] or "")
        if not run_id:
            self.status("this artifact is not a run bundle")
            return

        select_run(self.state, run_id)

    def open_use(self, run_id: str) -> None:
        """Selects a run that used this artifact."""
        select_run(self.state, run_id)
