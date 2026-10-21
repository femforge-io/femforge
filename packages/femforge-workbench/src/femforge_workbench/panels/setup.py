"""The Setup tab: what each body and boundary group of the open run received from the solver, in the solve frame, with the scene glyphs it shows."""

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, TypedDict, cast

from trame.decorators import change
from trame.widgets import html, react

from femforge.common.files import BundleFile
from femforge.common.log import log
from femforge.common.tags import TagDict, TagKey, parse_tags
from femforge.common.utils import title
from femforge.elmer.variables import ElmerVariable
from femforge.frames import BodyMismatch, Frame, GeometryCheck
from femforge.frames.base import axis_word
from femforge.materials.properties import MaterialProperties
from femforge.meshing.naming import name_fragment
from femforge.physical.conditions import Condition, ConditionGlyph, SummaryPart
from femforge.physical.domain import EntityTag
from femforge.physical.readout import readout
from femforge.physical.units import U, quantity
from femforge.run.conditions import ConditionRecord, ConditionsFile
from femforge.run.entities import Body, Boundary, BoundaryKind, Entities
from femforge.run.inspect import Run
from femforge.run.manifest import RunManifest
from femforge.viz.glyphs import EntityKind, EntityRef
from femforge.viz.symmetry import Expansion
from femforge_workbench.panels import Cls, PanelComponent
from femforge_workbench.panels.field_viewer import STACK, TIMES, FieldVar, expansion_label, names_of, only, setting
from femforge_workbench.widgets.accordion import Accordion, AccordionContent, AccordionItem, AccordionTrigger
from femforge_workbench.widgets.busy import Busy
from femforge_workbench.widgets.button import Button
from femforge_workbench.widgets.element import EVENT, setter
from femforge_workbench.widgets.entity_grid import ConditionLine, EntityGrid, EntityRow, LinePart
from femforge_workbench.widgets.field import FieldLabel
from femforge_workbench.widgets.icon import Icon, IconName
from femforge_workbench.widgets.input import COMMIT_VALUE, Input
from femforge_workbench.widgets.reveal import Reveal
from femforge_workbench.widgets.switch import Switch
from femforge_workbench.widgets.toggle import ToggleGroup, ToggleGroupItem


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench
    from femforge_workbench.panels.field_viewer import FieldViewerController


FILTER_ABOVE = 12
"""The boundary-group count above which the Boundaries table starts on the groups that received a condition."""

SEARCH_ID = "ff-setup-search"
NO_RUN = "No run open. Pick one in the Run picker in the app bar."
NO_INDEX = "No run index. Pass telemetry to the workbench to list runs."
OPEN_FAILED = "The run could not be opened."
NO_CONDITIONS = "No Conditions"
NO_GLYPHS = "No glyphs in this run."
NO_GLYPHS_AUTHORED = "Glyphs need a mesh; this run failed before meshing."
ARROW_NOTE = "Arrow length scales with |F|."
NO_GHOST = "No authored geometry in this bundle."
GHOST_WARPED = "Undeformed; the solved body is warped "
EXPONENT_ABOVE = 1e6
"""The warp scale above which the ghost caption reads it in exponent form."""
NO_CELLS_FIELDS = "Not in the fields file: no condition landed here."
NO_CELLS_MESH = "Not in the mesh files: re-solve to restore the mesh."
HIDDEN_LABEL = "Hidden in the Field Viewer; press S to show it."
CELLS_UNREADABLE = "Highlight unavailable: the scene's groups failed to load."
GLYPH_SHOWN = "Glyph Shown"
GLYPH_HIDDEN = "Glyph Hidden"
HIDDEN_BY_LEGEND = "Glyph hidden by the legend"
HIDDEN_BY_SWITCH = "Glyph hidden by Show Glyphs"
NBSP = "\u00a0"
SEPARATOR = " · "
AUTOMATIC = "(Set by femforge)"
OVERRIDDEN = "(Overridden)"
OVERRIDDEN_TIP = "A later condition on this group set the same keywords, so these never reached the solver."
LEGACY_NAMES = "Names recovered from mesh.names."
SOLVING_MESH = "Solving · glyphs drawn on the mesh."
NO_FIELDS_MESH = "No fields · glyphs drawn on the mesh."
SOLVING_PLAIN = "Solving · showing the mesh."
NO_FIELDS_PLAIN = "No fields · showing the mesh."
NO_MESH = "Glyphs need the mesh; re-solve to restore it."
NO_FRAME = "Frame Not Recorded"
FAILED_BEFORE_MESH = "The run failed before meshing. Showing the authored tags; re-run it to mesh and list its groups."
NAME_COLUMN = "Name"
GLYPH_COLUMN = "Glyph"
TAG_COLUMN = "Tag"
MATERIAL_COLUMN = "Material"
NO_SIDECAR = "This run recorded no conditions.json, so its conditions are not listed; re-solve it to list what each group received."
SETS_NONE = "This run sets no conditions; every boundary takes the solver's natural condition."
MATERIAL_PREFIX = "material:"
TAG_PREFIX = "tag:"
FRAME_SECTION = "frame"
TAGS_SECTION = "tags"
BODIES_SECTION = "bodies"
BOUNDARIES_SECTION = "boundaries"
GLYPHS_SECTION = "glyphs"
OPEN_SECTIONS = [FRAME_SECTION, TAGS_SECTION, BODIES_SECTION, BOUNDARIES_SECTION, GLYPHS_SECTION]
HEADER = "flex flex-1 items-center gap-2"
COUNT = "ml-auto font-normal text-muted-foreground tabular-nums"
NOTICE_ROW = "flex items-start gap-1.5 px-2 text-label text-muted-foreground"
SEARCH_ROW = "flex shrink-0 items-center gap-1 px-2 pt-2"
SETTINGS = "flex shrink-0 flex-col gap-1.5 px-2"
FILTER_GROUP = "grid w-full grid-cols-2"
LEGEND_ROW = "grid h-6 grid-cols-[1rem_minmax(0,1fr)_2rem_auto] items-center gap-x-2"
SR_ONLY = "sr-only"
EMPTY_STACK = "flex min-w-0 flex-col gap-1 px-4 text-center"
EMPTY_DETAIL = "break-all text-muted-foreground"


class SetupState(StrEnum):
    """What the Setup tab has to show for the open run."""

    NONE = "none"
    """No run is open."""

    AUTHORED = "authored"
    """The run failed before meshing, so only its authored tags are known."""

    GROUPS = "groups"
    """The run has mesh groups to list."""


class FilterChoice(StrEnum):
    """Which boundary groups the Boundaries table lists."""

    WITH_CONDITIONS = "with_conditions"
    """Only the groups that received a condition."""

    ALL = "all"
    """Every group."""


class NoticeLevel(StrEnum):
    """How a notice over the tables reads: a fact, or a warning."""

    INFO = "info"
    """A fact about the run."""

    WARNING = "warning"
    """Something kept the run from producing what the tab lists."""


