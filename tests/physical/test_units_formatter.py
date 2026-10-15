"""``RichEngineeringFormatter`` (physical.units): the Rich-markup pint formatter driven through ``format(q, spec)``."""

from typing import cast

import pytest

from femforge.physical.units import U, Quantity, RichEngineeringFormatter, VectorQuantity, quantity
from femforge.common.vector import Vec3

# Rich markup style tokens the formatter emits.
_MAG_OPEN = "[#FFB86C]"
_UNIT_OPEN = "[#E1E446 bold]"
_X_OPEN = "[#FC7B7B]"


def test_default_format_is_styled_and_compact() -> None:
    # ".5g~#PS" -> styled (S), compact (#), pretty (P).
    out: str = format(350 * U.K, ".5g~#PS")
    assert _MAG_OPEN in out  # magnitude wrapped in Rich markup (styled)
    assert _UNIT_OPEN in out  # unit wrapped in Rich markup
    assert "350" in out
    assert "K" in out


def test_unstyled_spec_emits_no_markup() -> None:
    # No "S" -> plain text.
    out: str = format(350 * U.K, ".5g~#P")
    assert out == "350 K"
    assert "[" not in out


def test_composite_unit_styles_each_segment_and_slash() -> None:
    # A unit with a slash exercises _style_unit's segment-join / slash-tagging.
    out: str = format(8.7 * U.W / (U.m * U.K), ".5g~#PS")
    assert _UNIT_OPEN in out
    assert "/" in out
    assert "W" in out
    assert "K" in out


def test_compact_picks_engineering_prefix() -> None:
    # With "#" (compact), a small value gets an SI prefix rather than e-notation.
    out: str = format(quantity(5.0e-8, U.m), ".5g~#PS")
    assert "nm" in out  # 50 nm
    assert "50" in out


def test_pretty_scientific_scalar_uses_superscripts() -> None:
    assert format(quantity(5.0e-8, U.m), ".3g~P") == "5×10⁻⁸ m"
    assert format(quantity(-5.0e-8, U.m), ".3g~P") == "-5×10⁻⁸ m"
    assert format(quantity(1.23e9, U.m), ".3g~P") == "1.23×10⁹ m"


def test_vector_magnitude_formats_three_components() -> None:
    # A Vec3-magnitude quantity exercises _format_vec3_magnitude.
    out: str = format(quantity(Vec3(3.0, 4.0, 0.0), U.N), ".5g~#PS")
    assert out.startswith("<")
    assert _X_OPEN in out  # the x component carries the x_style colour
    assert "3" in out and "4" in out and "0" in out
    assert "N" in out


def test_vector_pretty_scientific_per_component() -> None:
    out: str = format(quantity(Vec3(-5.0e-8, 2.5e-8, 0.0), U.N), ".3g~P")
    assert out == "<-5×10⁻⁸, 2.5×10⁻⁸, 0> N"


def test_vector_magnitude_unstyled() -> None:
    # Vector path with no "S": components joined but not Rich-tagged.
    out: str = format(quantity(Vec3(3.0, 4.0, 0.0), U.N), ".5g~#P")
    assert out.startswith("<")
    assert "[" not in out  # no Rich markup
    assert "3" in out and "4" in out


def test_vector_compact_uses_magnitude_for_prefix() -> None:
    # Vector + "#": the compact prefix comes from abs(mag) of the whole vector.
    out: str = format(quantity(Vec3(1.5e-8, 2.5e-8, 0.0), U.N), ".5g~#PS")
    assert "nN" in out  # nano-newton prefix selected from the vector magnitude


def test_dimensionless_quantity_has_no_unit_markup() -> None:
    # A dimensionless quantity yields an empty unit string -> _style_unit returns early, no unit markup.
    out: str = format(quantity(5.0, U.dimensionless), ".5g~#PS")
    assert "5" in out
    assert _UNIT_OPEN not in out  # no unit markup emitted (empty unit string)


def test_tag_returns_text_unchanged_when_style_or_text_empty() -> None:
    # _tag's early-return guard for empty style or empty text.
    assert RichEngineeringFormatter._tag("", "some-style") == ""
    assert RichEngineeringFormatter._tag("x", "") == "x"


def test_scalar_compact_falls_back_when_to_compact_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # The scalar compact branch wraps to_compact() in try/except; if it raises, the original quantity is used.
    fmt = cast(RichEngineeringFormatter, U.formatter)
    q: Quantity = 350 * U.K

    def boom(_self: object, *_a: object, **_k: object) -> Quantity:
        raise RuntimeError("boom")

    monkeypatch.setattr(type(q), "to_compact", boom)
    out: str = fmt.format_quantity(q, ".5g~#PS")
    assert "350" in out
    assert "K" in out


def test_vector_compact_falls_back_when_conversion_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # The vector compact branch also has a try/except; a raising quantity.to(...) keeps the original.
    fmt = cast(RichEngineeringFormatter, U.formatter)
    v: VectorQuantity = quantity(Vec3(3.0, 4.0, 0.0), U.N)

    def boom(_self: object, *_a: object, **_k: object) -> VectorQuantity:
        raise RuntimeError("boom")

    monkeypatch.setattr(type(v), "to", boom)
    out: str = fmt.format_quantity(cast(Quantity, v), ".5g~#PS")
    assert out.startswith("<")
    assert "N" in out
