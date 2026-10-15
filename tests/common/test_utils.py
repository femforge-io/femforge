"""Coverage tests for ``common.utils``: each ``COLORS`` rich-markup helper and each free function is invoked and asserted."""

import os

import pytest
from rich.text import Text

from femforge.common.utils import (
    COLORS,
    clear_terminal,
    contains_rich_markup,
    preserve_and_format,
    title,
)


def test_gradient_multiple_steps() -> None:
    """A multi-step gradient interpolates endpoints inclusively."""
    colors: list[str] = COLORS.gradient("#000000", "#FFFFFF", 3)
    assert colors == ["#000000", "#7F7F7F", "#FFFFFF"]


def test_gradient_single_step() -> None:
    """``steps == 1`` avoids divide-by-zero via ``max(steps - 1, 1)``."""
    colors: list[str] = COLORS.gradient("#FF0000", "#00FF00", 1)
    assert colors == ["#FF0000"]


def test_gradient_zero_steps() -> None:
    """Zero steps yields an empty list."""
    colors: list[str] = COLORS.gradient("#FF0000", "#00FF00", 0)
    assert colors == []


def test_prop_plain_value() -> None:
    """A plain value takes the non-markup ``PropName + : + PropValue`` path."""
    result: Text = COLORS.Prop("speed", "10")
    assert result.plain == "speed: 10"


def test_prop_markup_value() -> None:
    """A value containing rich markup takes the ``contains_rich_markup`` path."""
    result: Text = COLORS.Prop("speed", "[bold]fast[/bold]")
    assert result.plain == "speed: fast"


def test_prop_name() -> None:
    """``PropName`` styles the text with the property-name color."""
    result: Text = COLORS.PropName("torque")
    assert result.plain == "torque"
    assert result.style == COLORS.PROPERTY_NAME


def test_prop_value() -> None:
    """``PropValue`` styles the text with the property-value color."""
    result: Text = COLORS.PropValue("42")
    assert result.plain == "42"
    assert result.style == COLORS.PROPERTY_VALUE


def test_h1() -> None:
    """``H1`` applies the header-1 color plus bold."""
    result: Text = COLORS.H1("Title")
    assert result.plain == "Title"
    assert result.style == COLORS.HEADER_1 + " bold"


def test_h2() -> None:
    """``H2`` applies the header-2 color plus bold."""
    result: Text = COLORS.H2("Sub")
    assert result.plain == "Sub"
    assert result.style == COLORS.HEADER_2 + " bold"


def test_h3() -> None:
    """``H3`` applies the header-3 color plus bold."""
    result: Text = COLORS.H3("Minor")
    assert result.plain == "Minor"
    assert result.style == COLORS.HEADER_3 + " bold"


def test_units() -> None:
    """``Units`` styles the text with the units color."""
    result: Text = COLORS.Units("mm")
    assert result.plain == "mm"
    assert result.style == COLORS.UNITS


def test_title_replaces_underscores() -> None:
    """``title`` swaps underscores for spaces and title-cases."""
    assert title("max_speed_limit") == "Max Speed Limit"


def test_title_keeps_acronyms_capitals_and_joining_words() -> None:
    """Acronyms come out upper case, words already holding a capital stay as written, joining words stay lower case past the first."""
    assert title("run_id") == "Run ID"
    assert title("magnetostatics_2d") == "Magnetostatics 2D"
    assert title("polar_xy") == "Polar XY"
    assert title("GeometryIds") == "Geometry Ids"
    assert title("Fx [N]") == "Fx [N]"
    assert title("Steel1080 per axis") == "Steel1080 per Axis"
    assert title("back to the field colormap") == "Back to the Field Colormap"
    assert title("the green-blue map") == "The Green-Blue Map"


def test_contains_rich_markup_true() -> None:
    """Balanced brackets plus a known style token is detected as markup."""
    assert contains_rich_markup("[bold]x[/bold]") is True


def test_contains_rich_markup_no_brackets() -> None:
    """Plain text without brackets is not markup."""
    assert contains_rich_markup("plain text") is False


def test_contains_rich_markup_brackets_without_style() -> None:
    """Brackets without any known style token are not markup."""
    assert contains_rich_markup("[abc]") is False


def test_contains_rich_markup_non_string() -> None:
    """A non-string argument short-circuits to ``False``."""
    assert contains_rich_markup(123) is False  # pyright: ignore[reportArgumentType]


def test_preserve_and_format_text_instance() -> None:
    """An existing ``Text`` is appended into the styled container."""
    src: Text = Text("hello", style="italic")
    result: Text = preserve_and_format(src)
    assert result.plain == "hello"
    assert result.style == "bold"


def test_preserve_and_format_non_string() -> None:
    """A non-string, non-``Text`` value is stringified."""
    result: Text = preserve_and_format(42)
    assert result.plain == "42"
    assert result.style == "bold"


def test_preserve_and_format_ansi() -> None:
    """An ANSI escape sequence is parsed via ``Text.from_ansi``."""
    result: Text = preserve_and_format("\x1b[32mgreen\x1b[0m")
    assert result.plain == "green"


def test_preserve_and_format_markup() -> None:
    """Bracketed rich markup is parsed via ``Text.from_markup``."""
    result: Text = preserve_and_format("[red]warn[/red]")
    assert result.plain == "warn"


def test_preserve_and_format_plain_text() -> None:
    """Plain text falls through to a direct append."""
    result: Text = preserve_and_format("just text")
    assert result.plain == "just text"
    assert result.style == "bold"


def test_preserve_and_format_custom_style() -> None:
    """The ``new_style`` argument controls the container style."""
    result: Text = preserve_and_format("text", new_style="underline")
    assert result.style == "underline"


def test_clear_terminal_issues_the_platform_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    issued: list[str] = []

    def record(command: str) -> int:
        issued.append(command)
        return 0

    monkeypatch.setattr(os, "system", record)
    clear_terminal()
    assert issued == ["cls" if os.name == "nt" else "clear"]
