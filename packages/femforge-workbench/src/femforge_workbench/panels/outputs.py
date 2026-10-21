"""The outputs panel: one toggle per declared output of a catalogue card, each shown by its kind, the whole set swapped by show()."""

import json
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, cast

import polars as pl
from trame.widgets import client, html, react

from femforge.common.log import log
from femforge_workbench.panels import Cls, PanelComponent
from femforge_workbench.panels.table import TablePanel
from femforge_workbench.registry import OutputKind, OutputSpec, WorkbenchSpec
from femforge_workbench.widgets.code import Code
from femforge_workbench.widgets.tip import Tip
from femforge_workbench.widgets.toggle import ToggleGroup, ToggleGroupItem


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


TABLE_ROWS = 1000
"""Rows of a table shown in the grid."""

TEXT_BYTES = 256 * 1024
"""The tail of a text file shown in the editor."""

ROOT_PREFIX = "wb-"
MISSING_MESSAGE = "not produced yet"
UNWRITTEN_MARK = "○"
"""The mark a tab carries while its file does not exist yet."""

TABLE_SUFFIX = "table"
TAB = "tab"
SERVED_KINDS = (OutputKind.HTML, OutputKind.IMAGE)
EDITOR_OPTIONS: dict[str, object] = {
    "readOnly": True,
    "domReadOnly": True,
    "lineNumbers": "on",
    "renderLineHighlight": "none",
    "scrollBeyondLastLine": False,
    "wordWrap": "off",
}
"""The Monaco options every read-only viewer in the workbench shares."""


class StateVar(StrEnum):
    """The per-panel state variables the toggles and the viewers bind."""

    TABS = "tabs"
    SELECTED = "selected"
    VIEWER = "viewer"
    CAPTION = "caption"
    URL = "url"
    TEXT = "text"
    LANGUAGE = "language"
    OPTIONS = "options"


class Viewer(StrEnum):
    """Which viewer the shown output goes into; one variable holds it, so each condition is a single equality."""

    MISSING = "missing"
    TABLE = "table"
    FRAME = "frame"
    IMAGE = "image"
    CODE = "code"

    @classmethod
    def of(cls, kind: OutputKind) -> "Viewer":
        """The viewer an output kind is shown in."""
        match kind:
            case OutputKind.TABLE:
                return cls.TABLE
            case OutputKind.HTML:
                return cls.FRAME
            case OutputKind.IMAGE:
                return cls.IMAGE
            case OutputKind.MARKDOWN | OutputKind.TEXT:
                return cls.CODE


class TabField(StrEnum):
    """The keys of a tab entry."""

    INDEX = "index"
    TITLE = "title"
    KIND = "kind"
    EXISTS = "exists"


def language(path: Path) -> str:
    """The Monaco language of a text output."""
    match path.suffix.lower():
        case ".md":
            return "markdown"
        case ".json":
            return "json"
        case ".py":
            return "python"
        case ".yaml" | ".yml":
            return "yaml"
        case ".toml" | ".ini" | ".cfg":
            return "ini"
        case _:
            return "plaintext"


def serve_roots(specs: Sequence[WorkbenchSpec]) -> dict[str, Path]:
    """One URL root per directory holding a spec's html or image outputs: wb-<name> for the first directory, wb-<name>-2 and on for the rest."""
    roots: dict[str, Path] = {}
    for spec in specs:
        served = [output.path.parent for output in spec.outputs if output.kind in SERVED_KINDS]
        for index, directory in enumerate(dict.fromkeys(served), start=1):
            root = f"{ROOT_PREFIX}{spec.name}" if index == 1 else f"{ROOT_PREFIX}{spec.name}-{index}"
            roots[root] = directory

    return roots


def read_table(path: Path, rows: int = TABLE_ROWS) -> tuple[pl.DataFrame, int]:
    """The first `rows` of a csv or parquet file and its full row count."""
    lazy = pl.scan_parquet(path) if path.suffix.lower() == ".parquet" else pl.scan_csv(path, infer_schema_length=rows)
    total = cast("int", lazy.select(pl.len()).collect().item())

    return lazy.head(rows).collect(), total


def read_tail(path: Path, limit: int = TEXT_BYTES) -> str:
    """The last `limit` bytes of a text file, decoded leniently."""
    size = path.stat().st_size
    with path.open("rb") as file:
        if size > limit:
            _ = file.seek(size - limit)

        data = file.read()

    return data.decode("utf-8", errors="replace")


