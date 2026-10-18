"""Pygments filter that retags Python names by how they are used."""

from collections.abc import Iterator
from typing import Final

from pygments.filter import Filter
from pygments.lexer import Lexer
from pygments.token import Keyword, Name, Punctuation, Whitespace, _TokenType


__all__ = ["SemanticNames"]

Token = tuple[_TokenType, str]

DECLARATIONS: Final = frozenset({"async", "class", "def", "global", "lambda", "nonlocal"})
"""Keywords painted as declarations rather than control flow."""

BUILTIN_TYPES: Final = frozenset(
    {
        "bool",
        "bytes",
        "classmethod",
        "complex",
        "dict",
        "enumerate",
        "filter",
        "float",
        "frozenset",
        "int",
        "list",
        "map",
        "object",
        "property",
        "range",
        "set",
        "staticmethod",
        "str",
        "super",
        "tuple",
        "type",
        "zip",
    }
)
"""Builtins that are classes, painted as types rather than functions."""


class SemanticNames(Filter):
    """Retags plain names as functions, classes, constants, namespaces or variables."""

    def filter(self, lexer: Lexer, stream: Iterator[Token]) -> Iterator[Token]:
        tokens = list(stream)
        modules = bound_modules(tokens)
        for index, (ttype, value) in enumerate(tokens):
            if ttype is Name:
                ttype = retag(value, following(tokens, index), modules)
            elif ttype is Name.Builtin:
                ttype = Name.Class if value in BUILTIN_TYPES else Name.Function
            elif ttype is Keyword and value in DECLARATIONS:
                ttype = Keyword.Declaration

            yield ttype, value


def following(tokens: list[Token], index: int) -> Token:
    """The first token after `index` that is not whitespace."""
    for ttype, value in tokens[index + 1 :]:
        if ttype not in Whitespace:
            return ttype, value

    return Whitespace, ""


def retag(value: str, nxt: Token, modules: frozenset[str]) -> _TokenType:
    ntype, nvalue = nxt
    if value in modules:
        return Name.Namespace

    stem = value.lstrip("_") or value
    if ntype is Punctuation and nvalue.startswith("("):
        return Name.Class if stem[0].isupper() else Name.Function

    if len(stem) > 1 and stem.isupper():
        return Name.Constant

    if stem[0].isupper():
        return Name.Class

    return Name.Variable


def bound_modules(tokens: list[Token]) -> frozenset[str]:
    """Names bound by plain import statements: the alias, else the root package."""
    bound: set[str] = set()
    index = 0
    while index < len(tokens):
        ttype, value = tokens[index]
        index += 1
        if ttype is Keyword.Namespace and value == "import" and not from_import(tokens, index - 1):
            index = bind_import(tokens, index, bound)

    return frozenset(bound)


def from_import(tokens: list[Token], index: int) -> bool:
    """Whether the import keyword at `index` belongs to a from-import."""
    for ttype, value in reversed(tokens[:index]):
        if "\n" in value:
            return False

        if ttype is Keyword.Namespace and value == "from":
            return True

    return False


def bind_import(tokens: list[Token], index: int, bound: set[str]) -> int:
    """Add the names an import statement binds and return the index after it."""
    item: list[str] = []
    while index < len(tokens):
        ttype, value = tokens[index]
        index += 1
        if "\n" in value:
            break

        if ttype is Keyword and value == "as":
            item.clear()
        elif ttype is Name.Namespace and value != ".":
            item.append(value)
        elif value == ",":
            bound.update(item[:1])
            item = []

    bound.update(item[:1])
    return index
