"""Every backticked repository path on a hand-written docs page names a file or directory that exists."""

import re
from pathlib import Path
from typing import NamedTuple

import pytest


ROOT = Path(__file__).parents[1]
"""The repository root that cited paths resolve against."""

DOCS = ROOT / "docs"
"""The docs source tree."""

SKIPPED = ("reference",)
"""Top-level docs directories outside the check: the generated API reference."""

OPENING = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})")
"""An opening code fence and its fence run."""

SPAN = re.compile(r"(?<!`)`(?P<text>[^`\n]+)`(?!`)")
"""A single-backtick inline code span."""

CITATION = re.compile(
    r"(?P<path>(?:src|packages|tests|scripts|typings|docs|examples|\.github)/[^\s:`]*)(?::\d+(?:-\d+)?|::(?P<test>\w+))?",
)
"""A repository path under a checked root, with an optional line, line range or ``::`` test name."""

PENDING: frozenset[str] = frozenset()
"""Paths a page cites before they land; each leaves this set once it exists."""

GENERATED = frozenset({"docs/schema"})
"""Build outputs a page may cite; the docs build writes them, so a fresh checkout lacks them."""

IGNORED = frozenset(line.strip().rstrip("/") for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
"""Every pattern the root .gitignore lists, without a trailing slash."""


class Citation(NamedTuple):
    """One cited repository path and the page that cites it."""

    page: str
    """The citing page relative to the repository root, in POSIX form."""

    path: str
    """The cited path relative to the repository root, without its suffix."""

    test: str
    """The test function a ``::`` suffix names, or an empty string."""


def _prose(text: str) -> list[str]:
    lines = text.splitlines()
    kept: list[str] = []
    index = 0
    while index < len(lines):
        opening = OPENING.match(lines[index])
        index += 1
        if opening is None:
            kept.append(lines[index - 1])
            continue

        fence = opening["fence"]
        while index < len(lines) and not re.fullmatch(rf"[ \t]*{re.escape(fence[0])}{{{len(fence)},}}[ \t]*", lines[index]):
            index += 1

        index += 1

    return kept


def _citations(text: str, page: str) -> list[Citation]:
    spans = [span["text"] for line in _prose(text) for span in SPAN.finditer(line)]
    matches = [CITATION.fullmatch(span) for span in spans]

    return [Citation(page, match["path"].rstrip("/"), match["test"] or "") for match in matches if match is not None]


def _pages() -> list[Path]:
    return sorted(page for page in DOCS.rglob("*.md") if page.relative_to(DOCS).parts[0] not in SKIPPED)


FOUND = [citation for page in _pages() for citation in _citations(page.read_text(encoding="utf-8"), page.relative_to(ROOT).as_posix())]
"""Every citation across the hand-written pages, in page order."""

CITATIONS = sorted({(citation.page, citation.path) for citation in FOUND})
"""Every distinct (page, path) pair."""

TESTS = sorted({citation for citation in FOUND if citation.test})
"""Every distinct citation that names a test function."""


def test_scan_reads_suffixes_and_skips_fences() -> None:
    text = "\n".join(
        [
            "See `src/femforge/config.py:37-61` and `tests/test_cli.py:12`, `src/femforge/run/`.",
            "Ignore `pyproject.toml:12`, `femforge.run`, ``src/double.py`` and `src/a.py, src/b.py`.",
            "",
            "```python",
            "# `src/fenced.py`",
            "```",
            "",
            "    ````text",
            "    `src/indented.py`",
            "    ```",
            "    ````",
            "Then `.github/workflows/docs.yml` and `tests/test_cli.py::test_help`.",
        ],
    )

    assert _citations(text, "probe.md") == [
        Citation("probe.md", "src/femforge/config.py", ""),
        Citation("probe.md", "tests/test_cli.py", ""),
        Citation("probe.md", "src/femforge/run", ""),
        Citation("probe.md", ".github/workflows/docs.yml", ""),
        Citation("probe.md", "tests/test_cli.py", "test_help"),
    ]


def test_pages_cite_paths() -> None:
    assert CITATIONS


@pytest.mark.parametrize(("page", "path"), CITATIONS)
def test_cited_path_exists(page: str, path: str) -> None:
    if path in PENDING:
        assert not (ROOT / path).exists(), f"{path} exists; remove it from PENDING"

        return

    if path in GENERATED:
        assert path in IGNORED, f"{path} is a build output; list it in .gitignore"

        return

    assert (ROOT / path).exists(), f"{page} cites {path}"


@pytest.mark.parametrize("citation", TESTS, ids=[f"{citation.page}-{citation.path}::{citation.test}" for citation in TESTS])
def test_cited_test_is_defined(citation: Citation) -> None:
    source = (ROOT / citation.path).read_text(encoding="utf-8")

    assert re.search(rf"^\s*(?:async\s+)?def {citation.test}\(", source, re.MULTILINE), f"{citation.page} cites {citation.path}::{citation.test}"
