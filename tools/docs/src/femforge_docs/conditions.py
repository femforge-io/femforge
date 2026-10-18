"""Markdown tables for the conditions guide, generated from the shipped conditions, physics and frames."""

from __future__ import annotations

import inspect
import re
from typing import NamedTuple, cast

from pydantic.fields import FieldInfo

from femforge.common.introspect import FIELD_DOCSTRING, attribute_docstrings
from femforge.elmer.config import physics_config
from femforge.elmer.config.common.selection import SELECTABLE_PHYSICS
from femforge.elmer.physics import Physics, condition_physics
from femforge.elmer.sections import SifSection
from femforge.frames import Frame, shipped_frames
from femforge.physical.conditions import Condition, ConditionTarget, ElmerKeyword, shipped_conditions
from femforge.physical.units import Quantity


PHYSICS: tuple[Physics, ...] = tuple(Physics(value) for value in SELECTABLE_PHYSICS)
"""The selectable physics, one guide page each, in selection order."""

BASE_FIELDS: frozenset[str] = frozenset(Condition.model_fields) | {"kind"}
"""The fields every condition carries, left out of its field table."""

RST_LITERAL = re.compile(r"``([^`]+)``")
"""An inline literal in docstring markup, rewritten to a Markdown code span."""


class FieldDoc(NamedTuple):
    """A field docstring split into its symbol, unit and meaning."""

    symbol: str | None
    """The glyph, or None when the docstring names none."""

    unit: str | None
    """The unit in brackets, or None for a plain docstring."""

    meaning: str
    """The rest of the docstring on one line."""


class Reading(NamedTuple):
    """A condition one physics reads and the frames that read it."""

    condition: type[Condition]
    """The condition class."""

    frames: tuple[Frame, ...]
    """The shipped frames whose solve of the physics reads the condition."""


def _markdown(text: str) -> str:
    return RST_LITERAL.sub(r"`\1`", " ".join(text.split()))


def _summary(doc: str | None) -> str:
    return _markdown(inspect.cleandoc(doc or "").split("\n\n")[0])


def _frames() -> tuple[Frame, ...]:
    # Constructed unvalidated: a frame with a required field, such as the Cartesian 2D thickness, still answers solves().
    return tuple(kind.model_construct() for kind in shipped_frames())


def _default(condition: type[Condition]) -> Condition:
    return condition.model_construct()


def _target(condition: type[Condition]) -> str:
    if _default(condition).target is ConditionTarget.BODY:
        return "body"

    return "outer shell" if condition.external_only else "boundary"


def _code(text: str) -> str:
    return f"`{text}`"


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    def _row(*cells: str) -> str:
        return f"| {' | '.join(cells)} |"

    rule: tuple[str, ...] = tuple("---" for _ in header)
    lines: list[str] = [_row(*header), _row(*rule), *(_row(*row) for row in rows)]

    return "\n".join(lines)


def field_doc(text: str) -> FieldDoc:
    """The symbol, unit and meaning of a field docstring; a docstring without a unit is all meaning."""
    match: re.Match[str] | None = FIELD_DOCSTRING.match(text)

    if match is None:
        return FieldDoc(None, None, _markdown(text))

    return FieldDoc(match["symbol"], match["unit"], _markdown(match["meaning"]))


def title(physics: Physics) -> str:
    """The guide page title of ``physics``, such as ``Current conduction``."""
    return physics.value.replace("_", " ").capitalize()


def page(physics: Physics) -> str:
    """The guide page slug of ``physics``, such as ``current-conduction``."""
    return physics.value.replace("_", "-")


def block_heading(section: SifSection) -> str:
    """The heading a physics page files the conditions of ``section`` under."""
    match section:
        case SifSection.MATERIAL:
            return "Material-wired conditions"
        case SifSection.BOUNDARY_CONDITION:
            return "Boundary conditions"
        case SifSection.BODY_FORCE:
            return "Body forces"
        case SifSection.COMPONENT:
            return "Components"
        case SifSection.EQUATION | SifSection.SOLVER | SifSection.BODY:
            raise ValueError(f"No condition lands in the {section.value} section.")