class SetupVar(StrEnum):
    """The Setup tab's state variables; the scene keys it writes are the field viewer's own."""

    STATE = "setup_state"
    QUERY = "setup_query"
    SEARCH_KEY = "setup_search_key"
    """Bumped to remount the search field, which is uncontrolled, after the server cleared the query."""

    FILTER = "setup_filter"
    FRAME_TITLE = "setup_frame_title"
    FRAME_FACTS = "setup_frame_facts"
    CHECK = "setup_check"
    """The Geometry Check lines: one per failing body and test, worst first, or the single verdict."""

    CHECK_FAILED = "setup_check_failed"
    IS_2D = "setup_is_2d"
    HAS_GHOST = "setup_has_ghost"
    HAS_CONDITIONS = "setup_has_conditions"
    """Whether the run recorded its conditions, the only time the Boundaries filter can tell which groups received one."""

    BOUNDARY_COLUMNS = "setup_boundary_columns"
    """The Boundaries table's column headers, its measure named by the frame."""

    HAS_SCENE = "setup_has_scene"
    """Whether the Field Viewer draws a scene of the open run, the only time the scene switches do anything."""

    BODY_ROWS = "setup_body_rows"
    BOUNDARY_ROWS = "setup_boundary_rows"
    TAG_ROWS = "setup_tag_rows"
    BODY_COUNT = "setup_body_count"
    BOUNDARY_COUNT = "setup_boundary_count"
    TAG_COUNT = "setup_tag_count"
    MORE = "setup_more"
    LEGEND_ROWS = "setup_legend_rows"
    NOTICE = "setup_notice"
    NOTICE_LEVEL = "setup_notice_level"
    CAPTION = "setup_caption"
    EMPTY = "setup_empty"
    """What the tab says while no run is open: why, from the Field Viewer's reason."""

    EMPTY_DETAIL = "setup_empty_detail"
    """The failure line of a run that could not be opened, empty otherwise."""

    WARP_CAPTION = "setup_warp_caption"
    """The ghost's note on the warp the solved body is drawn with."""

    MISS = "setup_miss"
    """The no-match notice of a search, empty while something matches."""

    ANNOUNCE = "setup_announce"
    """The polite live-region text that reads a selection change aloud."""


class FactRow(TypedDict):
    """One row of the Frame section."""

    label: str
    """The row's label."""

    value: str
    """The row's value."""


class LegendRow(TypedDict):
    """One glyph kind the run draws, with how many groups draw it."""

    kind: str
    """The ``ConditionGlyph`` value."""

    label: str
    """The kind's name."""

    count: int
    """How many groups draw the kind."""


@dataclass(frozen=True)
class SetupModel:
    """Everything the Setup tab reads from one run, each part None or empty when the bundle lacks it."""

    run_id: str
    """The run bundle's name."""

    frame: Frame | None
    """The frame the run solved in; None when its manifest cannot be read."""

    entities: Entities
    """The run's body and boundary groups with their measured geometry."""

    conditions: ConditionsFile | None
    """What each group received from the sif; None for a run that recorded none."""

    tags: Sequence[EntityTag] = ()
    """The authored domain tags with their conditions."""

    materials: Sequence[MaterialProperties] = ()
    """The authored materials, for their full names."""

    check: GeometryCheck | None = None
    """The frame's verdict on the authored geometry; None for a solid or a run that recorded none."""

    has_fields: bool = False
    """Whether the run wrote fields."""

    has_mesh: bool = False
    """Whether the Elmer mesh is still in the bundle, which the glyphs of a run without fields are drawn on."""

    has_ghost: bool = False
    """Whether the bundle holds the authored bodies a 2D frame sectioned."""

    legacy_names: bool = False
    """Whether the group names were recovered from ``mesh.names`` for a bundle without ``entities.json``."""

    solving: bool = False
    """Whether the run is still solving."""

    @property
    def state(self) -> SetupState:
        """Groups once the run has any, else only the authored tags."""
        entities = self.entities
        if len(entities.bodies) or len(entities.boundaries):
            return SetupState.GROUPS

        return SetupState.AUTHORED


@dataclass(frozen=True)
class RowView:
    """What the tables show besides the run: the search, the boundary filter and the scene state the rows reflect."""

    query: str = ""
    """The search text; empty matches everything."""

    filter: FilterChoice = FilterChoice.ALL
    """Which boundary groups the Boundaries table lists."""

    visible: frozenset[str] | None = None
    """The bodies the Field Viewer shows; None while no scene names them."""

    rows_off: frozenset[str] = frozenset()
    """The entity ids whose glyphs a row switched off."""

    highlightable: frozenset[str] | None = None
    """The entity ids the scene has cells of; None while unknown, which dims nothing."""

    unreadable: bool = False
    """Whether the scene's group cells failed to read, which dims every row for that reason."""

    drawn: bool = True
    """Whether the Field Viewer draws a scene of the run, the only time a row's glyph toggle does anything."""

    glyphs: bool = True
    """Whether Show Glyphs is on."""

    kinds_off: frozenset[str] = frozenset()
    """The glyph kinds the legend switched off."""


@dataclass(frozen=True)
class Listing:
    """One table's rows and how many groups it holds before the search and the filter."""

    rows: list[EntityRow]
    """The rows shown."""

    total: int
    """Every group the run has of the kind."""

    unlisted: int = 0
    """The groups the search matched that the filter left out."""


def read_setup(run: Run, *, solving: bool = False) -> SetupModel:
    """Everything the Setup tab shows of ``run``, tolerating a missing or outdated manifest and a missing or unreadable sidecar.

    Args:
        run: The opened run bundle.
        solving: Whether the run is still solving.
    """
    try:
        manifest: RunManifest | None = run.manifest
    except (FileNotFoundError, ValueError):
        log.bind(run_id=run.path.name).info("Run manifest unreadable.")
        manifest = None

    try:
        conditions: ConditionsFile | None = run.conditions()
    except (OSError, ValueError):
        log.bind(run_id=run.path.name).opt(exception=True).warning("Conditions sidecar unreadable.")
        conditions = None

    domain = manifest.config.domain if manifest is not None else None

    return SetupModel(
        run_id=run.path.name,
        frame=manifest.config.frame if manifest is not None else None,
        entities=run.entities,
        conditions=conditions,
        tags=tuple(domain.tags) if domain is not None else (),
        materials=tuple(domain.materials) if domain is not None else (),
        check=manifest.geometry_check if manifest is not None else None,
        has_fields=run.vtu_path is not None,
        has_mesh=run.mesh_groups() is not None,
        has_ghost=(run.path / BundleFile.GEOMETRY_AUTHORED).is_file(),
        legacy_names=not (run.path / BundleFile.ENTITIES).is_file() and (run.path / BundleFile.MESH_NAMES).is_file(),
        solving=solving,
    )


def authored_names(model: SetupModel) -> tuple[str, ...]:
    """Every ``Name`` the run's bodies, tags and condition selectors were authored with."""
    selectors: list[TagDict] = [dict(body.tags) for body in model.entities.bodies]
    selectors.extend(tag.selector for tag in model.tags)
    if model.conditions is not None:
        groups = [*model.conditions.bodies, *model.conditions.boundaries]
        selectors.extend(record.selector for group in groups for record in group.conditions)

    names = [selector[TagKey.NAME] for selector in selectors if TagKey.NAME in selector]

    return tuple(dict.fromkeys(names))


def display_name(group: str, names: Sequence[str]) -> str:
    """The authored name a group was named after, such as ``RightFace`` for ``RIGHTFACE``; the group name itself when none matches."""
    return next((name for name in names if name_fragment(name) == group), group)


