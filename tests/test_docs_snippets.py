"""Every ``python`` code block in the hand-written docs type-checks as a standalone module."""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, cast

import pytest


ROOT = Path(__file__).parents[1]
"""The repository root, whose basedpyright configuration checks the blocks."""

DOCS = ROOT / "docs"
"""The docs source tree."""

SKIPPED = ("reference",)
"""Top-level docs directories outside the check: the generated API reference."""

OPENING = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>[^`]*?)[ \t]*$")
"""An opening code fence: its indentation, its fence run and its info string."""

INFO = "python"
"""The exact info string of a checked block; attribute fences such as ``{ .python .no-check }`` differ from it."""


class Snippet(NamedTuple):
    """One checked code block and where it sits."""

    page: str
    """The page path relative to the repository root, in POSIX form."""

    line: int
    """The 1-based page line of the opening fence."""

    code: str
    """The block body with the fence indentation removed."""


def _snippets(text: str, page: str) -> list[Snippet]:
    lines = text.splitlines()
    found: list[Snippet] = []
    index = 0
    while index < len(lines):
        opening = OPENING.match(lines[index])
        index += 1
        if opening is None:
            continue

        indent, fence = opening["indent"], opening["fence"]
        start = index
        while index < len(lines) and not re.fullmatch(rf"[ \t]*{re.escape(fence[0])}{{{len(fence)},}}[ \t]*", lines[index]):
            index += 1

        body = [line.removeprefix(indent) for line in lines[start:index]]
        index += 1
        if opening["info"] != INFO:
            continue

        found.append(Snippet(page, start, "\n".join(body) + "\n"))

    return found


def _pages() -> list[Path]:
    return sorted(page for page in DOCS.rglob("*.md") if page.relative_to(DOCS).parts[0] not in SKIPPED)


SNIPPETS = [snippet for page in _pages() for snippet in _snippets(page.read_text(encoding="utf-8"), page.relative_to(ROOT).as_posix())]
"""Every checked block across the hand-written pages, in page order."""


def test_fence_scan_reads_indented_and_attribute_fences() -> None:
    text = "\n".join(
        [
            "```python",
            "x = 1",
            "```",
            "",
            "```{ .python .no-check }",
            "y = 2",
            "```",
            "",
            '```python exec="on"',
            "z = 3",
            "```",
            "",
            "=== \"Tab\"",
            "",
            "    ````python",
            "    w = 4",
            "    ```",
            "    ````",
            "",
        ],
    )
    snippets = _snippets(text, "probe.md")

    assert snippets == [Snippet("probe.md", 1, "x = 1\n"), Snippet("probe.md", 15, "w = 4\n```\n")]


def test_python_blocks_type_check(tmp_path: Path) -> None:
    if not SNIPPETS:
        pytest.skip("no python code block in the docs")

    files: dict[str, Snippet] = {}
    for snippet in SNIPPETS:
        stem = snippet.page.removeprefix("docs/").removesuffix(".md").replace("/", "__")
        file = tmp_path / f"{stem}_L{snippet.line}.py"
        file.write_text(f"# {snippet.page}:{snippet.line}\n{snippet.code}", encoding="utf-8")
        files[file.name] = snippet

    done = subprocess.run(
        [sys.executable, "-m", "basedpyright", "--project", str(ROOT / "pyproject.toml"), "--outputjson", *(str(tmp_path / name) for name in files)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = cast("dict[str, object]", json.loads(done.stdout))
    diagnostics = cast("list[dict[str, object]]", report["generalDiagnostics"])
    summary = cast("dict[str, int]", report["summary"])

    errors: list[str] = []
    warnings: list[str] = []
    for diagnostic in diagnostics:
        snippet = files[Path(cast("str", diagnostic["file"])).name]
        start = cast("dict[str, dict[str, int]]", diagnostic["range"])["start"]
        message = cast("str", diagnostic["message"]).replace("\n", " ")
        entry = f"{snippet.page}:{snippet.line + start['line']}: {message}"
        if diagnostic["severity"] == "error":
            errors.append(entry)
        else:
            warnings.append(entry)

    assert summary["filesAnalyzed"] == len(files)
    assert errors == [], "\n".join([*errors, *(f"warning: {entry}" for entry in warnings)])