def home(condition: type[Condition]) -> Physics:
    """The first selectable physics that some shipped frame solves as the default physics of ``condition``."""
    feeds: Physics = _default(condition).physics

    for physics in PHYSICS:
        for frame in _frames():
            if frame.supports(physics) and frame.solves(physics) is feeds:
                return physics

    raise ValueError(f"No selectable physics solves as {feeds.value}, the physics {condition.__name__} feeds.")


def anchor(condition: type[Condition]) -> str:
    """The link to the section of ``condition`` on its home page, relative to the conditions folder."""
    return f"{page(home(condition))}.md#{condition.__name__.lower()}"


def _link(condition: type[Condition]) -> str:
    return f"[`{condition.__name__}`]({anchor(condition)})"


def _page_link(physics: Physics) -> str:
    return f"[{title(physics)}]({page(physics)}.md)"


def _frame_link(frame: Frame) -> str:
    return f"[{type(frame).__name__}](../frames/{frame.kind.replace('_', '-')}.md)"


def read_by(physics: Physics) -> list[Reading]:
    """The conditions a solve of ``physics`` reads under some shipped frame, in ``shipped_conditions()`` order."""
    readings: list[Reading] = []

    for condition in shipped_conditions():
        feeds: Physics = _default(condition).physics
        frames: tuple[Frame, ...] = tuple(frame for frame in _frames() if frame.supports(physics) and feeds in condition_physics(frame.solves(physics)))

        if frames:
            readings.append(Reading(condition, frames))

    return readings


def blocks(physics: Physics) -> list[SifSection]:
    """The sif sections that hold at least one condition ``physics`` reads, in ``SifSection`` order."""
    sections: set[SifSection] = {reading.condition.section for reading in read_by(physics)}

    return [section for section in SifSection if section in sections]


def _fields(condition: type[Condition]) -> list[tuple[str, FieldInfo]]:
    return [(name, field) for name, field in condition.model_fields.items() if name not in BASE_FIELDS]


def solves_table(physics: Physics) -> str:
    """One row per shipped frame: what ``physics`` solves as there and the preset solver stack of that form."""
    rows: list[tuple[str, ...]] = []

    for frame in _frames():
        if not frame.supports(physics):
            rows.append((_frame_link(frame), "no form", ""))
            continue

        form: Physics = frame.solves(physics)
        solvers: str = ", ".join(type(solver).__name__ for solver in physics_config(form).solvers)
        rows.append((_frame_link(frame), _code(form.name), solvers))

    return _table(("Frame", "Solves as", "Solver stack"), rows)


def block_table(physics: Physics, section: SifSection) -> str:
    """The conditions ``physics`` reads in ``section``: their target, the fields they set and the frames that read them."""

    def _sets(condition: type[Condition]) -> str:
        parts: list[str] = []

        for name, field in _fields(condition):
            unit: str | None = field_doc(field.description or "").unit
            parts.append(_code(name) if unit is None else f"{_code(name)} [{unit}]")

        return ", ".join(parts) or "no field"

    readings: list[Reading] = [reading for reading in read_by(physics) if reading.condition.section is section]

    if not readings:
        return f"No condition `{physics.name}` reads lands in the {section.value} section."

    rows: list[tuple[str, ...]] = []

    for condition, frames in readings:
        frame_links: str = ", ".join(_frame_link(frame) for frame in frames)
        rows.append((_link(condition), _target(condition), _sets(condition), frame_links))

    return _table(("Condition", "Target", "Sets", "Frames"), rows)