def boundary_name(boundary: Boundary, names: Sequence[str]) -> str:
    """How a boundary group reads: a named face by its authored name, an adjacency group by the bodies it borders, such as ``Flexure · External``."""
    bordering = [display_name(body, names) for body in boundary.bodies]
    lead = bordering[0] if bordering else boundary.name
    match boundary.kind:
        case BoundaryKind.NAMED:
            return display_name(boundary.name, names)
        case BoundaryKind.EXTERNAL:
            return f"{lead}{SEPARATOR}External"
        case BoundaryKind.AXIS:
            return f"{lead}{SEPARATOR}Axis"
        case BoundaryKind.INTERFACE:
            return " ↔ ".join(bordering) if bordering else boundary.name


def order(boundary: Boundary, *, conditioned: bool) -> int:
    """The boundary's rank in the table: named faces, then other groups with an authored condition, then interfaces, externals and axis traces."""
    if boundary.kind is BoundaryKind.NAMED:
        return 0

    if conditioned:
        return 1

    match boundary.kind:
        case BoundaryKind.NAMED | BoundaryKind.INTERFACE:
            return 2
        case BoundaryKind.EXTERNAL:
            return 3
        case BoundaryKind.AXIS:
            return 4


def icon_of(glyph: ConditionGlyph, *, negative: bool = False) -> IconName:
    """The legend icon of a glyph kind; a negative current density points into the plane."""
    match glyph:
        case ConditionGlyph.SURFACE_FORCE:
            return "arrow-big-right-dash"
        case ConditionGlyph.FIXED_DISPLACEMENT:
            return "anchor"
        case ConditionGlyph.MAGNETIZATION:
            return "magnet"
        case ConditionGlyph.CURRENT_DENSITY:
            return "circle-x" if negative else "circle-dot"
        case ConditionGlyph.THERMAL:
            return "flame"
        case ConditionGlyph.VOLTAGE:
            return "zap"


def legend_label(glyph: ConditionGlyph) -> str:
    """The name a glyph kind's legend row reads."""
    match glyph:
        case ConditionGlyph.VOLTAGE:
            return ElmerVariable.POTENTIAL.display_name
        case ConditionGlyph.SURFACE_FORCE | ConditionGlyph.FIXED_DISPLACEMENT | ConditionGlyph.MAGNETIZATION | ConditionGlyph.CURRENT_DENSITY | ConditionGlyph.THERMAL:
            return title(glyph.value)


def dropped_tip(component: str, frame: Frame | None) -> str:
    """Why the frame drops an authored axis: it runs along no solve axis, whose authored directions the tip names, such as ``solve X and Y run along +Y and +Z``."""
    lead = f"Authored {component.upper()} runs along no solve axis"
    plane = frame.plane if frame is not None else None
    if frame is None or plane is None:
        return f"{lead}."

    axes = "radial and axial" if frame.revolves else "solve X and Y"

    return f"{lead}: {axes} run along {axis_word(plane.x_dir)} and {axis_word(plane.y_dir)}."


def line_of(lead: str, parts: Sequence[tuple[str, bool, str]]) -> ConditionLine:
    """A condition line from its lead word and its parts as text, muted flag and tooltip: plain parts merge into one run, a muted part keeps its own."""
    runs: list[LinePart] = []
    plain = lead
    for index, (text, muted, tip) in enumerate(parts):
        separator = (" " if lead else "") if index == 0 else SEPARATOR
        if not muted and not tip:
            plain += separator + text
            continue

        if plain + separator:
            runs.append({"text": plain + separator})

        part: LinePart = {"text": text, "muted": muted}
        if tip:
            part["tip"] = tip

        runs.append(part)
        plain = ""

    if plain:
        runs.append({"text": plain})

    return {"parts": runs}


def condition_lines(record: ConditionRecord, frame: Frame | None) -> list[ConditionLine]:
    """The lines one emission reads as: its kind and the solver-side summary in ``frame``, each authored field the frame dropped muted with the reason, and whether a later emission overrode it."""
    kind = title(record.kind)
    condition = record.condition
    if condition is None:
        return [line_of(kind, [(AUTOMATIC, True, "")])]

    emitted = {keyword.name: keyword.value for keyword in record.keywords}
    overridden = bool(record.keywords) and all(keyword.shadowed for keyword in record.keywords)

    def read(part: SummaryPart) -> tuple[str, bool, str]:
        text = f"{part.label} {part.value}" if part.label else part.value
        if part.component is not None and part.component in record.dropped:
            return text, True, dropped_tip(part.component, frame)

        return text, False, part.tip

    lines: list[ConditionLine] = []
    for number, summary in enumerate(condition.summary(emitted, frame=frame)):
        parts = [read(part) for part in summary]
        if number == 0 and overridden:
            parts.append((OVERRIDDEN, True, OVERRIDDEN_TIP))

        lines.append(line_of(kind if number == 0 else "", parts))

    return lines


def line_text(line: ConditionLine) -> str:
    """A condition line as plain text."""
    return "".join(part.get("text", "") for part in line["parts"])


def measure_cell(boundary: Boundary, frame: Frame | None) -> tuple[str, str]:
    """A boundary's measure column and its tooltip: the area in 3D, the length of a 2D line with the surface it stands for, empty when unmeasured."""
    if frame is None or frame.model_dim == 3:
        area = boundary.measure
        text = readout(quantity(area, "m**2")) if area is not None else ""

        return text, ""

    length = boundary.length
    if length is None:
        return "", ""

    fact = frame.face_measure(length, boundary.centroid) if boundary.centroid is not None else None
    tip = f"{fact.label} {fact.value}" if fact is not None else ""

    return readout(quantity(length, "m")), tip


def blocks_of(records: Sequence[ConditionRecord]) -> list[str]:
    """The sif blocks a group's emissions landed in, such as ``Boundary Condition 2``, each once."""
    return list(dict.fromkeys(f"{record.section} {record.block}" for record in records))


def matches(texts: Sequence[str], query: str) -> bool:
    """Whether any of ``texts`` contains ``query``, ignoring case; an empty query matches everything."""
    needle = query.strip().casefold()

    return not needle or any(needle in text.casefold() for text in texts)


def count_text(shown: int, total: int) -> str:
    """A section's count: the total, or ``shown of total`` while a search or filter hides some."""
    return str(total) if shown == total else f"{shown} of {total}"


def records_for(model: SetupModel, kind: EntityKind, name: str) -> list[ConditionRecord]:
    """The emissions a group received; empty for a run without the sidecar.

    Args:
        model: The run.
        kind: Whether the group is a body or a boundary.
        name: The group name.
    """
    conditions = model.conditions
    if conditions is None:
        return []

    group = conditions.body(name) if kind is EntityKind.BODY else conditions.boundary(name)

    return list(group.conditions) if group is not None else []


