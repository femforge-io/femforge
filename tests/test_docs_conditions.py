"""The conditions guide has one page per selectable physics, one home heading per shipped condition, and generated tables that render."""

import contextlib
import io
import re
from pathlib import Path
from typing import NamedTuple

import pytest

from femforge.elmer.physics import Physics
from femforge.elmer.sections import SifSection
from femforge.physical.conditions import Condition, CoilTerminal, FixedTemperature, Magnetization, shipped_conditions
from femforge_docs.conditions import (
    PHYSICS,
    FieldDoc,
    anchor,
    block_heading,
    block_table,
    blocks,
    condition_details,
    field_doc,
    finder_table,
    home,
    page,
    physics_table,
    read_by,
    reads_table,
    shipped_table,
    solves_table,
)


ROOT = Path(__file__).parents[1]
"""The repository root."""

GUIDE = ROOT / "docs" / "user-guide" / "conditions"
"""The conditions guide folder."""

NAV_PAGE = re.compile(r"^\s+- (?:[^:\n]+: )?user-guide/conditions/(?P<page>[\w-]+)\.md$", re.MULTILINE)
"""A nav entry of the conditions section in ``mkdocs.yml``, which ``!!python/name`` tags keep out of ``yaml.safe_load``."""

EXEC_FENCE = re.compile(r'^```python exec="true" session="(?P<session>[^"]+)"\n(?P<code>.*?)^```$', re.MULTILINE | re.DOTALL)
"""A markdown-exec fence and the session it runs in."""

SECTIONS: list[SifSection] = [section for section in SifSection if any(condition.section is section for condition in shipped_conditions())]
"""The sif sections some shipped condition lands in."""

BLOCK_HEADINGS: set[str] = {block_heading(section) for section in SECTIONS}
"""The headings a physics page files conditions under."""

CONDITION_IDS: list[str] = [condition.__name__ for condition in shipped_conditions()]
"""The test ids of the shipped conditions."""

PAGES: list[Path] = sorted(GUIDE.glob("*.md"))
"""The guide pages."""


class Heading(NamedTuple):
    """A Markdown heading outside the code fences and the code of the fence after it."""

    level: int
    """The number of ``#`` marks."""

    text: str
    """The heading text."""

    parent: str | None
    """The nearest ``##`` heading above a ``###`` one."""

    next_fence: str
    """The code of the first fence after the heading, or an empty string."""


def _headings(text: str) -> list[Heading]:
    lines = text.splitlines()
    headings: list[Heading] = []
    fenced = False
    parent: str | None = None

    for index, line in enumerate(lines):
        if line.startswith("```"):
            fenced = not fenced
            continue

        match = re.match(r"^(#{2,3}) (.+)$", line)

        if fenced or match is None:
            continue

        level, title = len(match[1]), match[2]
        fence = EXEC_FENCE.search("\n".join(lines[index:]) + "\n")
        next_fence = "" if fence is None else fence["code"]

        if level == 2:
            parent = title

        headings.append(Heading(level, title, parent if level == 3 else None, next_fence))

    return headings


def _page_text(physics: Physics) -> str:
    return (GUIDE / f"{page(physics)}.md").read_text(encoding="utf-8")


def _assert_tables(text: str) -> None:
    rows = [line for line in text.splitlines() if line.startswith("|")]
    widths = {line.count("|") for line in rows}

    assert len(widths) <= 1, f"ragged table rows:\n{text}"


def test_the_nav_lists_the_index_then_one_page_per_selectable_physics() -> None:
    nav = [match["page"] for match in NAV_PAGE.finditer((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))]
    files = sorted(path.stem for path in GUIDE.glob("*.md"))

    assert nav == ["index", *(page(physics) for physics in PHYSICS)]
    assert files == sorted(nav)


def test_the_llms_index_covers_the_guide_folders() -> None:
    text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")

    assert "User guide: [user-guide/*.md, user-guide/conditions/*.md, user-guide/frames/*.md]" in text


def test_each_shipped_condition_has_one_home_heading_under_its_section() -> None:
    homes: dict[str, list[str]] = {}

    for physics in PHYSICS:
        for heading in _headings(_page_text(physics)):
            if heading.level == 3:
                homes.setdefault(heading.text, []).append(page(physics))

    assert sorted(homes) == sorted(condition.__name__ for condition in shipped_conditions())

    for condition in shipped_conditions():
        assert homes[condition.__name__] == [page(home(condition))]