def condition_details(condition: type[Condition]) -> str:
    """The facts line, summary, field table and emitted keywords of ``condition``."""

    def _keyword(field: FieldInfo) -> ElmerKeyword | None:
        metadata: list[object] = cast("list[object]", field.metadata)

        return next((meta for meta in metadata if isinstance(meta, ElmerKeyword)), None)

    def _default_text(field: FieldInfo) -> str:
        if field.is_required():
            return "required"

        value: object = cast("object", field.get_default(call_default_factory=True))

        if isinstance(value, Quantity):
            return _code(f"{value:~P}")

        return _code(repr(value) if isinstance(value, str) else str(value))

    def _writes() -> str | None:
        if condition.on_boundary is not Condition.on_boundary:
            return _summary(condition.on_boundary.__doc__)

        if condition.to_elmer is not Condition.to_elmer:
            return _summary(condition.to_elmer.__doc__)

        return None

    default: Condition = _default(condition)
    name: str = condition.__name__
    facts: list[str] = [
        f"**Physics** `{default.physics.name}`",
        f"**Target** {_target(condition)}",
        f"**Section** {condition.section.value}",
        f"**Kind** `{default.model_dump()['kind']}`",
        f"**API** [{name}][femforge.physical.{name}]",
    ]
    parts: list[str] = [" · ".join(facts), _summary(condition.__doc__)]
    rows: list[tuple[str, ...]] = []

    for field_name, field in _fields(condition):
        doc: FieldDoc = field_doc(field.description or "")
        keyword: ElmerKeyword | None = _keyword(field)
        symbol: str = "" if doc.symbol is None else _code(doc.symbol)
        sif: str = "" if keyword is None else _code(keyword.key)
        rows.append((_code(field_name), symbol, doc.unit or "", doc.meaning, sif, _default_text(field)))

    parts.append(_table(("Field", "Symbol", "Unit", "Meaning", "Sif keyword", "Default"), rows) if rows else "It takes no fields.")
    writes: str | None = _writes()

    if writes is not None:
        parts.append(f"Writes: {writes}")

    return "\n\n".join(parts)


def finder_table() -> str:
    """Selectable physics by sif section, each cell linking the conditions that physics reads in that section."""
    used: set[SifSection] = {section for physics in PHYSICS for section in blocks(physics)}
    sections: list[SifSection] = [section for section in SifSection if section in used]
    rows: list[tuple[str, ...]] = []

    for physics in PHYSICS:
        readings: list[Reading] = read_by(physics)
        cells: list[str] = []

        for section in sections:
            links: list[str] = [_link(reading.condition) for reading in readings if reading.condition.section is section]
            cells.append(", ".join(links))

        rows.append((_page_link(physics), *cells))

    return _table(("Physics", *(section.value for section in sections)), rows)


def physics_table() -> str:
    """Every physics member, what it solves and its preset solver stack."""
    docs: dict[str, str] = attribute_docstrings(Physics)
    rows: list[tuple[str, ...]] = []

    for physics in Physics:
        member: str = f"[`{physics.name}`]({page(physics)}.md)" if physics in PHYSICS else _code(physics.name)
        solvers: str = ", ".join(type(solver).__name__ for solver in physics_config(physics).solvers)
        rows.append((member, _markdown(docs[physics.name]), solvers))

    return _table(("Physics", "Solves", "Preset solver stack"), rows)


def shipped_table() -> str:
    """Every shipped condition with its home page, section, target and the physics that read it."""
    rows: list[tuple[str, ...]] = []

    for condition in shipped_conditions():
        readers: list[str] = [_page_link(physics) for physics in PHYSICS if any(reading.condition is condition for reading in read_by(physics))]
        rows.append((_link(condition), _page_link(home(condition)), condition.section.value, _target(condition), ", ".join(readers)))

    return _table(("Condition", "Home physics", "Section", "Target", "Read by"), rows)


def reads_table() -> str:
    """The solved physics that also read the conditions of other physics."""
    rows: list[tuple[str, ...]] = []

    for physics in Physics:
        reads: frozenset[Physics] = condition_physics(physics)

        if reads == {physics}:
            continue

        others: list[str] = [_code(member.name) for member in Physics if member in reads and member is not physics]
        names: str = ", ".join([_code(physics.name), *others])
        rows.append((_code(physics.name), names))

    rows.append(("Any other", "Its own"))

    return _table(("Solved physics", "Reads the conditions of"), rows)
