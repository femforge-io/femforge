"""The field viewer: a controller that opens a run's VTU as a FieldScene, the controls that steer it, and the view that shows it."""

import asyncio
import json
import math
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache, partial
from pathlib import Path
from typing import cast

import polars as pl
import pyvista as pv
from trame.app import TrameComponent
from trame.decorators import change, controller
from trame.widgets import html, react

from femforge.artifacts.lock import LOCK_SUFFIX, DirLock
from femforge.common.files import RunFile
from femforge.common.log import TELEMETRY_ENV, log
from femforge.common.utils import title
from femforge.run.entities import Bodies
from femforge.run.fields import GEOMETRY_IDS_ARRAY
from femforge.run.inspect import Run
from femforge.run.result import Result
from femforge.telemetry.paths import Telemetry, resolve_root
from femforge.telemetry.runs import RunQuery
from femforge.telemetry.schemas import LiveRunColumn, RunColumn
from femforge.viz.field_display import BodyPaint, BodyStyle, DisplayMode, DisplaySpec, FieldInfo, FieldScene, Legend, line_vectors, sample_colormap
from femforge.viz.field_lines import FieldLineOptions, SeedPlacement
from femforge.viz.symmetry import Expansion
from femforge_workbench import dock
from femforge_workbench.app import Hook, Workbench
from femforge_workbench.colormaps import COLORMAPS, FAVORITES, ColormapType
from femforge_workbench.feeds import Feed
from femforge_workbench.panels import ITEM, Cls, ItemField, PanelComponent, Shared, labelled, picker
from femforge_workbench.panels.scene import ScenePanel, View, toolbar_id, view_id
from femforge_workbench.registry import FIELD_VIEWER
from femforge_workbench.widgets.accordion import Accordion, AccordionContent, AccordionItem, AccordionTrigger
from femforge_workbench.widgets.button import Button
from femforge_workbench.widgets.checkbox import Checkbox
from femforge_workbench.widgets.combobox import Combobox, ComboboxItem
from femforge_workbench.widgets.element import EVENT, setter
from femforge_workbench.widgets.field import Field, FieldLabel
from femforge_workbench.widgets.icon import Icon
from femforge_workbench.widgets.input import ColorInput, Input
from femforge_workbench.widgets.progress import Progress
from femforge_workbench.widgets.slider import Slider
from femforge_workbench.widgets.switch import Switch
from femforge_workbench.widgets.toggle import ToggleGroup, ToggleGroupItem
from femforge_workbench.widgets.tour import TourStep


RUN_PICKER_LIMIT = 200
"""The newest indexed runs the run picker offers."""

RUNS_PERIOD = 1.0
"""How often the run picker checks the run index for runs that landed or finished solving."""

PICK_HOLD = 30.0
"""`t` `[s]`: The longest the open dropdown holds new runs back, so a page that closed without saying so never freezes the list."""

RUNNING = "running"
FAILED = "error"
NO_RUN_SELECTED = title("no run selected")
NO_RUNS_YET = title("no runs yet")
NO_RUN_INDEX = title("no run index")
INDEX_UNREADABLE = title("run index unreadable")
SOLVING_AS = title("solving")
NO_INDEX_NOTICE = f"No run index. Pass telemetry to field_viewer or set {TELEMETRY_ENV} to list runs here."
NO_RUNS_NOTICE = "No runs yet. The first run opens here as soon as it starts solving; the solver's progress and any errors show in its terminal."
RUN_MENU = "max-w-[36rem]"

CLIM_STEPS = 1000
"""Range slider resolution over the data range."""

BOUNDS_RATIO = 2.0
"""The largest diagonal ratio between two runs whose camera still carries over from one to the other."""

FIELDS_PERIOD = 1.0
"""How often the viewer checks whether a run it shows empty has finished solving."""

LINE_COUNT_MIN = 2
MAX_LENGTH_RANGE = (0.05, 1.0)
MAX_LENGTH_STEP = 0.05
WHOLE_DOMAIN = ""
WHOLE_DOMAIN_LABEL = "Whole Domain"
NO_RUN = "No Run Open"
VOLUME_2D = "Volume (3D Only)"
MATERIAL_PREFIX = "material:"
DEFAULTS = DisplaySpec()
"""The display the viewer starts from unless given one."""

LINE_DEFAULTS = FieldLineOptions()
FULL_WIDTH = "w-full"
RUN_WIDTH = "w-96"
PATH_WIDTH = "w-[28rem] shrink-0"
BUSY_FULL = 100.0
"""The bar is pulsed, not filled: the load reports no fraction."""

SYMMETRY_SECTION = "symmetry"
FIELD_SECTION = "field"
LINES_SECTION = "lines"
BODIES_SECTION = "bodies"
OPEN_SECTIONS = [SYMMETRY_SECTION, FIELD_SECTION, LINES_SECTION, BODIES_SECTION]
OPACITY_RANGE = (0.0, 1.0)
OPACITY_STEP = 0.01
ALL_BODIES = "All Bodies"
PREVIEW_STOPS = 16
"""Colors sampled for a colormap's swatch, enough for the CSS gradient to read as the map."""

FAVORITES_GROUP = "Favorites"
COLORMAP_FILTERS = [FAVORITES_GROUP, *(kind.value for kind in ColormapType)]
SAME_AS_FIELD = "same-as-field"
SAME_AS_FIELD_LABEL = "Same as Field"
STACK = "flex flex-col gap-1.5"
SETTING = "flex h-6 w-full shrink-0 items-center justify-between gap-2"
READOUTS = "flex items-center justify-between"
SWITCH_ID = "ff-switch-"
BODY_TABLE = "flex w-full shrink-0 flex-col gap-1"
BODY_ROW = "grid h-6 grid-cols-[1rem_minmax(0,1fr)_5.5rem_4.5rem] items-center gap-x-2"
BODY_NAME = "truncate"
MEMBER_NAME = "truncate pl-3"
GROUP_NAME = "truncate font-medium"
CHIP_SLOT = "grid grid-cols-[minmax(0,1fr)_1rem] items-center gap-0.5"
CHIP = "h-4 w-full min-w-0 cursor-pointer rounded-sm border border-border p-0"
COLOR_CHIP = f"{CHIP} [&::-webkit-color-swatch-wrapper]:p-0 [&::-webkit-color-swatch]:rounded-[3px] [&::-webkit-color-swatch]:border-0"
CHIP_CLEAR = "size-4"
PRECISION = 3
"""Significant figures of the range readouts, matching the color bar's labels."""
RANGE_VAR = "--ff-range"
RANGE_TRACK = "[&_[data-slot=slider-range]]:[background-image:var(--ff-range)]"
"""Paints the track between the range thumbs with the field colormap, which the slider then reads as the legend."""

RUN_GROUP = "flex items-center gap-3"
TOUR_PREFIX = "tour-"
RUN_TOUR_ID = f"{TOUR_PREFIX}run"
SWEEP_RANGE = (15.0, 360.0)
SWEEP_STEP = 15.0
DEGREES = "°"
NOTHING_TO_EXPAND = title("nothing to expand")


class SymmetryState(StrEnum):
    """What the symmetry section has to offer for the open run."""

    NONE = "none"
    """No run is open."""

    FIXED = "fixed"
    """The run was solved in 3D, so there is nothing to expand."""

    OPEN = "open"
    """The run was solved on a section that can be expanded."""


type Item = dict[str, object]
type Location = Run | str | Path
"""Where a run is: a Run, a run id or a bundle directory."""


class FieldPanel(StrEnum):
    """The viewer's panel ids."""

    CONTROLS = "field-controls"
    VIEW_3D = "field3d"


class FieldVar(StrEnum):
    """The viewer's state variables, bound by the controls, the app-bar run picker and the two views alike."""

    RUN = "field_run"
    """The location asked for: an indexed run id or a bundle directory."""

    RUN_TEXT = "field_run_text"
    RUN_ITEMS = "field_run_items"
    RUN_PLACEHOLDER = "field_run_placeholder"
    RUN_PICKING = "field_run_picking"
    """Whether the run picker's dropdown is open, which holds new runs back until it closes."""

    RUN_ID = "field_run_id"
    BUSY = "field_busy"
    MODEL_DIM = "field_model_dim"
    FIELDS = "field_fields"
    FIELD = "field_field"
    MODES = "field_modes"
    MODE = "field_mode"
    COLORMAP = "field_colormap"
    LOG_SCALE = "field_log_scale"
    INVERT = "field_invert"
    OUTLINE = "field_outline"
    LEGEND = "field_legend"
    EXPANSION = "field_expansion"
    SWEEP = "field_sweep"
    SOLVED_AS = "field_solved_as"
    SYMMETRY = "field_symmetry"
    AUTO_CLIM = "field_auto_clim"
    CLIM = "field_clim"
    CLIM_MIN = "field_clim_min"
    CLIM_MAX = "field_clim_max"
    CLIM_STEP = "field_clim_step"
    VISIBLE_BODIES = "field_visible_bodies"
    HAS_VECTORS = "field_has_vectors"
    STREAMLINES = "field_streamlines"
    SEED_PLACEMENTS = "field_seed_placements"
    SEED_PLACEMENT = "field_seed_placement"
    LINE_COUNT = "field_line_count"
    LINE_COUNT_MAX = "field_line_count_max"
    MAX_LENGTH = "field_max_length"
    LINES_BODIES = "field_lines_bodies"
    LINES_BODY = "field_lines_body"
    OPACITY = "field_opacity"
    OPACITY_MAPPING = "field_opacity_mapping"
    OPACITY_RANGE = "field_opacity_range"
    BODY_ROWS = "field_body_rows"
    FIELD_GRADIENT = "field_gradient"
    BODY_COLORMAP = "field_body_colormap"
    BODY_COLORMAP_SHOWN = "field_body_colormap_shown"
    """The body colormap picker's value: SAME_AS_FIELD while no body takes the body colormap."""

    BODY_LEAD = "field_body_lead"
    COLORMAP_ITEMS = "field_colormap_items"


