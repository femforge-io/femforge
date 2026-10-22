"""The docs highlight filter tags names the way an editor's semantic tokens would."""

import pytest
from femforge_docs.highlight import SemanticNames
from pygments.lexers import get_lexer_by_name  # pyright: ignore[reportUnknownVariableType]
from pygments.token import Keyword, Name, _TokenType

SOURCE = '''\
import os, plotly.graph_objects as go
from femforge.run import Driver, solve

import femforge as ff

magnet = ff.EntityTag(selector={ff.TagKey.NAME: "Magnet"})
run = ff.materials.N52()
_ = _Private()
result = ff.Driver().evaluate(magnet)
print(len(result), int(os.environ), go.Figure(), solve)


def total(self, count: int) -> float:
    if count:
        return count

    raise ValueError
'''


def last_tag_per_word() -> dict[str, _TokenType]:
    lexer = get_lexer_by_name("python", filters=[SemanticNames()])
    tags: dict[str, _TokenType] = {}
    for ttype, value in lexer.get_tokens(SOURCE):
        tags[value] = ttype

    return tags


TAGS = last_tag_per_word()


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("ff", Name.Namespace),
        ("os", Name.Namespace),
        ("go", Name.Namespace),
        ("run", Name.Variable),
        ("solve", Name.Variable),
        ("EntityTag", Name.Class),
        ("Driver", Name.Class),
        ("N52", Name.Class),
        ("_Private", Name.Class),
        ("TagKey", Name.Class),
        ("NAME", Name.Constant),
        ("evaluate", Name.Function),
        ("print", Name.Function),
        ("len", Name.Function),
        ("total", Name.Function),
        ("int", Name.Class),
        ("float", Name.Class),
        ("selector", Name.Variable),
        ("materials", Name.Variable),
        ("magnet", Name.Variable),
        ("_", Name.Variable),
        ("count", Name.Variable),
        ("def", Keyword.Declaration),
        ("if", Keyword),
        ("return", Keyword),
        ("import", Keyword.Namespace),
        ("self", Name.Builtin.Pseudo),
        ("ValueError", Name.Exception),
    ],
)
def test_words_take_their_role(word: str, expected: _TokenType) -> None:
    assert TAGS[word] is expected


def test_a_trailing_name_with_nothing_after_it_is_a_variable() -> None:
    lexer = get_lexer_by_name("python", filters=[SemanticNames()])

    assert [ttype for ttype, value in lexer.get_tokens("width") if value == "width"] == [Name.Variable]