@pytest.mark.parametrize("condition", shipped_conditions(), ids=CONDITION_IDS)
def test_a_home_heading_renders_its_own_condition(condition: type[Condition]) -> None:
    headings = _headings(_page_text(home(condition)))
    heading = next(heading for heading in headings if heading.level == 3 and heading.text == condition.__name__)

    assert heading.parent == block_heading(condition.section)
    assert heading.next_fence.strip() == f"print(condition_details({condition.__name__}))"
    assert anchor(condition) == f"{page(home(condition))}.md#{condition.__name__.lower()}"


@pytest.mark.parametrize("physics", PHYSICS, ids=page)
def test_the_block_headings_follow_the_sections_the_physics_reads(physics: Physics) -> None:
    headings = [heading for heading in _headings(_page_text(physics)) if heading.level == 2 and heading.text in BLOCK_HEADINGS]
    sections = blocks(physics)

    assert [heading.text for heading in headings] == [block_heading(section) for section in sections]

    for heading, section in zip(headings, sections, strict=True):
        assert heading.next_fence.strip() == f"print(block_table(Physics.{physics.name}, SifSection.{section.name}))"


@pytest.mark.parametrize("path", PAGES, ids=[path.stem for path in PAGES])
def test_every_exec_fence_runs(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    sessions: dict[str, dict[str, object]] = {}
    fences = list(EXEC_FENCE.finditer(text))

    assert fences

    for fence in fences:
        session = fence["session"]
        output = io.StringIO()

        assert session == ("conditions" if path.stem == "index" else path.stem)

        with contextlib.redirect_stdout(output):
            exec(fence["code"], sessions.setdefault(session, {}))  # noqa: S102 - runs the page's own fences as markdown-exec does

        assert output.getvalue().strip(), f"{path.name}: a fence printed nothing:\n{fence['code']}"
        _assert_tables(output.getvalue())


@pytest.mark.parametrize("physics", PHYSICS, ids=page)
def test_the_page_helpers_render_for_every_physics_and_section(physics: Physics) -> None:
    solves = solves_table(physics)

    assert solves.startswith("| Frame | Solves as | Solver stack |")
    _assert_tables(solves)

    for section in SifSection:
        rendered = block_table(physics, section)

        assert rendered
        _assert_tables(rendered)


@pytest.mark.parametrize("condition", shipped_conditions(), ids=CONDITION_IDS)
def test_the_condition_details_render(condition: type[Condition]) -> None:
    details = condition_details(condition)

    assert details.startswith("**Physics**")
    assert f"[{condition.__name__}][femforge.physical.{condition.__name__}]" in details
    _assert_tables(details)


def test_the_index_tables_render() -> None:
    for table in (finder_table(), physics_table(), shipped_table(), reads_table()):
        assert table.startswith("|")
        _assert_tables(table)

    assert len(shipped_table().splitlines()) == 2 + len(shipped_conditions())
    assert reads_table().splitlines()[-1] == "| Any other | Its own |"


def test_electrostatics_reads_no_condition_and_has_no_axisymmetric_form() -> None:
    assert read_by(Physics.ELECTROSTATICS) == []
    assert blocks(Physics.ELECTROSTATICS) == []
    assert "| no form |" in solves_table(Physics.ELECTROSTATICS)
    assert block_table(Physics.ELECTROSTATICS, SifSection.BODY_FORCE) == "No condition `ELECTROSTATICS` reads lands in the Body Force section."


def test_joule_heating_reads_its_constituents_conditions() -> None:
    read = [reading.condition.__name__ for reading in read_by(Physics.JOULE_HEATING)]

    assert read == ["FixedTemperature", "HeatFlux", "Convection", "HeatSource", "FixedVoltage", "JouleHeat"]
    assert blocks(Physics.JOULE_HEATING) == [SifSection.BOUNDARY_CONDITION, SifSection.BODY_FORCE]


@pytest.mark.parametrize("section", [SifSection.EQUATION, SifSection.SOLVER, SifSection.BODY])
def test_a_section_no_condition_lands_in_has_no_heading(section: SifSection) -> None:
    with pytest.raises(ValueError, match="No condition lands"):
        _ = block_heading(section)


def test_field_doc_splits_a_physical_docstring() -> None:
    doc = FixedTemperature.model_fields["value"].description or ""

    assert field_doc(doc) == FieldDoc("T", "K", "Fixed temperature imposed on the boundary.")
    assert field_doc("`[-]`: A ``knob``\n  over two lines.") == FieldDoc(None, "-", "A `knob` over two lines.")


@pytest.mark.parametrize(("condition", "name"), [(Magnetization, "direction"), (CoilTerminal, "role")])
def test_field_doc_keeps_a_plain_docstring_as_its_meaning(condition: type[Condition], name: str) -> None:
    doc = condition.model_fields[name].description or ""

    assert field_doc(doc) == FieldDoc(None, None, doc)