TOUR_STEPS: list[TourStep] = [
    {
        "target": f"#{view_id(FieldPanel.VIEW_3D)}",
        "title": "Your Solved Field",
        "body": "This view colors the solved field by the quantity you pick in Field. Drag to orbit and scroll to zoom.",
        "side": "left",
    },
    {
        "target": f"#{RUN_TOUR_ID}",
        "title": "Switch Between Runs",
        "body": "Every run of this project is listed here, newest first, including ones still solving. Pick one to open it.",
        "side": "bottom",
    },
    {
        "target": f"#{TOUR_PREFIX}{SYMMETRY_SECTION}",
        "title": "Expand the Section",
        "body": "Show a 2D section as solved, or mirror or revolve it into 3D. Sweep sets how far the revolve goes.",
        "side": "right",
    },
    {
        "target": f"#{TOUR_PREFIX}{FIELD_SECTION}",
        "title": "Choose What to Color",
        "body": "Pick the quantity and its colormap. The range slider shows the value each color stands for.",
        "side": "right",
    },
    {
        "target": f"#{TOUR_PREFIX}{LINES_SECTION}",
        "title": "Trace the Field",
        "body": "Field lines follow the shown vector field, or the flux density or displacement while a scalar is shown, from seeds on the center plane. Raise the seed count for denser lines.",
        "side": "right",
    },
    {
        "target": f"#{TOUR_PREFIX}{BODIES_SECTION}",
        "title": "Show or Hide Bodies",
        "body": "Check a body to show it, and use its chip and slider to set its color and opacity.",
        "side": "right",
    },
    {
        "target": f"#{toolbar_id(FieldPanel.VIEW_3D)}",
        "title": "Frame and Save the View",
        "body": "Fit, Top and Iso reset the camera, and Screenshot saves a PNG. Set Legend in the Field section first to include the color scale.",
        "side": "bottom",
    },
]
"""The tour's stops through the viewer, in order; the shell adds its own last stop."""

DISPLAY_KEYS: tuple[FieldVar, ...] = (
    FieldVar.FIELD,
    FieldVar.MODE,
    FieldVar.COLORMAP,
    FieldVar.LOG_SCALE,
    FieldVar.INVERT,
    FieldVar.OUTLINE,
    FieldVar.LEGEND,
    FieldVar.EXPANSION,
    FieldVar.AUTO_CLIM,
    FieldVar.VISIBLE_BODIES,
    FieldVar.STREAMLINES,
    FieldVar.SEED_PLACEMENT,
    FieldVar.LINES_BODY,
    FieldVar.OPACITY_MAPPING,
    FieldVar.BODY_COLORMAP,
)
"""The state variables whose change redraws the scene."""


def names_of(value: object) -> list[str]:
    """The strings of a state list, nothing for anything else."""
    if not isinstance(value, list):
        return []

    return [str(item) for item in cast("list[object]", value)]


def number_of(value: object, fallback: float) -> float:
    """A state number, or the fallback for anything that is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback

    return float(value)


def pair_of(value: object) -> tuple[float, float] | None:
    """A state ``[lo, hi]`` as a tuple, None for anything else."""
    if not isinstance(value, list) or len(cast("list[object]", value)) != 2:
        return None

    lo, hi = cast("list[object]", value)
    if isinstance(lo, bool) or isinstance(hi, bool) or not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return None

    return (float(lo), float(hi))


class RowKey(StrEnum):
    """The keys of a body-table row."""

    ID = "id"
    TITLE = "title"
    MEMBERS = "members"
    GROUP = "group"
    MEMBER = "member"
    PAINT = "paint"
    COLOR = "color"
    OPACITY = "opacity"


def hex_of(color: str) -> str:
    """A color pyvista resolves as the ``#rrggbb`` a color input takes."""
    return pv.Color(color).hex_rgb


@cache
def colormap_gradient(name: str, *, reverse: bool = False) -> str | None:
    """A left-to-right CSS gradient through the colormap (right-to-left when reversed), None when no installed package resolves it."""
    try:
        samples = cast("list[list[float]]", sample_colormap(name, PREVIEW_STOPS).tolist())
    except Exception:  # noqa: BLE001 - a colormap that cannot resolve loses its swatch, never the select
        log.bind(colormap=name).warning("Colormap has no preview.")
        return None

    last = len(samples) - 1
    stops = ", ".join(f"rgb({round(r * 255)}, {round(g * 255)}, {round(b * 255)}) {100 * index / last:.0f}%" for index, (r, g, b, *_) in enumerate(samples))

    return f"linear-gradient(to {'left' if reverse else 'right'}, {stops})"


@cache
def colormap_items() -> list[ComboboxItem]:
    """The colormap pickers' items: the favorites first, then every other catalogue entry, each with its label, groups and swatch."""
    catalogue = {entry.name: entry for entry in COLORMAPS}
    names = [*FAVORITES, *(name for name in catalogue if name not in FAVORITES)]
    items: list[ComboboxItem] = []
    for name in names:
        entry = catalogue[name]
        groups = [FAVORITES_GROUP, entry.kind.value] if name in FAVORITES else [entry.kind.value]
        item: ComboboxItem = {"value": name, "label": title(entry.label), "groups": groups}
        gradient = colormap_gradient(name)
        if gradient is not None:
            item["swatch"] = gradient

        items.append(item)

    return items


def mode_items(info: FieldInfo) -> list[Item]:
    """The mode select's items for one field."""
    return [{ItemField.TITLE: mode.label, ItemField.VALUE: str(mode)} for mode in info.modes]


def expansion_label(value: str) -> str:
    """How an expansion reads as a segment: the result, beside what the run was solved as."""
    match Expansion(value):
        case Expansion.SECTION:
            return "Section"
        case Expansion.MIRROR:
            return "Mirrored"
        case Expansion.REVOLVE:
            return "Revolved"


def placement_items(model_dim: int = 3) -> list[Item]:
    """The seed placement select's items; a run shown flat offers no volume to seed."""
    items: list[Item] = [{ItemField.TITLE: title(placement.value), ItemField.VALUE: str(placement)} for placement in SeedPlacement]
    if model_dim == 2:
        volume = next(item for item in items if item[ItemField.VALUE] == SeedPlacement.VOLUME)
        volume.update({ItemField.TITLE: VOLUME_2D, ItemField.DISABLED: True})

    return items


def body_items(names: Sequence[str]) -> list[Item]:
    """The field-line region select's items: the whole domain first, then every body."""
    return [{ItemField.TITLE: WHOLE_DOMAIN_LABEL, ItemField.VALUE: WHOLE_DOMAIN}, *({ItemField.TITLE: name, ItemField.VALUE: name} for name in names)]


def body_rows(bodies: Bodies, names: Sequence[str], looks: Mapping[str, BodyStyle], body_colormap: str) -> list[Item]:
    """The body table's rows: a material that owns several bodies as a group row over them, then the rest, each showing where its color comes from, that color and its opacity.

    Args:
        bodies: The run's bodies, whose materials group the rows.
        names: The body names to list, in order.
        looks: Each body's own style by name; a body without one takes the field colormap.
        body_colormap: The colormap spread evenly over the bodies whose paint is `BodyPaint.COLORMAP`.
    """
    grouped: dict[str, list[str]] = {}
    for name in names:
        body = bodies.get(name)
        if body is None or body.material is None:
            continue

        grouped.setdefault(body.material, []).append(name)

    styles = [looks.get(name) or BodyStyle(body=name) for name in names]
    resolved = DisplaySpec(body_styles=tuple(styles), body_colormap=body_colormap).solid()

    def row(id: str, title: str, members: Sequence[str], *, group: bool = False, member: bool = False) -> Item:
        own = [looks.get(name) or BodyStyle(body=name) for name in members]
        paints = {style.paint for style in own}
        colors = [resolved.get(name) for name in members]
        return {
            RowKey.ID: id,
            RowKey.TITLE: title,
            RowKey.MEMBERS: list(members),
            RowKey.GROUP: group,
            RowKey.MEMBER: member,
            RowKey.PAINT: str(paints.pop()) if len(paints) == 1 else str(BodyPaint.FIELD),
            RowKey.COLOR: colors[0],
            RowKey.OPACITY: [own[0].opacity],
        }

    rows: list[Item] = []
    placed: set[str] = set()
    for material, members in grouped.items():
        if len(members) < 2:
            continue

        rows.append(row(f"{MATERIAL_PREFIX}{material}", material, members, group=True))
        rows.extend(row(name, name, [name], member=True) for name in members)
        placed.update(members)

    rows.extend(row(name, name, [name]) for name in names if name not in placed)

    return rows


def has_vector_field(fields: Sequence[FieldInfo]) -> bool:
    """Whether the fields include a vector field for the field lines to follow."""
    return line_vectors(fields, "") is not None


class RunFlag(StrEnum):
    """What the run picker says about a run ahead of its id."""

    SOLVING = "solving"
    """The run is being solved under a live claim; opening it waits for its fields."""

    FAILED = "failed"
    """The run's solve failed before it wrote fields; opening it says so."""

    NO_FIELDS = "no fields"
    """The open run has no fields: its solve failed or its fields were purged."""


@dataclass(frozen=True)
class RunEntry:
    """One run the picker offers: what picking it opens, how it reads, and what the picker says about it."""

    value: str
    """The location picking the run opens: its run id when indexed, else its bundle directory."""

    run_id: str
    description: str | None = None
    physics: str | None = None
    dim: int | None = None
    flag: RunFlag | None = None
    runs_dir: Path | None = field(default=None, compare=False)
    """The directory holding the run's bundle and its claim, None when unknown."""

    @property
    def by_path(self) -> bool:
        """Whether the run is offered by its bundle directory rather than from the index."""
        return self.value != self.run_id

    @property
    def name(self) -> str:
        """How the picker names the run: its description, else its id."""
        return self.description or self.run_id

    @property
    def ended(self) -> bool:
        """Whether a run listed as solving lost its claim, so the index must be read again to learn how it ended."""
        if self.flag is not RunFlag.SOLVING or self.runs_dir is None:
            return False

        return not claimed(self.runs_dir, self.run_id)

    def item(self) -> Item:
        """The run as a picker item: its description, else its id, then its flag and, muted, its id, else its physics and dimension."""
        extent = f"{self.dim}D" if self.dim is not None else ""
        identity = [self.run_id] if self.description else [part for part in (title(self.physics or ""), extent) if part]
        item: Item = {ItemField.TITLE: self.name, ItemField.VALUE: self.value, ItemField.DETAIL: identity}
        if self.flag is not None:
            item[ItemField.FLAG] = title(self.flag)

        return item


def claimed(runs_dir: Path, run_id: str) -> bool:
    """Whether a live claim holds the run: its lock directory exists and its holder still beats."""
    lock = DirLock(runs_dir / f"{run_id}{LOCK_SUFFIX}")

    return lock.path.is_dir() and not lock.is_stale()


