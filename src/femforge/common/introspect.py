"""Source-level reflection: the attribute docstrings an IDE shows on hover, read back at runtime."""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from types import ModuleType


FIELD_DOCSTRING = re.compile(r"^(?:`(?P<symbol>[^`]+)` )?`\[(?P<unit>[^\]]+)\]`: (?P<meaning>\S.*)", re.DOTALL)
"""A physical field's docstring, `` `glyph` `[unit]`: Meaning``; the glyph is optional for dimensionless knobs."""


def attribute_docstrings(target: type | ModuleType) -> dict[str, str]:
    """The string literal directly under each assignment or type alias in a class or module body, ``if`` blocks included, keyed by name."""
    source: str = textwrap.dedent(inspect.getsource(target))
    tree: ast.Module = ast.parse(source)

    body: list[ast.stmt] = tree.body
    if isinstance(target, type):
        first: ast.stmt = tree.body[0]
        if not isinstance(first, ast.ClassDef):
            raise TypeError(f"{target.__qualname__}'s source does not start with its class definition.")
        body = first.body

    docstrings: dict[str, str] = {}

    def collect(statements: list[ast.stmt]) -> None:
        for statement, following in zip(statements, statements[1:]):
            text: str | None = _string_literal(following)
            if text is None:
                continue

            for name in _assigned_names(statement):
                docstrings[name] = inspect.cleandoc(text)

        for statement in statements:
            if isinstance(statement, ast.If):
                collect(statement.body)
                collect(statement.orelse)

    collect(body)

    return docstrings


def _string_literal(statement: ast.stmt) -> str | None:
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
        return statement.value.value
    return None


def _assigned_names(statement: ast.stmt) -> list[str]:
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return [statement.target.id]
    if isinstance(statement, ast.Assign):
        return [target.id for target in statement.targets if isinstance(target, ast.Name)]
    if isinstance(statement, ast.TypeAlias):
        return [statement.name.id]
    return []