def entity_row(
    model: SetupModel,
    view: RowView,
    ref: EntityRef,
    name: str,
    records: Sequence[ConditionRecord],
    *,
    detail: str = "",
    detail_tip: str = "",
    tip: Sequence[str] = (),
) -> EntityRow:
    """The row of one group: its detail, tooltip facts, lines, glyph toggle, highlight state and an accessible name that reads them all.

    Args:
        model: The run.
        view: The search, filter and scene state.
        ref: The group.
        name: The name the row reads.
        records: The emissions the group received.
        detail: The material or measure column.
        detail_tip: The detail's tooltip.
        tip: The name tooltip's facts, the Elmer name first.
    """

    def drawn_glyph() -> tuple[ConditionGlyph, bool] | None:
        for record in records:
            condition = record.condition
            glyph = condition.glyph if condition is not None else None
            if glyph is None:
                continue

            numbers = [keyword.value for keyword in record.keywords if isinstance(keyword.value, int | float) and not isinstance(keyword.value, bool)]

            return glyph, any(number < 0 for number in numbers)

        return None

    def spoken_text(line: ConditionLine) -> str:
        def spoken(part: LinePart) -> str:
            text = part.get("text", "")
            tip = part.get("tip", "")

            return f"{text} ({tip})" if tip else text

        return "".join(spoken(part) for part in line["parts"])

    lines = [line for record in records for line in condition_lines(record, model.frame)]
    if not lines and model.conditions is not None:
        lines = [line_of(NO_CONDITIONS, [])]

    row: EntityRow = {"id": ref.id, "name": name, "lines": lines, "detail": detail, "tip": list(tip)}
    if detail_tip:
        row["detail_tip"] = detail_tip

    glyphed = drawn_glyph()
    read_detail = f"{detail} ({detail_tip})" if detail and detail_tip else detail
    spoken = [name, read_detail, *(spoken_text(line) for line in lines)]
    if glyphed is not None:
        glyph, negative = glyphed
        row["icon"] = icon_of(glyph, negative=negative)

    if glyphed is not None and view.drawn:
        shown = ref.id not in view.rows_off
        overruled = HIDDEN_BY_SWITCH if not view.glyphs else HIDDEN_BY_LEGEND if glyphed[0].value in view.kinds_off else ""
        row["glyph_shown"] = shown
        if overruled:
            row["glyph_note"] = overruled

        spoken.append(overruled or (GLYPH_SHOWN if shown else GLYPH_HIDDEN))

    spoken.extend(tip)
    unlit = view.highlightable is not None and ref.id not in view.highlightable
    if view.unreadable or unlit:
        cause = NO_CELLS_FIELDS if model.has_fields else NO_CELLS_MESH
        row["dimmed"] = True
        row["dim_tip"] = CELLS_UNREADABLE if view.unreadable else cause
        spoken.append(row["dim_tip"])

    if view.drawn and not row.get("dimmed"):
        row["fit"] = True

    row["label"] = ", ".join(text for text in spoken if text)

    return row


def body_rows(model: SetupModel, view: RowView) -> Listing:
    """The Bodies table: a material shared by several bodies as a group row over them, then the rest, each with its material and conditions."""
    names = authored_names(model)
    bodies = list(model.entities.bodies)

    def body_measure(body: Body) -> str:
        measure = body.measure
        if measure is None:
            return ""

        if model.frame is not None and model.frame.model_dim == 2:
            return f"Section Area {readout(quantity(measure, 'm**2'))}"

        return f"Volume {readout(quantity(measure, 'm**3'), unit=U.mm**3)}"

    def material_name(tag: str) -> str:
        return next((candidate.name for candidate in model.materials if candidate.tag == tag), "")

    def row(body: Body, *, member: bool) -> EntityRow:
        ref = EntityRef(EntityKind.BODY, body.name)
        records = records_for(model, EntityKind.BODY, body.name)
        facts = [body.name, "Body", body_measure(body), *blocks_of(records)]
        material = body.material or ""
        shown = "" if member else material
        entry = entity_row(
            model,
            view,
            ref,
            display_name(body.name, names),
            records,
            detail=shown,
            detail_tip=material_name(shown) if shown else "",
            tip=[fact for fact in facts if fact],
        )
        if member:
            entry["member"] = True

        if view.visible is not None and body.name not in view.visible:
            entry["hidden"] = True
            entry["label"] = f"{entry.get('label', body.name)}, {HIDDEN_LABEL}"

        return entry

    def found(body: Body) -> bool:
        records = records_for(model, EntityKind.BODY, body.name)
        kinds = [title(record.kind) for record in records]

        return matches([display_name(body.name, names), body.name, body.material or "", *kinds], view.query)

    shown = [body for body in bodies if found(body)]
    materials = [body.material for body in bodies if body.material is not None]
    shared = [material for material in dict.fromkeys(materials) if materials.count(material) > 1]
    rows: list[EntityRow] = []
    for material in shared:
        members = [body for body in shown if body.material == material]
        if not members:
            continue

        heading: EntityRow = {"id": f"{MATERIAL_PREFIX}{material}", "name": material, "detail": f"{len(members)} Bodies", "group": True}
        full = material_name(material)
        if full:
            heading["tip"] = [material, full]

        heading["label"] = ", ".join(text for text in (material, full, f"{len(members)} Bodies") if text)
        rows.append(heading)
        rows.extend(row(body, member=True) for body in members)

    rows.extend(row(body, member=False) for body in shown if body.material not in shared)

    return Listing(rows=rows, total=len(bodies))


def boundary_rows(model: SetupModel, view: RowView) -> Listing:
    """The Boundaries table, ranked named faces first, the groups the filter leaves out counted for the More row; a block femforge set ranks by its group's kind but passes the filter."""
    names = authored_names(model)
    boundaries = list(model.entities.boundaries)

    def found(boundary: Boundary) -> bool:
        records = records_for(model, EntityKind.BOUNDARY, boundary.name)
        bordering = [display_name(body, names) for body in boundary.bodies]
        kinds = [title(record.kind) for record in records]

        return matches([boundary_name(boundary, names), boundary.name, *bordering, *kinds], view.query)

    def rank(boundary: Boundary) -> tuple[int, str]:
        records = records_for(model, EntityKind.BOUNDARY, boundary.name)
        conditioned = any(record.condition is not None for record in records)

        return order(boundary, conditioned=conditioned), boundary_name(boundary, names).casefold()

    def kind_word(kind: BoundaryKind) -> str:
        match kind:
            case BoundaryKind.NAMED:
                return "Named Face"
            case BoundaryKind.EXTERNAL:
                return "External Shell"
            case BoundaryKind.AXIS:
                return "Symmetry Axis"
            case BoundaryKind.INTERFACE:
                return "Interface"

    def row(boundary: Boundary) -> EntityRow:
        ref = EntityRef(EntityKind.BOUNDARY, boundary.name)
        records = records_for(model, EntityKind.BOUNDARY, boundary.name)
        detail, tip = measure_cell(boundary, model.frame)
        bordering = ", ".join(display_name(body, names) for body in boundary.bodies)
        facts = [boundary.name, kind_word(boundary.kind), f"Borders {bordering}" if bordering else "", *blocks_of(records)]

        return entity_row(model, view, ref, boundary_name(boundary, names), records, detail=detail, detail_tip=tip, tip=[fact for fact in facts if fact])

    matched = sorted((boundary for boundary in boundaries if found(boundary)), key=rank)
    filtering = view.filter is FilterChoice.WITH_CONDITIONS and model.conditions is not None
    listed = [boundary for boundary in matched if not filtering or records_for(model, EntityKind.BOUNDARY, boundary.name)]

    return Listing(rows=[row(boundary) for boundary in listed], total=len(boundaries), unlisted=len(matched) - len(listed))