def bundle_entry(run: Run, value: str, flag: RunFlag | None) -> RunEntry:
    """An opened bundle as a picker entry, read from its manifest, its id alone when it has none to read.

    Args:
        run: The opened bundle.
        value: The location it was opened from.
        flag: What the picker says about it.
    """
    try:
        manifest = run.manifest
    except (FileNotFoundError, ValueError):
        return RunEntry(value, run.path.name, flag=flag, runs_dir=run.path.parent)

    return RunEntry(value, run.path.name, manifest.config.meta.description, str(manifest.physics), manifest.config.frame.model_dim, flag, run.path.parent)


def run_entries(telemetry: Telemetry | None) -> list[RunEntry]:
    """The newest indexed runs that have fields, are solving under a live claim or failed, empty without a telemetry root; a solve that died keeps saying running, so its stale claim drops it."""
    if telemetry is None:
        return []

    status = pl.col(RunColumn.STATUS)
    offered = pl.col(RunColumn.HAS_VTU) | status.is_in([RUNNING, FAILED])
    frame = RunQuery(telemetry.runs.scan()).where(offered).newest(RUN_PICKER_LIMIT).with_live().frame()
    columns = (
        RunColumn.RUN_ID,
        RunColumn.DESCRIPTION,
        RunColumn.PHYSICS,
        RunColumn.MODEL_DIM,
        RunColumn.HAS_VTU,
        RunColumn.STATUS,
        LiveRunColumn.STALE,
        RunColumn.RUNS_DIR,
    )
    rows = cast("list[tuple[str, str | None, str | None, int | None, bool | None, str | None, bool | None, str | None]]", frame.select(columns).rows())

    entries: list[RunEntry] = []
    for run_id, description, physics, dim, has_vtu, state, stale, runs_dir in rows:
        live = state == RUNNING and stale is False
        failed = state == FAILED and not has_vtu
        if not has_vtu and not live and not failed:
            continue

        flag = RunFlag.SOLVING if live else RunFlag.FAILED if failed else None
        directory = Path(runs_dir) if runs_dir is not None else None
        entries.append(RunEntry(run_id, run_id, description, physics, dim, flag, directory))

    return entries


def merged(indexed: Sequence[RunEntry], shown: Sequence[RunEntry], opened: RunEntry | None, chosen: str) -> list[RunEntry]:
    """The picker's runs: the bundles opened by path that the index lacks, then the indexed runs, with the open run kept where neither holds it.

    Args:
        indexed: The runs the index offers, newest first.
        shown: The runs the picker offers now.
        opened: The run open or waited on, None before the first.
        chosen: The picker's value, kept offered while it opens.
    """
    ids = {entry.run_id for entry in indexed}
    own = opened.run_id if opened is not None else None
    kept = [entry for entry in shown if entry.by_path and entry.run_id not in ids and entry.run_id != own]
    entries = [*kept, *indexed]
    if opened is not None and opened.run_id not in ids:
        entries = [opened, *entries] if opened.by_path else [*entries, opened]

    values = {entry.value for entry in entries}
    if chosen in values:
        return entries

    runs = {entry.run_id for entry in entries}
    held = [entry for entry in shown if entry.value == chosen and entry.run_id not in runs]

    return [*entries, *held]


def similar_bounds(a: Sequence[float], b: Sequence[float]) -> bool:
    """Whether two bounding boxes frame alike: diagonals within BOUNDS_RATIO of each other and centres closer than half the larger diagonal."""

    def measure(box: Sequence[float]) -> tuple[list[float], float]:
        lower, upper = box[0::2], box[1::2]
        center = [(lo + hi) / 2.0 for lo, hi in zip(lower, upper, strict=True)]
        diagonal = math.sqrt(sum((hi - lo) ** 2 for lo, hi in zip(lower, upper, strict=True)))

        return center, diagonal

    center_a, diagonal_a = measure(a)
    center_b, diagonal_b = measure(b)
    if diagonal_a == 0.0 or diagonal_b == 0.0:
        return False

    largest = max(diagonal_a, diagonal_b)
    distance = math.sqrt(sum((p - q) ** 2 for p, q in zip(center_a, center_b, strict=True)))

    return largest / min(diagonal_a, diagonal_b) <= BOUNDS_RATIO and distance <= largest / 2.0


def solving(run: Run) -> bool:
    """Whether the run is still being solved: a live claim holds it and its manifest says running, or its bundle has none yet."""
    if not claimed(run.path.parent, run.path.name):
        return False

    try:
        return run.manifest.status == "running"
    except FileNotFoundError:
        return run.path.is_dir()


def missing_fields(run: Run) -> str | None:
    """Why the run has no fields to show, None when it has them."""
    if run.vtu_path is not None:
        return None

    record = run.purged(RunFile.FIELDS)
    if record is not None:
        return f"Fields purged on {record.applied_at:%Y-%m-%d} by {record.trigger}; re-solve to restore them"

    return "The solve wrote no fields"


def solving_notice(entry: RunEntry) -> str:
    """What the empty view says while the open run is still solving."""
    return f"{entry.name} is still solving. Its fields appear here when it finishes; the solver's terminal shows its progress."


def captioned(label: str) -> Field:
    """A caption over the control built inside the returned field: the controls column is too narrow for one beside it."""
    field = Field(orientation="vertical", classes=str(Cls.CONTROL))
    with field:
        FieldLabel(title(label), classes=str(Cls.CAPTION))

    return field


def setting(model: FieldVar, label: str, *, disabled: react.Bind | None = None) -> None:
    """A switch over `model` at the right of its label, every switch of the controls in one column.

    Args:
        model: The state variable the switch is bound to.
        label: The caption at the left.
        disabled: A client expression that disables the switch; None keeps it enabled.
    """
    with Field(orientation="horizontal", classes=SETTING):
        FieldLabel(title(label), html_for=f"{SWITCH_ID}{model}", classes=str(Cls.CAPTION))
        Switch(model=str(model), id=f"{SWITCH_ID}{model}", disabled=disabled)


def segmented(model: FieldVar, label: str, values: Sequence[str], *, read: Callable[[str], str] = title) -> None:
    """A captioned single choice among `values` as one row of equal segments; pressing the chosen segment again changes nothing.

    Args:
        model: The state variable the choice is bound to.
        label: The caption over the segments.
        values: The values offered, in order.
        read: Makes the text each value reads as; Title Case by default.
    """
    keep = react.Callback(f"{EVENT} && trame.state.set({json.dumps(str(model))}, {EVENT})")
    with captioned(label):
        with ToggleGroup(type_="single", variant="outline", size="sm", value=react.Bind(str(model)), on_value_change=keep, classes=str(Cls.SEGMENTED), aria_label=title(label)):
            for value in values:
                ToggleGroupItem(read(value), value=value, classes=str(Cls.SEGMENT))


def readouts(left: str | react.Bind, right: str | react.Bind) -> None:
    """The row over a slider: a caption or its low end at the left, its high end at the right."""
    with html.Div(classes=READOUTS):
        html.Span([title(left) if isinstance(left, str) else left], classes=str(Cls.CAPTION))
        html.Span([right], classes=str(Cls.CAPTION))


def seed_count() -> str:
    """The expression reading out the seeds the slider's count per axis makes for the chosen placement."""
    count = f"{FieldVar.LINE_COUNT}[0]"
    axes = f"({FieldVar.SEED_PLACEMENT} === {json.dumps(str(SeedPlacement.VOLUME))} ? 3 : {FieldVar.SEED_PLACEMENT} === {json.dumps(str(SeedPlacement.CENTER_PLANE))} ? 2 : 1)"

    return f"Math.pow({count}, {axes}).toLocaleString()"


def seed_limit(placement: SeedPlacement) -> int:
    """The most seeds per axis a placement offers, so the seeds it makes stay within an integration of a few seconds."""
    match placement:
        case SeedPlacement.GAP_LINE:
            return 200
        case SeedPlacement.CENTER_PLANE:
            return 60
        case SeedPlacement.VOLUME:
            return 16


def only(value: object, fallback: float) -> float:
    """The number in a one-thumb slider's state, or the fallback for anything else."""
    # The state holds the slider's whole array: a `[value]` expression would hand Radix a fresh array on every render, and its controlled value would then never settle.
    thumbs = cast("list[object]", value) if isinstance(value, list) else []
    if len(thumbs) != 1:
        return fallback

    return number_of(thumbs[0], fallback)