class WorkbenchOutputsPanel(PanelComponent):
    """Shows a card's outputs one at a time: tables in a hosted table panel, html in a frame, images, markdown and text; a missing file says so."""

    def __init__(self, app: "Workbench", id: str, title: str, *, catalogue: Sequence[WorkbenchSpec]) -> None:
        """Builds the panel and serves every directory holding a card's html or image outputs.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            catalogue: The cards whose outputs the panel can show.
        """
        self.catalogue = list(catalogue)
        self.spec: WorkbenchSpec | None = None
        self.roots = serve_roots(self.catalogue)
        # Its own template, hosted inside this panel's; built outside the layout so its root is not also a child here.
        self.table = TablePanel(app, f"{id}-{TABLE_SUFFIX}", "Table", pl.DataFrame(), filterable=True)

        super().__init__(app, id, title)

        # Static routes are read once at server start, so every served directory of the catalogue is declared now.
        if self.roots:
            _ = app.server.enable_module({"serve": {root: str(directory) for root, directory in self.roots.items()}})

    def build(self) -> None:
        """Builds the output toggles over the viewers, one shown at a time."""

        # A client-side condition must read every key it depends on: behind a `&&` the scope proxy never records the key, so the branch is never re-evaluated.
        def shows(viewer: Viewer) -> str:
            return f"{self.key(StateVar.VIEWER)} === {json.dumps(viewer.value)}"

        with html.Div(classes=str(Cls.TOOLBAR)):
            with (
                ToggleGroup(
                    model=self.key(StateVar.SELECTED),
                    type_="single",
                    size="sm",
                    variant="outline",
                ),
                react.For(items=self.key(StateVar.TABS), name=TAB),
            ):
                with ToggleGroupItem(value=react.Bind(f"{TAB}.{TabField.INDEX}")):
                    html.Span([react.Bind(f"{TAB}.{TabField.TITLE}")])
                    with react.If(f"!{TAB}.{TabField.EXISTS}"):
                        with Tip(content=MISSING_MESSAGE):
                            html.Span(UNWRITTEN_MARK, classes=str(Cls.CAPTION))

            html.Span(classes=str(Cls.SPACER))
            html.Span([react.Bind(self.key(StateVar.CAPTION))], classes=str(Cls.STATUS))

        with html.Div(classes=str(Cls.FILL)):
            with react.If(shows(Viewer.MISSING)):
                html.Div(MISSING_MESSAGE, classes=str(Cls.EMPTY))

            with react.If(shows(Viewer.TABLE)), html.Div(classes=str(Cls.FILL)):
                client.ServerTemplate(name=self.table.template_name)

            with react.If(shows(Viewer.FRAME)):
                html.Iframe(src=react.Bind(self.key(StateVar.URL)), classes=str(Cls.FRAME))

            with react.If(shows(Viewer.IMAGE)):
                html.Img(src=react.Bind(self.key(StateVar.URL)), classes=str(Cls.IMAGE))

            with react.If(shows(Viewer.CODE)):
                Code(
                    value=react.Bind(self.key(StateVar.TEXT)),
                    language=react.Bind(self.key(StateVar.LANGUAGE)),
                    options=react.Bind(self.key(StateVar.OPTIONS)),
                    classes=str(Cls.FILL),
                )

        self.state.update(
            {
                self.key(StateVar.TABS): [],
                self.key(StateVar.SELECTED): "0",
                self.key(StateVar.VIEWER): Viewer.MISSING.value,
                self.key(StateVar.CAPTION): "",
                self.key(StateVar.URL): "",
                self.key(StateVar.TEXT): "",
                self.key(StateVar.LANGUAGE): language(Path()),
                self.key(StateVar.OPTIONS): EDITOR_OPTIONS,
            }
        )
        _ = self.listen(self.key(StateVar.SELECTED))(self.on_selected)

    def tabs(self) -> list[dict[str, object]]:
        """The toggle entries of the shown card's outputs, each marked by whether its file exists."""
        outputs = self.spec.outputs if self.spec is not None else ()

        return [{TabField.INDEX: str(index), TabField.TITLE: output.title, TabField.KIND: str(output.kind), TabField.EXISTS: output.path.exists()} for index, output in enumerate(outputs)]

    def selected(self) -> int:
        """The index the toggle names, zero when it names none."""
        chosen = str(self.state[self.key(StateVar.SELECTED)] or "0")

        return int(chosen) if chosen.isdigit() else 0

    def show(self, spec: WorkbenchSpec) -> None:
        """Swaps the toggles to the spec's outputs and shows the first."""
        self.spec = spec
        self.reload(0)

    def reload(self, index: int | None = None) -> None:
        """Re-reads the tabs and the shown output, `index` when given else the selected one."""
        selected = self.selected() if index is None else index

        with self.state:
            self.state[self.key(StateVar.TABS)] = self.tabs()
            self.state[self.key(StateVar.SELECTED)] = str(selected)

        self.select(selected)

    def on_selected(self, **_: object) -> None:
        """Shows the output the toggle selected."""
        self.select(self.selected())

    def url(self, output: OutputSpec) -> str:
        """The served URL of an html or image output."""
        root = next((root for root, directory in self.roots.items() if directory == output.path.parent), None)
        if root is None:
            raise KeyError(f"{output.path.parent} is not served; the spec is not in the catalogue")

        return f"/{root}/{output.path.name}"

    def select(self, index: int) -> None:
        """Loads one output into the viewer of its kind."""
        if self.spec is None or not 0 <= index < len(self.spec.outputs):
            return

        output = self.spec.outputs[index]
        missing = not output.path.exists()
        with self.state:
            self.state[self.key(StateVar.VIEWER)] = Viewer.MISSING.value if missing else Viewer.of(output.kind).value
            self.state[self.key(StateVar.CAPTION)] = str(output.path)
            if missing:
                return

            match output.kind:
                case OutputKind.TABLE:
                    frame, total = read_table(output.path)
                    self.table.set_rows(frame)
                    self.state[self.key(StateVar.CAPTION)] = f"{output.path}: {frame.height} of {total} rows"
                case OutputKind.HTML | OutputKind.IMAGE:
                    self.state[self.key(StateVar.URL)] = self.url(output)
                case OutputKind.MARKDOWN | OutputKind.TEXT:
                    self.state[self.key(StateVar.TEXT)] = read_tail(output.path)
                    self.state[self.key(StateVar.LANGUAGE)] = language(output.path)

        log.bind(panel=self.id, output=str(output.path), kind=str(output.kind)).info("Output shown.")

    def dispose(self) -> None:
        """Releases the hosted table, then the panel."""
        self.table.dispose()
        super().dispose()
