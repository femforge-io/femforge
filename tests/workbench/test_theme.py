"""The palette: tokens read from theme.json, the scheme picked, the plotly template, and the live dist."""

import re
from pathlib import Path
from typing import cast

import pytest

from femforge_workbench import theme
from femforge_workbench.theme import TEMPLATE, Metrics, Palette, Tokens, hex_color, luminance, mix, rgb, rotate_hue, section, template_json, text


HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
DOCUMENT: dict[str, object] = {
    "light": {
        "background": "#ffffff",
        "foreground": "#0a0a0a",
        "card": "#f7f7f7",
        "border": "#e5e5e5",
        "primary": "#171717",
        "muted-foreground": "#737373",
        "chart-1": "#419bff",
        "chart-2": "#0b5ee9",
        "chart-3": "#0f4ace",
        "chart-4": "#153eae",
        "chart-5": "#19328a",
    },
    "dark": {
        "background": "#0f1010",
        "foreground": "#fafafa",
        "card": "#171717",
        "border": "#242222",
        "primary": "#16fe8e",
        "muted-foreground": "#a1a1a1",
        "chart-1": "#9b2afe",
        "chart-2": "#3c1499",
        "chart-3": "#0f63bd",
        "chart-4": "#1ec6d2",
        "chart-5": "#0ce87e",
    },
    "metrics": {"size-body": 14, "size-label": 12.5, "bar-height": 40, "tab-height": 30, "space": 4, "row-height": 26},
    "fonts": {"sans": "Inter, sans-serif", "mono": "JetBrains Mono, monospace"},
}


def _tokens() -> Tokens:
    return Tokens.from_json(DOCUMENT)


def test_hex_color_normalises_every_css_form() -> None:
    assert hex_color("rgb(17,17,17)") == "#111111"
    assert hex_color("rgba(1, 2, 3, 0.5)") == "#010203"
    assert hex_color("#abc") == "#AABBCC"
    assert hex_color("white") == "#FFFFFF"
    assert rgb("black") == (0, 0, 0)


def test_mix_luminance_and_hue_rotation() -> None:
    assert mix("#FFFFFF", "#000000", 0.5) == "#808080"
    assert mix("#FF0000", "#0000FF", 1.0) == "#FF0000"
    assert luminance("white") == 255.0
    assert rotate_hue("#FF0000", 1 / 3) == "#00FF00"


def test_tokens_read_both_schemes_the_metrics_and_the_fonts() -> None:
    tokens = _tokens()

    assert tokens.colors["dark"]["background"] == "#0f1010" and tokens.colors["light"]["background"] == "#ffffff"
    assert tokens.metrics == {"size-body": 14.0, "size-label": 12.5, "bar-height": 40.0, "tab-height": 30.0, "space": 4.0, "row-height": 26.0}
    assert tokens.fonts == {"sans": "Inter, sans-serif", "mono": "JetBrains Mono, monospace"}
    assert Metrics.from_tokens(tokens) == Metrics(size_body=14, size_label=12.5, bar_height=40, tab_height=30, space=4, row_height=26)


def test_the_palette_maps_tokens_by_scheme() -> None:
    dark = Palette.from_tokens(_tokens(), dark=True)
    light = Palette.from_tokens(_tokens(), dark=False)

    assert (dark.paper, dark.plot, dark.text, dark.grid, dark.accent, dark.muted) == ("#0f1010", "#171717", "#fafafa", "#242222", "#16fe8e", "#a1a1a1")
    assert dark.chart == ("#9b2afe", "#3c1499", "#0f63bd", "#1ec6d2", "#0ce87e")
    assert dark.is_dark and dark.monaco_theme == "vs-dark" and dark.font == "Inter, sans-serif" and dark.mono == "JetBrains Mono, monospace"
    assert not light.is_dark and light.monaco_theme == "vs" and light.paper == "#ffffff" and light.accent == "#171717"
    assert dark.metrics.bar_height == 40


def test_colorway_starts_with_the_chart_colours_then_rotates_the_accent() -> None:
    palette = Palette.from_tokens(_tokens(), dark=True)

    assert palette.colorway(3) == list(palette.chart[:3])
    assert palette.colorway(5) == list(palette.chart)
    seven = palette.colorway(7)
    assert seven[:5] == list(palette.chart) and len(seven) == 7
    assert seven[5] == rotate_hue(palette.accent, 1 / 3) and seven[6] == rotate_hue(palette.accent, 2 / 3)


def test_tones_derive_hex_chrome_and_contrasting_text() -> None:
    palette = Palette.from_tokens(_tokens(), dark=True)

    tones = palette.tones()

    assert all(HEX.match(value) for value in (tones.raised, tones.on_accent))
    assert tones.on_accent == hex_color(palette.paper)
    assert Palette.from_tokens(_tokens(), dark=False).tones().on_accent == hex_color("#0a0a0a"), "a dark accent takes the light scheme's text"


def test_xterm_theme_paints_the_terminal_in_the_palette() -> None:
    palette = Palette.from_tokens(_tokens(), dark=True)

    assert palette.xterm_theme() == {"background": "#0f1010", "foreground": "#fafafa", "cursor": "#16fe8e", "selectionBackground": palette.tones().raised}


def test_plotly_template_repaints_the_scheme_base_and_install_registers_it() -> None:
    dark = Palette.from_tokens(_tokens(), dark=True)
    light = Palette.from_tokens(_tokens(), dark=False)

    template = dark.plotly_template()
    layout = section(template, "layout")
    dark.install()
    installed = section(template_json(TEMPLATE), "layout")

    assert layout["paper_bgcolor"] == dark.paper and layout["plot_bgcolor"] == dark.plot
    assert layout["colorway"] == dark.colorway()
    assert section(layout, "font") | {"color": dark.text, "family": dark.font, "size": 12.5} == section(layout, "font")
    assert section(layout, "margin")["t"] == 30
    assert text(section(layout, "xaxis"), "gridcolor", "") == dark.grid
    assert text(installed, "paper_bgcolor", "") == dark.paper
    assert section(template_json(), "layout") == installed
    assert section(light.plotly_template(), "layout")["paper_bgcolor"] == "#ffffff"
    assert text(section(template_json("plotly_white"), "layout"), "paper_bgcolor", "") == "white", "the light scheme starts from plotly_white"


def test_a_missing_dist_names_the_build_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(theme, "SERVE_DIR", tmp_path)
    theme.load_tokens.cache_clear()
    try:
        with pytest.raises(FileNotFoundError, match="npm run build"):
            _ = theme.load_tokens()
    finally:
        theme.load_tokens.cache_clear()


@pytest.mark.skipif(not (theme.SERVE_DIR / theme.THEME_FILE).exists(), reason="the dist is not built")
def test_the_live_dist_paints_the_dark_background_the_owner_tuned() -> None:
    palette = Palette.load()

    assert palette.paper.lower() == "#0f1010"
    assert palette.accent.lower() == "#16fe8e", "the accent is the stylesheet's primary token, not a Python constant"
    assert palette.is_dark and len(palette.chart) == 5
    assert Palette.with_accent("#00CC6A").accent == "#00CC6A" and Palette.with_accent("#00CC6A").paper == palette.paper
    assert cast("float", palette.metrics.size_label) == 12.5
    assert palette.font.startswith("Inter")