class FieldViewerController(TrameComponent):
    """Owns the loaded run and its FieldScene: opens runs off the loop, publishes what they offer, and maps every display change to the plotter or the figure."""

    def __init__(self, app: Workbench, *, telemetry: Telemetry | None = None, display: DisplaySpec = DEFAULTS, open_newest: bool = True) -> None:
        """Seeds the viewer's state from `display` and registers the open hook and the state listeners.

        Args:
            app: The workbench shell the viewer runs in.
            telemetry: The telemetry root whose run index the run picker lists and run ids resolve in; None leaves the picker empty.
            display: The display the controls start from.
            open_newest: Whether the newest run opens by itself while nothing was opened or selected.
        """
        super().__init__(app.server)
        self.app = app
        self.id = FIELD_VIEWER.name
        """The name the run picker's feed logs under."""
        self.telemetry = telemetry
        self.open_newest = open_newest
        self.indexed: list[RunEntry] = []
        """The runs the index offered at its last read."""
        self.unreadable = False
        """Whether the last read of the index failed, which logs once until a read succeeds."""
        self.seen: list[Path] | None = None
        """The index shards at the feed's last read; None reads the index on the next tick."""
        self.picked_at = 0.0
        """When the run picker's dropdown last opened, on the monotonic clock."""
        self.indexed = self.scan_runs()
        self.opened: RunEntry | None = None
        """The run open or waited on, kept in the picker however the index changes."""
        self.entries = merged(self.indexed, [], None, "")
        """The runs the picker offers."""
        listed = NO_RUN if self.entries else NO_RUNS_NOTICE
        self.note = listed if telemetry is not None else NO_INDEX_NOTICE
        """What the empty view says, for a view built while no run is shown."""
        self.feed = self.runs_feed(telemetry) if telemetry is not None else None
        self.startup = display
        """The display the viewer was opened with; its body names are checked against every run shown."""
        self.hidden_bodies = display.hidden_bodies
        """The bodies every run opens with hidden."""
        self.robust = display.robust
        self.looks: dict[str, BodyStyle] = {style.body: style.model_copy(update={"color": hex_of(style.color) if style.color is not None else None}) for style in display.body_styles}
        """Every body's own color and opacity by name, kept across runs; a body without one takes the field colormap at full opacity."""
        lines = display.streamlines or LINE_DEFAULTS
        self.lines = lines
        """The field-line options the controls start from: their seeds, count and length, which the controls then set."""
        self.lines_region = lines.region_id
        """The field-line region the first run opens with, named once its bodies are known."""
        self.run: Run | None = None
        self.scene: FieldScene | None = None
        self.model_dim = 3
        self.bodies: list[str] = []
        self.viewer: FieldViewerPanel | None = None
        self.attached: set[str] = set()
        """The ids of the panels that took this controller; the last to leave releases it."""
        self.live = True
        """Whether the controller's listeners and open hook are registered."""
        self.drawn: FieldScene | None = None
        """The scene the plotter currently holds."""
        self.framed: tuple[float, ...] | None = None
        """The bounds the plotter's camera last framed, kept through a clear so the next run that frames alike keeps the view."""
        self.drawn_dim = 3
        """The dimension the last draw showed the run in, so a change of it re-frames the camera."""
        self.shown: set[str] = set()
        self.syncing = False
        self.dirty = False
        self.reveal = False
        self.generation = 0
        self.load_task: asyncio.Task[None] | None = None
        self.task: asyncio.Task[None] | None = None
        self.wait_task: asyncio.Task[None] | None = None
        self.volume_seeds = False
        """Whether a 2D run moved Volume seeds to the center plane, which the next 3D run moves back."""
        self.placement = lines.seed_placement
        """The seed placement the line count slider currently counts for."""
        self.seed_counts: dict[SeedPlacement, int] = {}
        """Each placement's own seeds per axis, so a count raised for a plane never lands on a volume."""

        self.state.update(
            {
                FieldVar.RUN: "",
                FieldVar.RUN_TEXT: "",
                FieldVar.RUN_ITEMS: [entry.item() for entry in self.entries],
                FieldVar.RUN_PLACEHOLDER: self.placeholder(self.entries),
                FieldVar.RUN_PICKING: False,
                FieldVar.RUN_ID: "",
                FieldVar.BUSY: False,
                FieldVar.MODEL_DIM: self.model_dim,
                FieldVar.FIELDS: [],
                FieldVar.FIELD: display.field,
                FieldVar.MODES: [],
                FieldVar.MODE: str(display.mode),
                FieldVar.COLORMAP: display.colormap,
                FieldVar.LOG_SCALE: display.log_scale,
                FieldVar.INVERT: display.invert,
                FieldVar.OUTLINE: display.outline,
                FieldVar.LEGEND: str(display.legend),
                FieldVar.EXPANSION: str(display.expansion),
                FieldVar.SWEEP: [display.sweep],
                FieldVar.SOLVED_AS: NO_RUN,
                FieldVar.SYMMETRY: str(SymmetryState.NONE),
                FieldVar.AUTO_CLIM: display.clim is None,
                FieldVar.CLIM: list(display.clim or (0.0, 1.0)),
                FieldVar.CLIM_MIN: 0.0,
                FieldVar.CLIM_MAX: 1.0,
                FieldVar.CLIM_STEP: 1.0 / CLIM_STEPS,
                FieldVar.VISIBLE_BODIES: [],
                FieldVar.HAS_VECTORS: False,
                FieldVar.STREAMLINES: display.streamlines is not None,
                FieldVar.SEED_PLACEMENTS: placement_items(),
                FieldVar.SEED_PLACEMENT: str(lines.seed_placement),
                FieldVar.LINE_COUNT: [min(lines.line_count, seed_limit(lines.seed_placement))],
                FieldVar.LINE_COUNT_MAX: seed_limit(lines.seed_placement),
                FieldVar.MAX_LENGTH: [lines.max_length],
                FieldVar.LINES_BODIES: body_items([]),
                FieldVar.LINES_BODY: WHOLE_DOMAIN,
                FieldVar.OPACITY: [display.opacity],
                FieldVar.OPACITY_MAPPING: display.opacity_map is not None,
                FieldVar.OPACITY_RANGE: list(display.opacity_map or OPACITY_RANGE),
                FieldVar.BODY_ROWS: [],
                FieldVar.FIELD_GRADIENT: colormap_gradient(display.colormap, reverse=display.invert) or "",
                FieldVar.BODY_COLORMAP: display.body_colormap,
                FieldVar.BODY_COLORMAP_SHOWN: SAME_AS_FIELD,
                FieldVar.BODY_LEAD: self.lead(colormap_gradient(display.colormap, reverse=display.invert)),
                FieldVar.COLORMAP_ITEMS: colormap_items(),
            }
        )
        _ = self.state.setdefault(Shared.SELECTED_RUN_ID, None)

    def attach(self, panel: PanelComponent) -> None:
        """Takes a panel of the viewer, registering the listeners, the hook and the run picker's feed again when a release had dropped them, and redrawing what the last run left."""
        if not self.live:
            self._bind_annotated_methods()
            self.live = True

        if self.feed is not None:
            self.app.when_ready(self.feed.start)

        self.attached.add(panel.id)
        if self.scene is not None:
            self.request_redraw()

    def detach(self, panel: PanelComponent) -> None:
        """Drops a panel that left the dock; the last one releases the controller, since nothing is left to show a run."""
        self.attached.discard(panel.id)
        if panel is self.viewer:
            self.viewer = None

        if not self.attached:
            self.release()

    def release(self) -> None:
        """Drops the state listeners, the open hook, the run picker's feed and the work in flight."""
        self._unbind_annotated_methods()
        self.live = False
        self.generation += 1
        if self.feed is not None:
            self.feed.stop()

        for task in (self.load_task, self.task, self.wait_task):
            if task is not None and not task.done():
                _ = task.cancel()

    def scan_runs(self) -> list[RunEntry]:
        """The runs the index offers, the last ones read when it cannot be read; a failure logs once until a read succeeds again."""
        try:
            found = run_entries(self.telemetry)
        except Exception:  # noqa: BLE001 - an unreadable index costs the picker, never the viewer
            if not self.unreadable:
                log.opt(exception=True).warning("Run index unavailable.")

            self.unreadable = True

            return self.indexed

        self.unreadable = False

        return found

    def runs_feed(self, telemetry: Telemetry) -> Feed[list[RunEntry] | None]:
        """The run picker's refresh: the index is read again only when its shards changed or a run it listed as solving lost its claim, so an idle tick costs one glob."""

        def poll() -> list[RunEntry] | None:
            shards = telemetry.runs.shards()
            ended = any(entry.ended for entry in self.indexed)
            if shards == self.seen and not ended:
                return None

            found = self.scan_runs()
            if self.unreadable:
                return None

            self.seen = shards

            return found

        return Feed(self, RUNS_PERIOD, poll, self.publish_runs)

    def publish_runs(self, found: list[RunEntry] | None) -> None:
        """Offers the runs a poll found, then opens the newest one while nothing was asked for."""
        if found is not None:
            self.indexed = found

        self.offer()
        self.open_first()

    def offer(self) -> None:
        """Publishes the picker's runs when they changed, holding them back while its dropdown is open so no row moves under the pointer."""
        held = time.monotonic() - self.picked_at < PICK_HOLD
        if bool(self.state[FieldVar.RUN_PICKING]) and held:
            return

        entries = merged(self.indexed, self.entries, self.opened, str(self.state[FieldVar.RUN]))
        placeholder = self.placeholder(entries)
        if entries == self.entries and placeholder == self.state[FieldVar.RUN_PLACEHOLDER]:
            return

        self.entries = entries
        with self.state:
            self.state[FieldVar.RUN_ITEMS] = [entry.item() for entry in entries]
            self.state[FieldVar.RUN_PLACEHOLDER] = placeholder

    def open_first(self) -> None:
        """Opens the newest run with fields, else the newest one solving, else the newest failed one, while nothing is open, opening or selected and the dock was given no run."""
        loading = self.load_task is not None and not self.load_task.done()
        asked = bool(self.state[FieldVar.RUN]) or bool(self.state[Shared.SELECTED_RUN_ID])
        if not self.open_newest or loading or asked:
            return

        solved = [entry for entry in self.entries if entry.flag is None]
        waiting = [entry for entry in self.entries if entry.flag is RunFlag.SOLVING]
        failed = [entry for entry in self.entries if entry.flag is RunFlag.FAILED]
        candidates = [*solved, *waiting, *failed]
        if not candidates:
            return

        log.bind(run_id=candidates[0].run_id).info("Opening the newest run.")
        self.open(candidates[0].value)

    def adopt(self, opened: RunEntry, path: Path) -> RunEntry:
        """Points the picker at the run being opened, with its bundle path beside it, and returns the entry the picker offers it by.

        Args:
            opened: The run's entry, flagged by what it has to show.
            path: The run's bundle directory.
        """
        self.opened = opened
        entries = merged(self.indexed, self.entries, opened, "")
        offered = next(entry for entry in entries if entry.run_id == opened.run_id)
        with self.quiet():
            self.state[FieldVar.RUN] = offered.value
            self.state[FieldVar.RUN_TEXT] = str(path)

        self.offer()

        return offered

    def placeholder(self, entries: Sequence[RunEntry]) -> str:
        """What the run picker reads while nothing is chosen, saying why when `entries` offers nothing."""
        if entries:
            return NO_RUN_SELECTED

        if self.telemetry is None:
            return NO_RUN_INDEX

        return INDEX_UNREADABLE if self.unreadable else NO_RUNS_YET

    @property
    def visible(self) -> bool:
        """Whether either view is on screen."""
        return bool(self.shown)

    def status(self, message: str) -> None:
        """Writes `message` to the shell's status line."""
        self.app.status(message)

    @contextmanager
    def quiet(self) -> Iterator[None]:
        """A state update whose change listeners must not redraw: the values come from the scene, not the user."""
        self.syncing = True
        try:
            with self.state:
                yield
        finally:
            self.syncing = False

    @controller.set(str(Hook.FIELD_OPEN))
    def open(self, location: Location) -> None:
        """Schedules opening the run at ``location`` (a run id, a bundle directory or a Run) on the server loop, with the run picker showing it."""
        with self.quiet():
            self.state[FieldVar.RUN] = str(location.path) if isinstance(location, Run) else str(location)

        self.load_task = asyncio.create_task(self.load(location))

    async def load(self, location: Location) -> None:
        """Opens the run at ``location`` off the loop and shows it; a run without fields empties the views, one still solving opens once it ends; a newer open in flight wins."""
        self.generation += 1
        token = self.generation
        waiting = False
        value = str(location.path) if isinstance(location, Run) else str(location)
        listed = next((entry for entry in self.entries if entry.value == value), None)
        name = listed.name if listed is not None else value
        self.set_busy(True)
        self.status(f"opening {name}")
        if self.scene is None:
            self.notice(f"Opening {name}")

        try:
            run = await asyncio.to_thread(self.resolve, location)
            reason = await asyncio.to_thread(missing_fields, run)
            waiting = reason is not None and await asyncio.to_thread(solving, run)
            missing = RunFlag.NO_FIELDS if reason is not None else None
            flag = RunFlag.SOLVING if waiting else missing
            opened = await asyncio.to_thread(bundle_entry, run, value, flag)
            indexed = await asyncio.to_thread(self.scan_runs)
            if token != self.generation:
                return

            self.indexed = indexed
            self.seen = None
            offered = self.adopt(opened, run.path)
            if waiting:
                self.clear(solving_notice(offered), status=f"{offered.name} is solving")
                with self.quiet():
                    self.state[FieldVar.SOLVED_AS] = SOLVING_AS

                self.wait_task = asyncio.create_task(self.await_fields(location, run.path, token))
                return

            if reason is not None:
                self.clear(f"{run.path.name}: {reason}")
                return

            scene = await asyncio.to_thread(FieldScene.from_run, run)
            if token != self.generation:
                return

            self.show(run, scene)
        except Exception as error:  # noqa: BLE001 - every failure to open a bundle ends in the status line
            log.opt(exception=True).warning("Run failed to open.")
            waiting = False
            self.clear(f"{type(error).__name__}: {error}")
        finally:
            if token == self.generation and not waiting:
                self.set_busy(False)

    async def await_fields(self, location: Location, path: Path, token: int) -> None:
        """Opens the run once its solve has ended, unless another open came first.

        Args:
            location: The location to open once the run is solved.
            path: The run's bundle directory, polled for the end of the solve.
            token: The open generation that started the wait; a newer open ends it.
        """
        while token == self.generation:
            await asyncio.sleep(FIELDS_PERIOD)
            if token != self.generation:
                return

            if not await asyncio.to_thread(solving, Run(path)):
                log.bind(run_id=path.name).info("Run solved; opening its fields.")
                self.open(location)
                return

    def clear(self, reason: str, *, status: str | None = None) -> None:
        """Empties both views and the run's controls, saying why over the view and in `status`, the reason itself when None, so nothing left on screen reads as the run asked for."""
        self.run = None
        self.scene = None
        self.drawn = None
        self.bodies = []
        if self.viewer is not None:
            self.viewer.clear()

        with self.quiet():
            self.state[FieldVar.RUN_ID] = ""
            self.state[FieldVar.FIELDS] = []
            self.state[FieldVar.MODES] = []
            self.state[FieldVar.BODY_ROWS] = []
            self.state[FieldVar.VISIBLE_BODIES] = []
            self.state[FieldVar.HAS_VECTORS] = False
            self.state[FieldVar.LINES_BODIES] = body_items([])
            self.state[FieldVar.SOLVED_AS] = NO_RUN
            self.state[FieldVar.SYMMETRY] = str(SymmetryState.NONE)

        self.notice(reason)
        self.status(status if status is not None else reason)

        log.bind(reason=reason).info("Field views cleared.")

    def notice(self, text: str) -> None:
        """Writes `text` over the empty 3D view; empty text removes it."""
        self.note = text
        if self.viewer is not None:
            self.viewer.notice(text)

    async def settle(self) -> None:
        """Waits for the open and the redraw in flight."""
        if self.load_task is not None and not self.load_task.done():
            await self.load_task

        if self.task is not None and not self.task.done():
            await self.task

    def resolve(self, location: Location) -> Run:
        """The run at a location: a Run as is, a directory as a bundle, anything else as a run id looked up in the run index."""
        if isinstance(location, Run):
            return location

        path = Path(location)
        if path.is_dir():
            return Run(path)

        if self.telemetry is None:
            raise FileNotFoundError(f"no run bundle at {path} and no telemetry root to look up a run id")

        runs = RunQuery(self.telemetry.runs.scan()).where(pl.col(RunColumn.RUN_ID) == str(location)).runs()
        if not runs:
            raise FileNotFoundError(f"no run bundle at {path} and no run {location!r} in the run index")

        return runs[0]

    def set_busy(self, busy: bool) -> None:
        """Shows or hides the busy bar."""
        with self.state:
            self.state[FieldVar.BUSY] = busy

    def show(self, run: Run, scene: FieldScene) -> None:
        """Adopts a loaded run: the knobs carry over, the field and mode snap to what it offers, every body but the hidden ones starts visible."""
        self.run = run
        self.scene = scene
        self.model_dim = scene.model_dim
        self.bodies = [str(key) for key in scene.blocks.keys()]
        spec = self.fit(self.spec_from_state(), scene)
        dim = scene.dim_for(spec)

        location = str(self.state[FieldVar.RUN])
        with self.quiet():
            self.state[FieldVar.RUN_ID] = scene.run_id
            self.state[FieldVar.MODEL_DIM] = self.model_dim
            self.state[FieldVar.SOLVED_AS] = title(scene.frame.label)
            self.state[FieldVar.SYMMETRY] = str(SymmetryState.OPEN if scene.expandable else SymmetryState.FIXED)
            self.state[FieldVar.SEED_PLACEMENTS] = placement_items(dim)
            self.state[FieldVar.SEED_PLACEMENT] = self.seeds_for(dim)
            self.state[FieldVar.FIELDS] = [{ItemField.TITLE: info.display_name, ItemField.VALUE: info.name} for info in scene.fields]
            self.state[FieldVar.FIELD] = spec.field
            self.state[FieldVar.MODES] = mode_items(scene.field_info(spec.field))
            self.state[FieldVar.MODE] = str(spec.mode)
            self.state[FieldVar.BODY_ROWS] = self.rows(scene)
            self.state[FieldVar.BODY_COLORMAP_SHOWN] = str(self.state[FieldVar.BODY_COLORMAP]) if self.painted(BodyPaint.COLORMAP) else SAME_AS_FIELD
            self.state[FieldVar.VISIBLE_BODIES] = [body for body in self.bodies if body not in self.hidden_bodies]
            self.state[FieldVar.HAS_VECTORS] = has_vector_field(scene.fields)
            self.state[FieldVar.LINES_BODIES] = body_items(self.bodies)
            self.state[FieldVar.LINES_BODY] = self.lines_body(scene)

        unmatched: list[str] = self.startup.unmatched(self.bodies)
        if unmatched:
            log.bind(run_id=scene.run_id, unmatched=unmatched, bodies=self.bodies).warning("Display names bodies this run lacks; check hidden_bodies and body_styles against its body names.")

        self.notice("")
        self.reveal = True
        self.request_redraw()
        self.app.offer_tour()

        log.bind(run_id=scene.run_id, model_dim=self.model_dim, cells=scene.grid.n_cells, location=location).info("Run shown.")
        self.status(f"{scene.run_id}: {len(scene.fields)} fields, {len(self.bodies)} bodies, {self.model_dim}D")

    def seeds_for(self, dim: int) -> str:
        """The seed placement for a run shown in `dim` dimensions: a flat one has no volume, so Volume seeds move to the center plane until a solid one takes them back."""
        current = SeedPlacement(str(self.state[FieldVar.SEED_PLACEMENT]))
        if dim == 2 and current is SeedPlacement.VOLUME:
            self.volume_seeds = True
            return str(SeedPlacement.CENTER_PLANE)

        if dim != 2 and self.volume_seeds:
            self.volume_seeds = False
            if current is SeedPlacement.CENTER_PLANE:
                return str(SeedPlacement.VOLUME)

        return str(current)

    def lines_body(self, scene: FieldScene) -> str:
        """The field-line region to show: the startup region on the first run that has it, else the current one while the run still has that body."""
        region, self.lines_region = self.lines_region, None
        named = [body.name for body in scene.bodies if body.mesh_id == region and body.name in self.bodies]
        if named:
            return named[0]

        current = str(self.state[FieldVar.LINES_BODY])

        return current if current in self.bodies else WHOLE_DOMAIN

    def members(self, row_id: object) -> list[str]:
        """The bodies a table row stands for, nothing for a row the current run does not have."""
        rows = cast("list[Item]", self.state[FieldVar.BODY_ROWS])
        found = [row for row in rows if row[RowKey.ID] == row_id]

        return names_of(found[0][RowKey.MEMBERS]) if found else []

    def look(self, body: str) -> BodyStyle:
        """A body's own style, or the field colormap at full opacity for a body without one."""
        return self.looks.get(body) or BodyStyle(body=body)

    def restyle(self, bodies: Sequence[str], *, redraw: bool = True, **changes: object) -> None:
        """Applies `changes` to the bodies' looks, republishes the table and redraws unless told not to.

        Args:
            bodies: The bodies to restyle.
            redraw: Whether the scene redraws; False while only a slider thumb moved.
            changes: The BodyStyle fields to set.
        """
        for body in bodies:
            self.looks[body] = self.look(body).model_copy(update=changes)

        self.publish_rows()
        if redraw:
            self.request_redraw()

    def rows(self, scene: FieldScene) -> list[Item]:
        """The body table's rows for the scene's bodies and the current looks."""
        return body_rows(scene.bodies, self.bodies, self.looks, str(self.state[FieldVar.BODY_COLORMAP]))

    def publish_rows(self) -> None:
        """Puts the body table's rows for the current looks and body colormap in the state, and what the body colormap picker reads."""
        if self.scene is None:
            return

        with self.state:
            self.state[FieldVar.BODY_ROWS] = self.rows(self.scene)
            self.state[FieldVar.BODY_COLORMAP_SHOWN] = str(self.state[FieldVar.BODY_COLORMAP]) if self.painted(BodyPaint.COLORMAP) else SAME_AS_FIELD

    def painted(self, paint: BodyPaint) -> list[str]:
        """The run's bodies whose color comes from `paint`."""
        return [name for name in self.bodies if self.look(name).paint is paint]

    def lead(self, gradient: str | None) -> ComboboxItem:
        """The body colormap picker's first entry, drawn in the field colormap."""
        item: ComboboxItem = {"value": SAME_AS_FIELD, "label": SAME_AS_FIELD_LABEL}
        if gradient is not None:
            item["swatch"] = gradient

        return item

    def on_body_colormap_pick(self, value: object) -> None:
        """Same as field returns the body colormap's bodies to the field colormap; a colormap, picked while no body takes one, takes the shown field-colored bodies, else every shown body."""
        if value == SAME_AS_FIELD:
            self.restyle(self.painted(BodyPaint.COLORMAP), paint=BodyPaint.FIELD, color=None)
            return

        with self.state:
            self.state[FieldVar.BODY_COLORMAP] = str(value)

        if self.painted(BodyPaint.COLORMAP):
            return

        shown = names_of(self.state[FieldVar.VISIBLE_BODIES])
        fielded = [name for name in self.painted(BodyPaint.FIELD) if name in shown]
        self.restyle(fielded or shown, paint=BodyPaint.COLORMAP, color=None)

    def on_row_visible(self, row_id: object, checked: object) -> None:
        """A row's checkbox: shows or hides every body it stands for."""
        members = self.members(row_id)
        visible = [name for name in names_of(self.state[FieldVar.VISIBLE_BODIES]) if name not in members]
        shown = set(visible if checked is not True else [*visible, *members])
        with self.state:
            self.state[FieldVar.VISIBLE_BODIES] = [name for name in self.bodies if name in shown]

    def on_row_colormap(self, row_id: object) -> None:
        """A row's field chip: its bodies take their share of the body colormap."""
        self.restyle(self.members(row_id), paint=BodyPaint.COLORMAP, color=None)

    def on_row_field(self, row_id: object) -> None:
        """A row's clear button: its bodies take the field colormap again."""
        self.restyle(self.members(row_id), paint=BodyPaint.FIELD, color=None)

    def on_row_color(self, row_id: object, color: object) -> None:
        """A row's color picked by hand: its bodies keep it until cleared."""
        self.restyle(self.members(row_id), paint=BodyPaint.SOLID, color=str(color))

    def on_row_opacity(self, row_id: object, value: object) -> None:
        """A row's opacity thumb moved: the looks and the table follow at once, the scene on the release."""
        self.restyle(self.members(row_id), redraw=False, opacity=number_of(value, 1.0))

    @change(FieldVar.BODY_COLORMAP)
    def on_body_colormap(self, **_: object) -> None:
        """Republishes the body table after the body colormap changed."""
        self.publish_rows()

    def fit(self, spec: DisplaySpec, scene: FieldScene) -> DisplaySpec:
        """The spec snapped to the scene's fields, falling back to its first field when the current one is not offered."""
        try:
            return spec.fitted(scene.fields)
        except KeyError:
            return spec.model_copy(update={"field": DEFAULTS.field}).fitted(scene.fields)

    def spec_from_state(self) -> DisplaySpec:
        """The display spec the controls currently describe; a color range the lookup table cannot map falls back to automatic."""
        state = self.state
        log_scale = bool(state[FieldVar.LOG_SCALE])
        clim = None if bool(state[FieldVar.AUTO_CLIM]) else pair_of(state[FieldVar.CLIM])
        if clim is not None and (clim[0] >= clim[1] or (log_scale and clim[0] <= 0.0)):
            clim = None

        visible = set(names_of(state[FieldVar.VISIBLE_BODIES]))
        hidden = tuple(name for name in self.bodies if name not in visible)
        expansion = Expansion(str(state[FieldVar.EXPANSION]))
        scene = self.scene
        dim = expansion.dim if scene is not None and scene.expandable else self.model_dim
        drawing_lines = bool(state[FieldVar.STREAMLINES]) and bool(state[FieldVar.HAS_VECTORS])
        streamlines = self.streamline_options(dim) if drawing_lines else None
        opacity_map = pair_of(state[FieldVar.OPACITY_RANGE]) if bool(state[FieldVar.OPACITY_MAPPING]) else None

        return DisplaySpec(
            field=str(state[FieldVar.FIELD]),
            mode=DisplayMode(str(state[FieldVar.MODE])),
            colormap=str(state[FieldVar.COLORMAP]),
            log_scale=log_scale,
            invert=bool(state[FieldVar.INVERT]),
            clim=clim,
            robust=self.robust,
            legend=Legend(str(state[FieldVar.LEGEND])),
            expansion=expansion,
            sweep=only(state[FieldVar.SWEEP], DEFAULTS.sweep),
            hidden_bodies=hidden,
            outline=bool(state[FieldVar.OUTLINE]),
            opacity=only(state[FieldVar.OPACITY], DEFAULTS.opacity),
            opacity_map=opacity_map,
            body_styles=tuple(look for look in (self.look(name) for name in self.bodies) if look.paint is not BodyPaint.FIELD or look.opacity != 1.0),
            body_colormap=str(state[FieldVar.BODY_COLORMAP]),
            streamlines=streamlines,
        )

    def streamline_options(self, dim: int) -> FieldLineOptions:
        """The startup field-line options with what the controls describe; a run shown flat seeds its center plane in place of a volume."""
        state = self.state
        scene = self.scene
        body = scene.bodies.get(str(state[FieldVar.LINES_BODY])) if scene is not None else None
        region = body.mesh_id if body is not None else None
        vectors = self.vector_field()
        placement = SeedPlacement(str(state[FieldVar.SEED_PLACEMENT]))
        if dim == 2 and placement is SeedPlacement.VOLUME:
            placement = SeedPlacement.CENTER_PLANE

        return self.lines.model_copy(
            update={
                "seed_placement": placement,
                "line_count": int(only(state[FieldVar.LINE_COUNT], LINE_DEFAULTS.line_count)),
                "max_length": only(state[FieldVar.MAX_LENGTH], LINE_DEFAULTS.max_length),
                "region_array": GEOMETRY_IDS_ARRAY if region is not None else None,
                "region_id": region,
                "clip_to_region": region is not None,
                "show_outline": False,
                "vectors": vectors,
            }
        )

    def vector_field(self) -> str | None:
        """The vector field the lines follow, by [`line_vectors`][femforge.viz.line_vectors]; None without a run or a vector field."""
        if self.scene is None:
            return None

        return line_vectors(self.scene.fields, str(self.state[FieldVar.FIELD]))

    def request_redraw(self) -> None:
        """Redraws with the latest state once the loop is free; bursts of changes collapse into one draw."""
        self.dirty = True
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.drain())

    async def drain(self) -> None:
        """Redraws until no change arrived during the last draw; a failed draw lands in the status line."""
        while self.dirty:
            self.dirty = False
            try:
                self.publish_seeds()
                await self.redraw(self.spec_from_state())
            except Exception as error:  # noqa: BLE001 - a display the scene cannot draw ends in the status line
                log.opt(exception=True).warning("Field redraw failed.")
                self.status(f"{type(error).__name__}: {error}")
                self.settle_expansion()

    def settle_expansion(self) -> None:
        """Puts the Show As choice back to what the scene shows after a draw failed, so the controls never claim an expansion the view lacks."""
        scene = self.scene
        if scene is None:
            return

        shown = str(scene.expanded[0])
        if str(self.state[FieldVar.EXPANSION]) != shown:
            with self.quiet():
                self.state[FieldVar.EXPANSION] = shown

    async def redraw(self, spec: DisplaySpec) -> None:
        """Draws the spec into the view, then publishes the color range it produced."""
        scene = self.scene
        if scene is None:
            return

        await self.draw_plotter(scene, spec)
        if scene is self.scene:
            self.publish_range(scene)

    async def draw_plotter(self, scene: FieldScene, spec: DisplaySpec) -> None:
        """Draws or recolors the scene in the plotter, keeping the camera across runs that still frame it; new field lines are integrated off the loop."""
        viewer = self.viewer
        if viewer is None:
            return

        dim = scene.dim_for(spec)
        if self.drawn is not scene:
            await self.expand(scene, spec)
            if scene is not self.scene:
                return

            bounds = tuple(scene.bounds)
            keep = self.framed is not None and similar_bounds(self.framed, bounds)
            scene.spec = spec.model_copy(update={"streamlines": None})
            viewer.render(scene, keep_camera=keep)
            if not keep:
                viewer.look(View.TOP if dim == 2 else View.ISO)

            self.drawn = scene
            self.framed = bounds
        else:
            changing = scene.expansion_of(spec.fitted(scene.fields)) != scene.expanded
            if changing:
                await self.expand(scene, spec)
                if scene is not self.scene:
                    return

            kept = scene.spec.streamlines if spec.streamlines is not None and not changing else None
            scene.update(viewer.plotter, spec.model_copy(update={"streamlines": kept}))
            if changing:
                self.framed = tuple(scene.bounds)
                viewer.look(View.TOP if dim == 2 else View.ISO) if dim != self.drawn_dim else viewer.fit()
                if viewer.oversized(viewer.footprint):
                    viewer.fall_back(viewer.footprint)
            else:
                viewer.push()

        self.drawn_dim = dim
        self.activate()

        wanted = spec.streamlines
        if wanted is None or wanted == scene.spec.streamlines:
            return

        cached = scene.integrated(wanted)
        if cached is not None:
            scene.show_streamlines(viewer.plotter, cached, wanted)
            viewer.push()
            return

        self.status("integrating field lines")
        actors = await asyncio.to_thread(scene.integrate_streamlines, wanted)
        if scene is not self.scene or self.drawn is not scene:
            return

        scene.show_streamlines(viewer.plotter, actors, wanted)
        viewer.push()
        self.status(f"field lines: {actors.streamlines.n_cells} segments from {wanted.line_count} seeds per axis")

    async def expand(self, scene: FieldScene, spec: DisplaySpec) -> None:
        """Expands the scene for the spec off the loop under the busy bar, which an open still in flight keeps up."""
        self.set_busy(True)
        try:
            _ = await asyncio.to_thread(scene.expand, spec)
        finally:
            if self.load_task is None or self.load_task.done():
                self.set_busy(False)

    def activate(self) -> None:
        """Brings the view's tab to the front once per opened run, unless it is already on screen."""
        if not self.reveal:
            return

        self.reveal = False
        if str(FieldPanel.VIEW_3D) in self.shown or self.app.dock is None:
            return

        self.app.dock.active_panel(str(FieldPanel.VIEW_3D))

    def display_dim(self) -> int:
        """The dimension the controls will show the run in: the chosen expansion's for a section, else the solved one."""
        scene = self.scene
        if scene is None or not scene.expandable:
            return self.model_dim

        return Expansion(str(self.state[FieldVar.EXPANSION])).dim

    def publish_seeds(self) -> None:
        """Offers the seed placements the coming draw's dimension allows, moving Volume seeds to the center plane while the run is shown flat, before the draw reads them."""
        if self.scene is None:
            return

        dim = self.display_dim()
        with self.quiet():
            self.state[FieldVar.SEED_PLACEMENTS] = placement_items(dim)
            self.state[FieldVar.SEED_PLACEMENT] = self.seeds_for(dim)

    def publish_range(self, scene: FieldScene) -> None:
        """Puts the data range under the range slider and, while automatic, the limits the draw used on it."""
        lo, hi = scene.data_range
        with self.quiet():
            self.state[FieldVar.CLIM_MIN] = lo
            self.state[FieldVar.CLIM_MAX] = hi
            self.state[FieldVar.CLIM_STEP] = (hi - lo) / CLIM_STEPS
            if bool(self.state[FieldVar.AUTO_CLIM]):
                self.state[FieldVar.CLIM] = list(scene.clim)

    def on_visible(self, panel_id: str, visible: bool) -> None:
        """Tracks whether the view is on screen; the viewer follows the shared run selection only while it is."""
        was = self.visible
        if visible:
            self.shown.add(panel_id)
        else:
            self.shown.discard(panel_id)

        if self.visible and not was:
            self.follow_selection()

    def follow_selection(self, *, again: bool = False) -> None:
        """Opens the run the shared selection names when it is not the one shown, or `again` regardless, for a run picked a second time."""
        selected = self.state[Shared.SELECTED_RUN_ID]
        if not isinstance(selected, str) or not selected:
            return

        if not again and self.scene is not None and self.scene.run_id == selected:
            return

        self.open(selected)

    def on_commit(self) -> None:
        """A slider thumb released: its value has been in the state since the drag began, and is drawn now."""
        if self.scene is None:
            return

        self.request_redraw()

    def on_clim_commit(self) -> None:
        """A range thumb released: the range stays where it was dragged, which turns Auto Range off, and is drawn now."""
        if self.scene is None:
            return

        with self.state:
            self.state[FieldVar.AUTO_CLIM] = False

        self.request_redraw()

    @change(*DISPLAY_KEYS)
    def on_display(self, **_: object) -> None:
        """Redraws after a display control changed, unless the scene itself set it."""
        if self.syncing or self.scene is None:
            return

        self.request_redraw()

    @change(FieldVar.FIELD)
    def on_field(self, **_: object) -> None:
        """Offers the chosen field's modes and snaps the mode to one of them."""
        scene = self.scene
        if self.syncing or scene is None:
            return

        try:
            info = scene.field_info(str(self.state[FieldVar.FIELD]))
        except KeyError:
            return

        self.state[FieldVar.MODES] = mode_items(info)
        if str(self.state[FieldVar.MODE]) not in {str(mode) for mode in info.modes}:
            self.state[FieldVar.MODE] = str(info.modes[0])

    @change(FieldVar.SEED_PLACEMENT)
    def on_placement(self, **_: object) -> None:
        """Keeps the count the last placement had and hands the new one its own, within its own range."""
        placement = SeedPlacement(str(self.state[FieldVar.SEED_PLACEMENT]))
        if placement is self.placement:
            return

        self.seed_counts[self.placement] = int(only(self.state[FieldVar.LINE_COUNT], self.lines.line_count))
        self.placement = placement
        limit = seed_limit(placement)
        count = self.seed_counts.get(placement, self.lines.line_count)

        self.state[FieldVar.LINE_COUNT_MAX] = limit
        self.state[FieldVar.LINE_COUNT] = [max(LINE_COUNT_MIN, min(count, limit))]

    @change(FieldVar.COLORMAP, FieldVar.INVERT)
    def on_colormap(self, **_: object) -> None:
        """Repaints the field chips of the body table and the body picker's Same as field in the colormap the scene draws."""
        gradient = colormap_gradient(str(self.state[FieldVar.COLORMAP]), reverse=bool(self.state[FieldVar.INVERT]))
        self.state[FieldVar.FIELD_GRADIENT] = gradient or ""
        self.state[FieldVar.BODY_LEAD] = self.lead(gradient)

    @change(FieldVar.RUN)
    def on_run(self, **_: object) -> None:
        """Opens the location the run picker or the bundle-path field set."""
        location = str(self.state[FieldVar.RUN])
        if self.syncing or not location:
            return

        self.open(location)

    @change(FieldVar.RUN_PICKING)
    def on_picking(self, **_: object) -> None:
        """Notes when the run picker's dropdown opens, and offers the runs it held back once it closes."""
        if bool(self.state[FieldVar.RUN_PICKING]):
            self.picked_at = time.monotonic()

            return

        self.offer()

    @change(Shared.SELECTED_RUN_ID)
    def on_selected_run(self, **_: object) -> None:
        """Opens the run another panel selected while a view is on screen."""
        if self.visible:
            self.follow_selection(again=True)