def tag_rows(model: SetupModel, query: str) -> Listing:
    """The Authored Tags table of a run that failed before meshing: each tag's selector and the conditions it carries."""

    def authored_lines(condition: Condition) -> list[ConditionLine]:
        kind = title(str(getattr(condition, "kind", "")))
        summary = condition.summary()

        return [line_of(kind if number == 0 else "", [(f"{part.label} {part.value}" if part.label else part.value, False, "") for part in parts]) for number, parts in enumerate(summary)]

    rows: list[EntityRow] = []
    for index, tag in enumerate(model.tags):
        name = ", ".join(f"{key}={value}" for key, value in tag.selector.items()) or "Every Entity"
        lines = [line for condition in tag.conditions for line in authored_lines(condition)] or [line_of(NO_CONDITIONS, [])]
        texts = [line_text(line) for line in lines]
        if not matches([name, *texts], query):
            continue

        row: EntityRow = {"id": f"{TAG_PREFIX}{index}", "name": name, "lines": lines, "label": ", ".join([name, *texts])}
        glyph = next((condition.glyph for condition in tag.conditions if condition.glyph is not None), None)
        if glyph is not None:
            row["icon"] = icon_of(glyph)

        rows.append(row)

    return Listing(rows=rows, total=len(model.tags))


def legend_rows(model: SetupModel) -> list[LegendRow]:
    """One row per glyph kind the run draws, in legend order, with how many groups draw it."""
    conditions = model.conditions
    groups = [*conditions.bodies, *conditions.boundaries] if conditions is not None else []
    kinds = [{record.condition.glyph for record in group.conditions if record.condition is not None and record.condition.glyph is not None} for group in groups]

    return [{"kind": str(glyph), "label": legend_label(glyph), "count": sum(glyph in found for found in kinds)} for glyph in ConditionGlyph if any(glyph in found for found in kinds)]


def frame_facts(model: SetupModel, expansion: Expansion, sweep: float) -> list[FactRow]:
    """The Frame section's rows: the frame's own facts, then how a revolved section is shown.

    Args:
        model: The run.
        expansion: How the Field Viewer shows the section.
        sweep: `[°]`: How far a revolve sweeps.
    """
    frame = model.frame
    if frame is None:
        return []

    rows: list[FactRow] = [{"label": fact.label, "value": fact.value} for fact in frame.facts()]
    if frame.revolves:
        shown = f"{expansion_label(expansion)} {sweep:g}°" if expansion is Expansion.REVOLVE else expansion_label(expansion)
        rows.append({"label": "Shown As", "value": shown})

    return rows


def check_line(check: GeometryCheck | None, frame: Frame | None) -> tuple[list[str], bool]:
    """The Geometry Check lines and whether the check failed: ``Passed``, one line per failing body and test worst first, or none for a frame that checks nothing."""
    if check is None:
        return (["Not Recorded"] if frame is not None and frame.plane is not None else []), False

    mismatches = sorted(check.mismatches, key=lambda mismatch: mismatch.fraction, reverse=True)
    if not mismatches:
        return ["Passed"], False

    def line(mismatch: BodyMismatch) -> str:
        body = parse_tags(mismatch.body).get(TagKey.NAME, mismatch.body)

        share = f"(Mismatch {100.0 * mismatch.fraction:.3g} %)".replace(" ", NBSP)

        return f"{body} Fails {title(mismatch.check.value)} Test {share}"

    return [line(mismatch) for mismatch in mismatches], True


def notice_of(model: SetupModel) -> tuple[str, NoticeLevel]:
    """The notice over the tables, empty for none: a failed-before-meshing warning, a run without the sidecar, or a run that sets no conditions."""
    if model.state is SetupState.AUTHORED:
        return FAILED_BEFORE_MESH, NoticeLevel.WARNING

    conditions = model.conditions
    if conditions is None:
        return NO_SIDECAR, NoticeLevel.INFO

    groups = [*conditions.bodies, *conditions.boundaries]
    if not any(group.conditions for group in groups):
        return SETS_NONE, NoticeLevel.INFO

    return "", NoticeLevel.INFO


def empty_text(failure: str | None, *, indexed: bool) -> str:
    """What the tab says while no run is open: that the run failed to open, that there is no run index to pick from, or where to pick one."""
    if failure is not None:
        return OPEN_FAILED

    return NO_RUN if indexed else NO_INDEX


def scale_text(scale: float) -> str:
    """A warp scale at three significant figures, grouped in thousands, such as ``22,700`` or ``1.5``; exponent form above a million."""
    if abs(scale) > EXPONENT_ABOVE:
        return f"{scale:.2e}"

    rounded = float(f"{scale:.3g}")
    if rounded == 0.0:
        return "0"

    decimals = max(0, 2 - math.floor(math.log10(abs(rounded))))
    text = f"{rounded:,.{decimals}f}"

    return text.rstrip("0").rstrip(".") if decimals else text


def caption_of(model: SetupModel) -> str:
    """The caption over the tables: what a run without fields shows, its glyphs named only when it draws some, and where legacy names came from."""
    parts: list[str] = []
    draws = bool(legend_rows(model))
    fieldless = model.state is SetupState.GROUPS and not model.has_fields
    if fieldless and model.has_mesh:
        glyphed = SOLVING_MESH if model.solving else NO_FIELDS_MESH
        plain = SOLVING_PLAIN if model.solving else NO_FIELDS_PLAIN
        parts.append(glyphed if draws else plain)
    elif fieldless and draws:
        parts.append(NO_MESH)

    if model.legacy_names:
        parts.append(LEGACY_NAMES)

    return SEPARATOR.join(parts)


def miss_text(query: str, shown: int) -> str:
    """The no-match notice of a search that left every table empty, else empty."""
    if not query.strip() or shown:
        return ""

    return f"No bodies or boundaries match '{query.strip()}'."


def boundary_columns(frame: Frame | None) -> list[str]:
    """The Boundaries table's column headers: the measure reads Length for a frame that meshes faces as lines, Area otherwise."""
    measure = "Length" if frame is not None and frame.model_dim == 2 else "Area"

    return [NAME_COLUMN, measure, GLYPH_COLUMN]


