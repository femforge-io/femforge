"""attribute_docstrings reads the string literal under each class or module assignment."""

import inspect
import sys
from typing import TYPE_CHECKING

import pytest

import femforge.common.introspect as introspect_module
from femforge.common.introspect import attribute_docstrings


type _Alias = list[int]
"""An alias the type statement binds."""

if TYPE_CHECKING:
    _checked: int = 1
    """Bound for the type checker."""
else:
    _runtime = 2
    """Bound at runtime."""


class _Documented:
    plain = 1
    """The plain one."""

    annotated: int = 2
    """
    The annotated one,
    cleaned.
    """

    undocumented = 3

    def method(self) -> None: ...

    """A string after a method documents nothing."""


def test_class_attribute_docstrings() -> None:
    assert attribute_docstrings(_Documented) == {"plain": "The plain one.", "annotated": "The annotated one,\ncleaned."}


def test_module_attribute_docstrings() -> None:
    import femforge.materials.copper as copper

    docs = attribute_docstrings(copper)
    assert docs["REFERENCE_TEMPERATURE"] == "`T_ref` `[K]`: The temperature the copper conductivity is quoted at (20 °C)."


def test_type_aliases_and_both_branches_of_an_if_carry_docstrings() -> None:
    docs = attribute_docstrings(sys.modules[__name__])

    assert docs["_Alias"] == "An alias the type statement binds."
    assert docs["_checked"] == "Bound for the type checker."
    assert docs["_runtime"] == "Bound at runtime."


def test_source_without_a_class_definition_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_a_class(_target: object) -> str:
        return "x = 1\n"

    monkeypatch.setattr(inspect, "getsource", not_a_class)
    with pytest.raises(TypeError, match="class definition"):
        _ = introspect_module.attribute_docstrings(_Documented)