class FieldViewerPanel(ScenePanel):
    """The 3D view of the field scene, mirrored in the browser by default; its visibility feeds the controller."""

    def __init__(self, app: Workbench, id: str, title: str, controller: FieldViewerController) -> None:
        """Builds the 3D view and attaches it to the controller.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            controller: The viewer's controller, shared with the controls.
        """
        self.controller = controller

        super().__init__(app, id, title)

        controller.viewer = self
        controller.drawn = None
        controller.framed = None
        controller.attach(self)
        if controller.scene is None:
            self.notice(controller.note)

    def clear(self) -> None:
        """Empties the plotter and pushes the empty frame."""
        self.plotter.clear()
        self.renderable = None
        self.push()

    def on_visible(self, visible: bool) -> None:
        """Tells the scene and the controller whether the view is on screen."""
        super().on_visible(visible)
        self.controller.on_visible(self.id, visible)

    def dispose(self) -> None:
        """Detaches from the controller, then frees the view."""
        self.controller.detach(self)
        super().dispose()


class FieldControlsPanel(PanelComponent):
    """The viewer's controls: the field, mode and colormap selects, the display switches, the colour range, the body tree and the field-line options."""

    def __init__(self, app: Workbench, id: str, title: str, controller: FieldViewerController) -> None:
        """Builds the controls and attaches them to the controller.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            controller: The viewer's controller, shared with the view.
        """
        self.controller = controller

        super().__init__(app, id, title)

        controller.attach(self)
        app.add_tour_steps(TOUR_STEPS)

    def build_run_picker(self) -> None:
        """The run select, disabled while it has nothing to offer, and beside it the open run's bundle path to read or copy; built into the app bar, its one place."""
        with html.Div(classes=RUN_GROUP, id=RUN_TOUR_ID):
            with labelled("Run"):
                picker(
                    str(FieldVar.RUN),
                    str(FieldVar.RUN_ITEMS),
                    placeholder=react.Bind(str(FieldVar.RUN_PLACEHOLDER)),
                    width=RUN_WIDTH,
                    disabled=react.Bind(f"!{FieldVar.RUN_ITEMS}.length"),
                    label="Run",
                    menu=RUN_MENU,
                    on_open_change=setter(str(FieldVar.RUN_PICKING)),
                )

            Input(value=react.Bind(str(FieldVar.RUN_TEXT)), read_only=True, placeholder="Bundle Path", classes=PATH_WIDTH, aria_label="Bundle path")

    def build_colormap(self) -> None:
        """The field colormap picker: searchable, filtered by type and favorites, the arrow keys stepping through it while it is closed."""
        Combobox(
            model=str(FieldVar.COLORMAP),
            items=react.Bind(str(FieldVar.COLORMAP_ITEMS)),
            filters=COLORMAP_FILTERS,
            search_placeholder="Search Colormaps",
            aria_label="Colormap",
        )

    def build_body_colormap(self) -> None:
        """The body colormap picker as a swatch in the color column, reading Same as field while no body takes it."""
        Combobox(
            value=react.Bind(str(FieldVar.BODY_COLORMAP_SHOWN)),
            on_value_change=self.callback(self.controller.on_body_colormap_pick, args=f"[{EVENT}]"),
            items=react.Bind(str(FieldVar.COLORMAP_ITEMS)),
            lead=react.Bind(str(FieldVar.BODY_LEAD)),
            filters=COLORMAP_FILTERS,
            compact=True,
            search_placeholder="Search Colormaps",
            aria_label="Body colormap",
        )

    def slider(self, model: FieldVar, label: str, bounds: tuple[float, float | react.Bind], step: float) -> None:
        """A slider over `model` whose thumbs move with the drag and whose scene follows the release.

        Args:
            model: The state variable holding the thumb values.
            label: The slider's accessible name.
            bounds: The low and high ends; the high end may be a client expression.
            step: The step between values.
        """
        Slider(
            value=react.Bind(str(model)),
            on_value_change=setter(str(model)),
            on_value_commit=self.callback(self.controller.on_commit),
            min_=bounds[0],
            max_=bounds[1],
            step=step,
            aria_label=label,
        )

    def build_range(self) -> None:
        """The Auto Range switch over the two-thumb color range across the data range, its ends read out above it and the colormap between its thumbs; a moved thumb pins the range."""
        setting(FieldVar.AUTO_CLIM, "Auto range")
        with html.Div(classes=STACK):
            readouts(react.Bind(f"{FieldVar.CLIM}[0].toPrecision({PRECISION})"), react.Bind(f"{FieldVar.CLIM}[1].toPrecision({PRECISION})"))
            Slider(
                value=react.Bind(str(FieldVar.CLIM)),
                on_value_change=setter(str(FieldVar.CLIM)),
                on_value_commit=self.callback(self.controller.on_clim_commit),
                min_=react.Bind(str(FieldVar.CLIM_MIN)),
                max_=react.Bind(str(FieldVar.CLIM_MAX)),
                step=react.Bind(str(FieldVar.CLIM_STEP)),
                classes=RANGE_TRACK,
                style={RANGE_VAR: react.Bind(str(FieldVar.FIELD_GRADIENT))},
                aria_label="Color range",
            )

    def build_legend(self) -> None:
        """The legend's three states as one segmented choice."""
        segmented(FieldVar.LEGEND, "Legend", [str(legend) for legend in Legend])

    def build_opacity(self) -> None:
        """The Opacity mapping switch and, while it is on, the opacity at each end of the color range; the body table holds the overall opacity."""
        setting(FieldVar.OPACITY_MAPPING, "Opacity mapping")
        with react.If(str(FieldVar.OPACITY_MAPPING)), html.Div(classes=STACK):
            readouts(react.Bind(f"{FieldVar.OPACITY_RANGE}[0].toFixed(2)"), react.Bind(f"{FieldVar.OPACITY_RANGE}[1].toFixed(2)"))
            self.slider(FieldVar.OPACITY_RANGE, "Opacity", OPACITY_RANGE, OPACITY_STEP)

    def build_symmetry(self) -> None:
        """The symmetry section: what the run was solved as and, for a section, how it is expanded and how far a revolution sweeps."""
        readouts("Solved as", react.Bind(str(FieldVar.SOLVED_AS)))
        with react.If(f"{FieldVar.SYMMETRY} === {json.dumps(str(SymmetryState.OPEN))}"), html.Div(classes=str(Cls.SECTION_BODY_STACK)):
            segmented(FieldVar.EXPANSION, "Show as", [str(expansion) for expansion in Expansion], read=expansion_label)
            with react.If(f"{FieldVar.EXPANSION} === {json.dumps(str(Expansion.REVOLVE))}"), html.Div(classes=STACK):
                readouts("Sweep", react.Bind(f"{FieldVar.SWEEP}[0] + {json.dumps(DEGREES)}"))
                self.slider(FieldVar.SWEEP, "Sweep angle", SWEEP_RANGE, SWEEP_STEP)

        with react.If(f"{FieldVar.SYMMETRY} === {json.dumps(str(SymmetryState.FIXED))}"):
            html.Span(NOTHING_TO_EXPAND, classes=str(Cls.CAPTION))

    def build_field(self) -> None:
        """The field section: what is shown and how it is colored, from the field select to the opacity mapping."""
        with captioned("Quantity"):
            picker(str(FieldVar.FIELD), str(FieldVar.FIELDS), placeholder=NO_RUN, width=FULL_WIDTH)

        with captioned("Mode"):
            picker(str(FieldVar.MODE), str(FieldVar.MODES), placeholder=NO_RUN, width=FULL_WIDTH)

        with captioned("Colormap"):
            self.build_colormap()

        with html.Div(classes=STACK):
            setting(FieldVar.INVERT, "Invert colormap")
            setting(FieldVar.LOG_SCALE, "Log scale")
            setting(FieldVar.OUTLINE, "Outline")

        self.build_range()
        self.build_legend()
        self.build_opacity()

    def build_bodies(self) -> None:
        """The body table: the All bodies row with the body colormap, then a row per body and per material group, every row checkbox, name, color and opacity."""
        controller = self.controller
        visible = str(FieldVar.VISIBLE_BODIES)
        row = ITEM
        members = f"{row}.{RowKey.MEMBERS}"

        def opacity_slider(value: str, change: react.Callback, commit: react.Callback, label: str | react.Bind) -> None:
            Slider(
                value=react.Bind(value),
                on_value_change=change,
                on_value_commit=commit,
                min_=OPACITY_RANGE[0],
                max_=OPACITY_RANGE[1],
                step=OPACITY_STEP,
                aria_label=label,
            )

        with html.Div(classes=BODY_TABLE):
            with html.Div(classes=BODY_ROW):
                Checkbox(
                    checked=react.Bind(f"{visible}.length === 0 ? false : {visible}.length === {FieldVar.BODY_ROWS}.filter(r => !r.{RowKey.GROUP}).length || 'indeterminate'"),
                    on_checked_change=react.Callback(f"trame.state.set({json.dumps(visible)}, {EVENT} === true ? {FieldVar.BODY_ROWS}.filter(r => !r.{RowKey.GROUP}).map(r => r.{RowKey.ID}) : [])"),
                    aria_label="Show every body",
                )
                html.Span(ALL_BODIES, classes=GROUP_NAME)
                self.build_body_colormap()
                opacity_slider(str(FieldVar.OPACITY), setter(str(FieldVar.OPACITY)), self.callback(controller.on_commit), "Opacity of every body")

            with react.For(items=str(FieldVar.BODY_ROWS), name=row), html.Div(classes=BODY_ROW):
                Checkbox(
                    checked=react.Bind(f"{members}.every(m => {visible}.includes(m)) || ({members}.some(m => {visible}.includes(m)) && 'indeterminate')"),
                    on_checked_change=self.callback(controller.on_row_visible, args=f"[{row}.{RowKey.ID}, {EVENT}]"),
                    aria_label=react.Bind(f"'Show ' + {row}.{RowKey.TITLE}"),
                )
                html.Span(
                    [react.Bind(f"{row}.{RowKey.TITLE}")],
                    classes=react.Bind(f"{row}.{RowKey.GROUP} ? {json.dumps(GROUP_NAME)} : {row}.{RowKey.MEMBER} ? {json.dumps(MEMBER_NAME)} : {json.dumps(BODY_NAME)}"),
                    title=react.Bind(f"{row}.{RowKey.TITLE}"),
                )
                with html.Span(classes=CHIP_SLOT):
                    painted = f"{row}.{RowKey.PAINT} !== {json.dumps(str(BodyPaint.FIELD))}"
                    with react.If(painted):
                        ColorInput(
                            value=react.Bind(f"{row}.{RowKey.COLOR}"),
                            on_change=self.callback(controller.on_row_color, args=f"[{row}.{RowKey.ID}, {EVENT}.target.value]"),
                            classes=COLOR_CHIP,
                            title=react.Bind(f"{row}.{RowKey.PAINT} === {json.dumps(str(BodyPaint.COLORMAP))} ? 'From the body colormap: pick to fix a color' : 'A fixed color'"),
                            aria_label=react.Bind(f"{row}.{RowKey.TITLE} + ' color'"),
                        )
                        with Button(
                            size="icon-xs",
                            variant="ghost",
                            classes=CHIP_CLEAR,
                            title="Back to the field colormap",
                            aria_label=react.Bind(f"{row}.{RowKey.TITLE} + ' back to the field colormap'"),
                            on_click=self.callback(controller.on_row_field, args=f"[{row}.{RowKey.ID}]"),
                        ):
                            Icon("x")

                    with react.If(f"!({painted})"):
                        Button(
                            size="icon-xs",
                            variant="ghost",
                            classes=CHIP,
                            style={"background": react.Bind(str(FieldVar.FIELD_GRADIENT))},
                            title="Field colormap: click for the body colormap",
                            aria_label=react.Bind(f"{row}.{RowKey.TITLE} + ' to the body colormap'"),
                            on_click=self.callback(controller.on_row_colormap, args=f"[{row}.{RowKey.ID}]"),
                        )

                opacity_slider(
                    f"{row}.{RowKey.OPACITY}",
                    self.callback(controller.on_row_opacity, args=f"[{row}.{RowKey.ID}, {EVENT}[0]]"),
                    self.callback(controller.on_commit),
                    react.Bind(f"{row}.{RowKey.TITLE} + ' opacity'"),
                )

    def build_lines(self) -> None:
        """The field-line section: the switch, the seeds, the region and the two integration sliders."""
        setting(FieldVar.STREAMLINES, "Show field lines", disabled=react.Bind(f"!{FieldVar.HAS_VECTORS}"))

        with captioned("Seeds"):
            picker(str(FieldVar.SEED_PLACEMENT), str(FieldVar.SEED_PLACEMENTS), width=FULL_WIDTH)

        with captioned("Integrate within"):
            picker(str(FieldVar.LINES_BODY), str(FieldVar.LINES_BODIES), placeholder=WHOLE_DOMAIN_LABEL, width=FULL_WIDTH)

        with html.Div(classes=STACK):
            readouts("Seed count", react.Bind(seed_count()))
            self.slider(FieldVar.LINE_COUNT, "Seeds per axis", (LINE_COUNT_MIN, react.Bind(str(FieldVar.LINE_COUNT_MAX))), 1)

        with html.Div(classes=STACK):
            readouts("Length", react.Bind(f"{FieldVar.MAX_LENGTH}[0].toPrecision(2)"))
            self.slider(FieldVar.MAX_LENGTH, "Line length", MAX_LENGTH_RANGE, MAX_LENGTH_STEP)

    def build(self) -> None:
        """Builds the busy bar over the field, field-line and body sections."""

        def section(value: str, heading: str, body: Callable[[], None]) -> None:
            with AccordionItem(value=value, id=f"{TOUR_PREFIX}{value}", classes=str(Cls.SECTION_ITEM)):
                AccordionTrigger(heading, classes=str(Cls.SECTION_TRIGGER))
                with AccordionContent(classes=str(Cls.SECTION_BODY)):
                    body()

        with html.Div(classes=str(Cls.CONTROLS)):
            with react.If(str(FieldVar.BUSY)):
                Progress(value=BUSY_FULL, classes=str(Cls.BUSY))

            with Accordion(type_="multiple", default_value=OPEN_SECTIONS, classes=str(Cls.SECTIONS)):
                section(SYMMETRY_SECTION, "Symmetry", self.build_symmetry)
                section(FIELD_SECTION, "Field", self.build_field)
                section(LINES_SECTION, "Field Lines", self.build_lines)
                section(BODIES_SECTION, "Bodies", self.build_bodies)

    def dispose(self) -> None:
        """Detaches from the controller, then releases the panel."""
        self.controller.detach(self)
        super().dispose()