class SetupPanel(PanelComponent):
    """The Setup tab beside the field viewer's controls: the frame, every body and boundary group with what it received, and the glyph legend."""

    def __init__(self, app: "Workbench", id: str, title: str, controller: "FieldViewerController") -> None:
        """Builds the tab and attaches it to the controller, showing the run it already holds.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            controller: The field viewer's controller, shared with the controls and the view.
        """
        self.controller = controller
        self.model: SetupModel | None = None
        self.rows: list[EntityRow] = []
        """The rows last published, for the names the announcements read."""
        defaults: dict[str, object] = {
            SetupVar.STATE: str(SetupState.NONE),
            SetupVar.QUERY: "",
            SetupVar.SEARCH_KEY: 0,
            SetupVar.FILTER: str(FilterChoice.ALL),
            SetupVar.FRAME_TITLE: NO_FRAME,
            SetupVar.FRAME_FACTS: [],
            SetupVar.CHECK: [],
            SetupVar.CHECK_FAILED: False,
            SetupVar.IS_2D: False,
            SetupVar.HAS_GHOST: False,
            SetupVar.HAS_CONDITIONS: False,
            SetupVar.BOUNDARY_COLUMNS: boundary_columns(None),
            SetupVar.HAS_SCENE: False,
            SetupVar.BODY_ROWS: [],
            SetupVar.BOUNDARY_ROWS: [],
            SetupVar.TAG_ROWS: [],
            SetupVar.BODY_COUNT: "0",
            SetupVar.BOUNDARY_COUNT: "0",
            SetupVar.TAG_COUNT: "0",
            SetupVar.MORE: "",
            SetupVar.LEGEND_ROWS: [],
            SetupVar.NOTICE: "",
            SetupVar.NOTICE_LEVEL: str(NoticeLevel.INFO),
            SetupVar.CAPTION: "",
            SetupVar.EMPTY: NO_RUN,
            SetupVar.EMPTY_DETAIL: "",
            SetupVar.WARP_CAPTION: "",
            SetupVar.MISS: "",
            SetupVar.ANNOUNCE: "",
        }
        for key, value in defaults.items():
            _ = app.state.setdefault(key, value)

        super().__init__(app, id, title)

        controller.setup_panel = self
        controller.attach(self)
        self.on_warp_scale()
        self.model = controller.setup
        self.publish()

    def view(self) -> RowView:
        """What the tables show besides the run, from the state."""
        state = self.state
        controller = self.controller

        return RowView(
            query=str(state[SetupVar.QUERY] or ""),
            filter=FilterChoice(str(state[SetupVar.FILTER])),
            visible=frozenset(names_of(state[FieldVar.VISIBLE_BODIES])) if controller.bodies else None,
            rows_off=frozenset(names_of(state[FieldVar.GLYPH_ROWS_OFF])),
            highlightable=controller.highlightable,
            unreadable=controller.cells_unreadable,
            drawn=controller.scene is not None or controller.groups is not None,
            glyphs=bool(state[FieldVar.GLYPHS]),
            kinds_off=frozenset(names_of(state[FieldVar.GLYPH_KINDS_OFF])),
        )

    def show_run(self, model: SetupModel | None, *, fresh: bool = True) -> None:
        """Shows a run; a fresh one resets its glyph toggles, selection and filter, while the search and the scene switches carry over.

        Args:
            model: The run, None when none is open.
            fresh: Whether the run differs from the one shown, False when a solving run reopens with its fields.
        """
        self.model = model
        if not fresh:
            self.publish()
            return

        with self.state:
            self.state[FieldVar.GLYPH_ROWS_OFF] = []
            self.state[FieldVar.GLYPH_KINDS_OFF] = []
            self.state[FieldVar.SELECTED_ENTITY] = ""
            self.state[SetupVar.ANNOUNCE] = ""
            if model is not None:
                many = len(model.entities.boundaries) > FILTER_ABOVE and model.conditions is not None
                self.state[SetupVar.FILTER] = str(FilterChoice.WITH_CONDITIONS if many else FilterChoice.ALL)

        self.publish()

    def publish(self) -> None:
        """Puts the open run's frame, tables, counts, legend and notices in the state."""
        model = self.model
        if model is None:
            failure = self.controller.failure
            self.rows = []
            with self.state:
                self.state[SetupVar.STATE] = str(SetupState.NONE)
                self.state[SetupVar.EMPTY] = empty_text(failure, indexed=self.controller.telemetry is not None)
                self.state[SetupVar.EMPTY_DETAIL] = failure or ""
                self.state[SetupVar.BODY_ROWS] = []
                self.state[SetupVar.BOUNDARY_ROWS] = []
                self.state[SetupVar.TAG_ROWS] = []
                self.state[SetupVar.LEGEND_ROWS] = []
                self.state[SetupVar.HAS_SCENE] = False

            return

        view = self.view()
        bodies = body_rows(model, view)
        boundaries = boundary_rows(model, view)
        tags = tag_rows(model, view.query)
        notice, level = notice_of(model)
        check, failed = check_line(model.check, model.frame)
        state = model.state
        shown = len(tags.rows) if state is SetupState.AUTHORED else len(bodies.rows) + len(boundaries.rows)
        self.rows = [*bodies.rows, *boundaries.rows]
        with self.state:
            self.state[SetupVar.STATE] = str(state)
            self.state[SetupVar.FRAME_TITLE] = title(model.frame.kind) if model.frame is not None else NO_FRAME
            self.state[SetupVar.CHECK] = check
            self.state[SetupVar.CHECK_FAILED] = failed
            self.state[SetupVar.IS_2D] = model.frame is not None and model.frame.model_dim == 2
            self.state[SetupVar.HAS_GHOST] = model.has_ghost
            self.state[SetupVar.HAS_CONDITIONS] = model.conditions is not None
            self.state[SetupVar.BOUNDARY_COLUMNS] = boundary_columns(model.frame)
            self.state[SetupVar.HAS_SCENE] = view.drawn
            self.state[SetupVar.BODY_ROWS] = bodies.rows
            self.state[SetupVar.BOUNDARY_ROWS] = boundaries.rows
            self.state[SetupVar.TAG_ROWS] = tags.rows
            self.state[SetupVar.BODY_COUNT] = count_text(sum(not row.get("group") for row in bodies.rows), bodies.total)
            self.state[SetupVar.BOUNDARY_COUNT] = count_text(len(boundaries.rows), boundaries.total)
            self.state[SetupVar.TAG_COUNT] = count_text(len(tags.rows), tags.total)
            self.state[SetupVar.MORE] = f"{boundaries.unlisted} More Without Conditions{SEPARATOR}All" if boundaries.unlisted and model.conditions is not None else ""
            self.state[SetupVar.LEGEND_ROWS] = legend_rows(model)
            self.state[SetupVar.NOTICE] = notice
            self.state[SetupVar.NOTICE_LEVEL] = str(level)
            self.state[SetupVar.CAPTION] = caption_of(model)
            self.state[SetupVar.MISS] = miss_text(view.query, shown)

        self.publish_frame()

    def publish_frame(self) -> None:
        """Puts the Frame section's rows in the state, the revolved sweep the Field Viewer shows included."""
        model = self.model
        if model is None:
            return

        sweep = cast("list[object]", self.state[FieldVar.SWEEP])
        degrees = sweep[0] if isinstance(sweep, list) and sweep and isinstance(sweep[0], int | float) else 360.0
        facts = frame_facts(model, Expansion(str(self.state[FieldVar.EXPANSION])), float(cast("float", degrees)))
        with self.state:
            self.state[SetupVar.FRAME_FACTS] = facts

    def name_of(self, entity: str) -> str:
        """The name a published row reads for an entity id; the id itself when no row has it."""
        return next((row.get("name", entity) for row in self.rows if row.get("id") == entity), entity)

    def select(self, entity: str) -> None:
        """Highlights an entity in the scene, or clears the highlight for an empty id, and reads the change aloud."""
        row = next((row for row in self.rows if row.get("id") == entity), None)
        if row is not None and row.get("dimmed"):
            with self.state:
                self.state[SetupVar.ANNOUNCE] = f"{row.get('name', entity)} has no cells to highlight"

            return

        chosen = entity if EntityRef.parse(entity) is not None else ""
        with self.state:
            self.state[FieldVar.SELECTED_ENTITY] = chosen
            self.state[SetupVar.ANNOUNCE] = f"Highlighted {self.name_of(chosen)}" if chosen else "Selection cleared"

    def on_select(self, entity: object) -> None:
        """A row clicked or chosen: selects it, and keeps it selected when it already was, so the clicks of a double-click never clear it; Escape clears the search first, then the selection."""
        chosen = str(entity or "")
        if not chosen and str(self.state[SetupVar.QUERY] or ""):
            self.clear_query()
            return

        self.select(chosen)

    def on_toggle_glyph(self, entity: object) -> None:
        """A row's glyph toggle: hides or shows that group's glyphs."""
        chosen = str(entity)
        off = names_of(self.state[FieldVar.GLYPH_ROWS_OFF])
        with self.state:
            self.state[FieldVar.GLYPH_ROWS_OFF] = [other for other in off if other != chosen] if chosen in off else [*off, chosen]

    def on_frame(self, entity: object) -> None:
        """A row double-clicked or framed with F: fits the camera to it."""
        ref = EntityRef.parse(str(entity))
        if ref is None:
            return

        self.controller.frame_entity(ref)

    def on_show(self, entity: object) -> None:
        """A hidden body's Show: shows it in the Field Viewer again."""
        ref = EntityRef.parse(str(entity))
        if ref is None:
            return

        visible = set(names_of(self.state[FieldVar.VISIBLE_BODIES])) | {ref.name}
        with self.state:
            self.state[FieldVar.VISIBLE_BODIES] = [body for body in self.controller.bodies if body in visible]

    def on_kind(self, kind: object, on: object) -> None:
        """A legend switch: shows or hides every glyph of one kind."""
        chosen = str(kind)
        off = [other for other in names_of(self.state[FieldVar.GLYPH_KINDS_OFF]) if other != chosen]
        with self.state:
            self.state[FieldVar.GLYPH_KINDS_OFF] = off if on is True else [*off, chosen]

    def clear_query(self) -> None:
        """Empties the search, remounting the field so it shows the empty text."""
        with self.state:
            self.state[SetupVar.QUERY] = ""
            self.state[SetupVar.SEARCH_KEY] = int(cast("int", self.state[SetupVar.SEARCH_KEY] or 0)) + 1

    def show_all(self) -> None:
        """The More row: lists every boundary group."""
        with self.state:
            self.state[SetupVar.FILTER] = str(FilterChoice.ALL)

    @change(SetupVar.QUERY, SetupVar.FILTER, FieldVar.VISIBLE_BODIES, FieldVar.GLYPH_ROWS_OFF, FieldVar.GLYPHS, FieldVar.GLYPH_KINDS_OFF)
    def on_rows(self, **_: object) -> None:
        """Republishes the tables after the search, the filter, the visible bodies, a row toggle, Show Glyphs or a legend switch changed."""
        if self.model is not None:
            self.publish()

    @change(FieldVar.WARP_SCALE)
    def on_warp_scale(self, **_: object) -> None:
        """Republishes the ghost's warp note after the Field Viewer's deformation scale changed."""
        scale = only(self.state[FieldVar.WARP_SCALE], 1.0)
        with self.state:
            self.state[SetupVar.WARP_CAPTION] = f"{GHOST_WARPED}{TIMES}{scale_text(scale)}"

    @change(FieldVar.EXPANSION, FieldVar.SWEEP)
    def on_expansion(self, **_: object) -> None:
        """Republishes the Frame section's Shown As row after the Field Viewer's expansion changed."""
        self.publish_frame()

    def on_visible(self, visible: bool) -> None:
        """Tells the scene whether the tab is on screen, the only time it draws glyphs."""
        with self.state:
            self.state[FieldVar.SETUP_SHOWN] = visible

    def dispose(self) -> None:
        """Hides the glyphs and detaches from the controller, then releases the panel."""
        with self.state:
            self.state[FieldVar.SETUP_SHOWN] = False

        self.controller.detach(self)
        super().dispose()

    def build_search(self) -> None:
        """The search field with its hidden label and clear button; Escape inside it clears it."""
        query = json.dumps(str(SetupVar.QUERY))
        escape = react.Callback(f"{EVENT}.key === 'Escape' && {EVENT}.target.value && ({EVENT}.stopPropagation(), {EVENT}.target.value = '', trame.state.set({query}, ''))")
        with html.Div(classes=SEARCH_ROW):
            FieldLabel("Search Bodies and Boundaries", html_for=SEARCH_ID, classes=SR_ONLY)
            Input(
                default_value=react.Bind(str(SetupVar.QUERY)),
                placeholder="Search Bodies and Boundaries",
                aria_label="Search bodies and boundaries",
                name=str(SetupVar.QUERY),
                id=SEARCH_ID,
                key=react.Bind(str(SetupVar.SEARCH_KEY)),
                classes="h-7",
                on_input=setter(str(SetupVar.QUERY), COMMIT_VALUE),
                on_key_down=escape,
            )
            with react.If(str(SetupVar.QUERY)), Button(size="icon-xs", variant="ghost", aria_label="Clear search", on_click=self.callback(self.clear_query)):
                Icon("x")

    def build_notices(self) -> None:
        """The notice, the caption and the no-match line over the tables."""
        with react.If(str(SetupVar.NOTICE)), html.Div(classes=NOTICE_ROW):
            with react.If(f"{SetupVar.NOTICE_LEVEL} === {json.dumps(str(NoticeLevel.WARNING))}"):
                Icon("triangle-alert", size=14, classes="mt-0.5 shrink-0")

            with react.If(f"{SetupVar.NOTICE_LEVEL} !== {json.dumps(str(NoticeLevel.WARNING))}"):
                Icon("info", size=14, classes="mt-0.5 shrink-0")

            html.Span([react.Bind(str(SetupVar.NOTICE))])

        with react.If(str(SetupVar.CAPTION)):
            html.Span([react.Bind(str(SetupVar.CAPTION))], classes=f"{Cls.CAPTION} px-2")

        with react.If(str(SetupVar.MISS)), html.Div(classes=NOTICE_ROW):
            html.Span([react.Bind(str(SetupVar.MISS))])
            Button("Clear Search", size="xs", variant="link", on_click=self.callback(self.clear_query))

    def build_frame(self) -> None:
        """The Frame section: the frame's title, its rows, the geometry check and, for a 2D frame, the outline and ghost switches."""
        html.Span([react.Bind(str(SetupVar.FRAME_TITLE))], classes="font-medium")
        with html.Div(classes=f"{Cls.PAIRS} px-0"):
            with react.For(items=str(SetupVar.FRAME_FACTS), name="fact"):
                html.Span([react.Bind("fact.label")], classes=str(Cls.CAPTION))
                html.Span([react.Bind("fact.value")], classes="tabular-nums whitespace-nowrap")

            with react.If(f"{SetupVar.CHECK}.length"):
                html.Span("Geometry Check", classes=str(Cls.CAPTION))
                with html.Span(classes="flex flex-col gap-0.5"):
                    with react.For(items=str(SetupVar.CHECK), name="line"), html.Span(classes="flex items-start gap-1"):
                        with react.If(str(SetupVar.CHECK_FAILED)):
                            Icon("triangle-alert", size=14, classes="mt-0.5 shrink-0")

                        html.Span([react.Bind("line")])

        with react.If(f"{SetupVar.IS_2D} && {SetupVar.HAS_SCENE}"), html.Div(classes=STACK):
            setting(FieldVar.SECTION_OUTLINE, "Section Outline")
            setting(FieldVar.AUTHORED_GHOST, "Authored Body Ghost", disabled=react.Bind(f"!{SetupVar.HAS_GHOST}"))
            with react.If(f"!{SetupVar.HAS_GHOST}"):
                html.Span(NO_GHOST, classes=str(Cls.CAPTION))

            with Reveal(open_=react.Bind(f"{FieldVar.AUTHORED_GHOST} && {SetupVar.HAS_GHOST} && {FieldVar.HAS_DISPLACEMENT} && {FieldVar.WARP}")):
                html.Span([react.Bind(str(SetupVar.WARP_CAPTION))], classes=str(Cls.CAPTION))

    def grid(self, rows: SetupVar, label: str, columns: Sequence[str] | react.Bind) -> None:
        """One table of rows under its column headers, wired to the selection, the glyph toggles, the framing and the Show of a hidden body.

        Args:
            rows: The state variable holding the rows.
            label: The grid's accessible name.
            columns: The column headers, or a binding to them.
        """
        EntityGrid(
            rows=react.Bind(str(rows)),
            selected=react.Bind(str(FieldVar.SELECTED_ENTITY)),
            label=label,
            columns=columns,
            on_select=self.callback(self.on_select, args=f"[{EVENT}]"),
            on_hover=react.Callback(f"trame.state.set({json.dumps(str(FieldVar.HOVERED))}, {EVENT})"),
            on_toggle_glyph=self.callback(self.on_toggle_glyph, args=f"[{EVENT}]"),
            on_frame=self.callback(self.on_frame, args=f"[{EVENT}]"),
            on_show=self.callback(self.on_show, args=f"[{EVENT}]"),
        )

    def build_boundaries(self) -> None:
        """The Boundaries section: the With Conditions | All filter, the table and the More row."""
        keep = react.Callback(f"{EVENT} && trame.state.set({json.dumps(str(SetupVar.FILTER))}, {EVENT})")
        with (
            react.If(str(SetupVar.HAS_CONDITIONS)),
            ToggleGroup(type_="single", variant="outline", size="sm", value=react.Bind(str(SetupVar.FILTER)), on_value_change=keep, classes=FILTER_GROUP, aria_label="Boundaries listed"),
        ):
            for choice in FilterChoice:
                ToggleGroupItem(title(choice.value), value=str(choice), classes=str(Cls.SEGMENT))

        self.grid(SetupVar.BOUNDARY_ROWS, "Boundaries", react.Bind(str(SetupVar.BOUNDARY_COLUMNS)))
        with react.If(str(SetupVar.MORE)):
            Button([react.Bind(str(SetupVar.MORE))], size="xs", variant="link", classes="self-start px-0", on_click=self.callback(self.show_all))

    def build_legend(self) -> None:
        """The Glyphs section: one row per glyph kind the run draws, each with its icon, count and switch."""
        rows = str(SetupVar.LEGEND_ROWS)
        off = str(FieldVar.GLYPH_KINDS_OFF)
        for glyph in ConditionGlyph:
            kind = json.dumps(str(glyph))
            with react.If(f"{rows}.some(row => row.kind === {kind})"), html.Div(classes=LEGEND_ROW):
                Icon(icon_of(glyph), size=14)
                FieldLabel(legend_label(glyph), html_for=f"ff-setup-kind-{glyph}", classes=f"{Cls.CAPTION} truncate")
                html.Span([react.Bind(f"{rows}.find(row => row.kind === {kind}).count")], classes="text-right text-muted-foreground tabular-nums")
                Switch(
                    checked=react.Bind(f"!{off}.includes({kind})"),
                    on_checked_change=self.optimistic(self.on_kind, off, f"{EVENT} === true ? {off}.filter(k => k !== {kind}) : [...{off}, {kind}]", f"[{kind}, {EVENT}]"),
                    id=f"ff-setup-kind-{glyph}",
                    disabled=react.Bind(f"!{SetupVar.HAS_SCENE}"),
                )

        authored = f"{SetupVar.STATE} === {json.dumps(str(SetupState.AUTHORED))}"
        with react.If(f"!{rows}.length && !({authored})"):
            html.Span(NO_GLYPHS, classes=str(Cls.CAPTION))

        with react.If(authored):
            html.Span(NO_GLYPHS_AUTHORED, classes=str(Cls.CAPTION))

        with react.If(f"{rows}.some(row => row.kind === {json.dumps(str(ConditionGlyph.SURFACE_FORCE))})"):
            html.Span(ARROW_NOTE, classes=str(Cls.CAPTION))

    def build(self) -> None:
        """Builds the busy bar, the empty state, the search, the switches, the notices, the four sections and the live region."""

        def section(value: str, heading: str, body: Callable[[], None], count: SetupVar | None = None) -> None:
            with AccordionItem(value=value, classes=str(Cls.SECTION_ITEM)):
                with AccordionTrigger(classes=str(Cls.SECTION_TRIGGER)), html.Span(classes=HEADER):
                    html.Span(heading)
                    if count is not None:
                        html.Span([react.Bind(str(count))], classes=COUNT)

                with AccordionContent(classes=str(Cls.SECTION_BODY)):
                    body()

        state = str(SetupVar.STATE)
        slash = f"{EVENT}.key === '/' && !['INPUT', 'TEXTAREA'].includes({EVENT}.target.tagName) && document.getElementById({json.dumps(SEARCH_ID)})"
        focus = react.Callback(f"{slash} && ({EVENT}.preventDefault(), document.getElementById({json.dumps(SEARCH_ID)}).focus())")
        with html.Div(classes=str(Cls.CONTROLS), on_key_down=focus):
            Busy(active=react.Bind(str(FieldVar.BUSY)))

            with react.If(f"{state} === {json.dumps(str(SetupState.NONE))}"), html.Div(classes=str(Cls.EMPTY)), html.Div(classes=EMPTY_STACK):
                html.Span([react.Bind(str(SetupVar.EMPTY))])
                with react.If(str(SetupVar.EMPTY_DETAIL)):
                    html.Span([react.Bind(str(SetupVar.EMPTY_DETAIL))], classes=EMPTY_DETAIL)

            with react.If(f"{state} !== {json.dumps(str(SetupState.NONE))}"), html.Div(classes="flex flex-col gap-2"):
                self.build_search()
                with react.If(str(SetupVar.HAS_SCENE)), html.Div(classes=SETTINGS):
                    setting(FieldVar.GLYPHS, "Show Glyphs")
                    setting(FieldVar.PLAIN_BODIES, "Plain Bodies")

                self.build_notices()
                with Accordion(type_="multiple", default_value=OPEN_SECTIONS, classes=str(Cls.SECTIONS)):
                    section(FRAME_SECTION, "Frame", self.build_frame)
                    with react.If(f"{state} === {json.dumps(str(SetupState.AUTHORED))}"):
                        section(TAGS_SECTION, "Authored Tags", partial(self.grid, SetupVar.TAG_ROWS, "Authored tags", [TAG_COLUMN, "", GLYPH_COLUMN]), SetupVar.TAG_COUNT)

                    with react.If(f"{state} === {json.dumps(str(SetupState.GROUPS))}"):
                        section(BODIES_SECTION, "Bodies", partial(self.grid, SetupVar.BODY_ROWS, "Bodies", [NAME_COLUMN, MATERIAL_COLUMN, GLYPH_COLUMN]), SetupVar.BODY_COUNT)
                        section(BOUNDARIES_SECTION, "Boundaries", self.build_boundaries, SetupVar.BOUNDARY_COUNT)

                    section(GLYPHS_SECTION, "Glyphs", self.build_legend)

            html.Span([react.Bind(str(SetupVar.ANNOUNCE))], classes=SR_ONLY, role="status", aria_live="polite", __properties=[("aria_live", "aria-live"), "role"])