def workbench(run: Result | Location | None = None, *, display: DisplaySpec = DEFAULTS, telemetry: str | Path | None = None) -> dock.Split:
    """The field viewer dock: the controls beside the view.

    Args:
        run: The run to open once the server runs: a Result, a Run, a run id or a bundle directory; None opens the newest indexed run once one lands.
        display: The display the controls start from.
        telemetry: The telemetry root whose run index the run picker lists; None reads FEMFORGE_TELEMETRY.
    """
    root = resolve_root(telemetry)
    index = Telemetry(root) if root is not None else None
    location = Run.from_result(run) if isinstance(run, Result) else run
    shared: FieldViewerController | None = None

    def controller_for(app: Workbench) -> FieldViewerController:
        nonlocal shared
        if shared is None or shared.app is not app:
            shared = FieldViewerController(app, telemetry=index, display=display, open_newest=location is None)
            if location is not None:
                app.when_ready(partial(shared.open, location))

        return shared

    def controls(app: Workbench, id: str, title: str) -> FieldControlsPanel:
        panel = FieldControlsPanel(app, id, title, controller_for(app))
        with app.header:
            panel.build_run_picker()

        return panel

    def view_3d(app: Workbench, id: str, title: str) -> FieldViewerPanel:
        return FieldViewerPanel(app, id, title, controller_for(app))

    return dock.Split([dock.Panel(FIELD_VIEWER.title, controls, id=FieldPanel.CONTROLS, weight=1), dock.Panel("View", view_3d, id=FieldPanel.VIEW_3D, weight=3)])
