"""The palette read from the dist's theme.json, painting plotly, pyvista, xterm and Monaco in the stylesheet's colours."""

import colorsys
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from enum import StrEnum
from functools import cache
from typing import cast

import plotly.io as pio

from femforge_workbench.widgets.module import BUILD_HINT, SERVE_DIR


TEMPLATE = "palette"
THEME_FILE = "theme.json"
NAMED_COLORS = {"white": (255, 255, 255), "black": (0, 0, 0)}
DARK_BELOW = 128.0
TITLELESS_TOP_MARGIN = 30

type Json = dict[str, object]


class Token(StrEnum):
    """The stylesheet tokens the palette reads."""

    BACKGROUND = "background"
    CARD = "card"
    FOREGROUND = "foreground"
    BORDER = "border"
    PRIMARY = "primary"
    MUTED_FOREGROUND = "muted-foreground"
    CHART_1 = "chart-1"
    CHART_2 = "chart-2"
    CHART_3 = "chart-3"
    CHART_4 = "chart-4"
    CHART_5 = "chart-5"


CHART_TOKENS = (Token.CHART_1, Token.CHART_2, Token.CHART_3, Token.CHART_4, Token.CHART_5)


class Section(StrEnum):
    """The sections of theme.json."""

    COLORS = "colors"
    METRICS = "metrics"
    FONTS = "fonts"


class Font(StrEnum):
    """The font stacks theme.json names."""

    SANS = "sans"
    MONO = "mono"


def rgb(color: str) -> tuple[int, int, int]:
    """The channels of a CSS colour written as a hex triplet, rgb()/rgba(), or white/black."""
    color = color.strip().lower()
    if color in NAMED_COLORS:
        return NAMED_COLORS[color]

    if color.startswith("#"):
        digits = color[1:]
        if len(digits) == 3:
            digits = "".join(ch * 2 for ch in digits)

        return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)

    numbers: list[str] = re.findall(r"[\d.]+", color)
    r, g, b = (int(float(n)) for n in numbers[:3])

    return r, g, b


def hex_color(color: str) -> str:
    """The colour as an upper-case hex triplet, the form pyvista takes."""
    red, green, blue = rgb(color)

    return f"#{red:02X}{green:02X}{blue:02X}"


def mix(color: str, onto: str, fraction: float) -> str:
    """The hex colour `fraction` of the way from `onto` to `color`.

    Args:
        color: The colour at a fraction of 1.
        onto: The colour at a fraction of 0.
        fraction: How far from `onto` toward `color`, from 0 to 1.
    """
    channels = [round(a * fraction + b * (1 - fraction)) for a, b in zip(rgb(color), rgb(onto), strict=True)]

    return "#" + "".join(f"{channel:02X}" for channel in channels)


def luminance(color: str) -> float:
    """The perceived brightness of a colour on the 0-255 scale."""
    r, g, b = rgb(color)

    return 0.299 * r + 0.587 * g + 0.114 * b


def rotate_hue(color: str, turns: float) -> str:
    """The colour with its hue rotated by a fraction of the wheel, as a hex triplet."""
    r, g, b = rgb(color)
    hue, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    r, g, b = colorsys.hls_to_rgb((hue + turns) % 1, lightness, saturation)

    return f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}"


def template_json(name: str | None = None) -> Json:
    """A registered plotly template (the default when unnamed) as plain JSON."""
    key = name or str(pio.templates.default)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]  # plotly's template registry ships no stub

    return cast("Json", pio.templates[key].to_plotly_json())  # pyright: ignore[reportUnknownMemberType]


def section(source: Mapping[str, object], key: str) -> Json:
    """The nested object under `key`, empty when absent."""
    value = source.get(key)
    if not isinstance(value, Mapping):
        return {}

    return dict(cast("Mapping[str, object]", value))


def text(source: Mapping[str, object], key: str, fallback: str) -> str:
    """The string under `key`, else the fallback.

    Args:
        source: The JSON object to read.
        key: The key to read.
        fallback: The string returned when the value is missing or not a string.
    """
    value = source.get(key)
    if not isinstance(value, str):
        return fallback

    return value


@dataclass(frozen=True)
class Tokens:
    """What theme.json carries: the colours, the metrics in CSS pixels and the font stacks."""

    colors: Mapping[str, str]
    metrics: Mapping[str, float]
    fonts: Mapping[str, str]

    @classmethod
    def from_json(cls, document: Mapping[str, object]) -> "Tokens":
        """Reads the tokens from theme.json's document."""
        colors = {name: str(value) for name, value in section(document, Section.COLORS).items()}
        metrics = {name: float(cast("float", value)) for name, value in section(document, Section.METRICS).items()}
        fonts = {name: str(value) for name, value in section(document, Section.FONTS).items()}

        return cls(colors=colors, metrics=metrics, fonts=fonts)


@cache
def load_tokens() -> Tokens:
    """The tokens of the built dist; a missing dist names the build command."""
    path = SERVE_DIR / THEME_FILE
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing: {BUILD_HINT}")

    return Tokens.from_json(cast("Mapping[str, object]", json.loads(path.read_text(encoding="utf-8"))))


@dataclass(frozen=True)
class Metrics:
    """The type sizes and chrome heights of the stylesheet, in CSS pixels."""

    size_body: float
    size_label: float
    bar_height: float
    tab_height: float
    space: float
    row_height: float

    @classmethod
    def from_tokens(cls, tokens: Tokens) -> "Metrics":
        """Reads the metrics from the tokens, which name each field in kebab case."""
        return cls(**{field.name: tokens.metrics[field.name.replace("_", "-")] for field in fields(cls)})


@dataclass(frozen=True)
class Tones:
    """The interaction colours derived from a palette, as hex."""

    raised: str
    on_accent: str


@dataclass(frozen=True)
class Palette:
    """The colours the panels share, read from the tokens."""

    paper: str
    plot: str
    text: str
    grid: str
    accent: str
    muted: str
    chart: tuple[str, ...]
    metrics: Metrics
    font: str
    mono: str

    @classmethod
    def from_tokens(cls, tokens: Tokens) -> "Palette":
        """The palette of the tokens."""
        colors = tokens.colors

        return cls(
            paper=colors[Token.BACKGROUND],
            plot=colors[Token.CARD],
            text=colors[Token.FOREGROUND],
            grid=colors[Token.BORDER],
            accent=colors[Token.PRIMARY],
            muted=colors[Token.MUTED_FOREGROUND],
            chart=tuple(colors[token] for token in CHART_TOKENS),
            metrics=Metrics.from_tokens(tokens),
            font=tokens.fonts[Font.SANS],
            mono=tokens.fonts[Font.MONO],
        )

    @classmethod
    def load(cls) -> "Palette":
        """The dist's palette."""
        return cls.from_tokens(load_tokens())

    @classmethod
    def with_accent(cls, accent: str) -> "Palette":
        """The dist's palette with an accent of the caller's own."""
        return replace(cls.load(), accent=accent)

    def colorway(self, count: int = 6) -> list[str]:
        """The chart colours, then hues rotated evenly from the accent for the rest."""
        listed = list(self.chart[:count])
        extra = count - len(listed)

        return [*listed, *(rotate_hue(self.accent, (i + 1) / (extra + 1)) for i in range(extra))]

    def tones(self) -> Tones:
        """The derived interaction colours: a raised surface and text that reads on the accent."""
        on_accent = self.paper if luminance(self.accent) >= DARK_BELOW else self.text

        return Tones(raised=mix(self.text, self.paper, 0.12), on_accent=hex_color(on_accent))

    def plotly_template(self, base: str | None = None) -> Json:
        """The base template, plotly_dark by default, repainted in this palette, at the label size, with the top margin a titleless figure needs."""
        template = template_json(base or "plotly_dark")
        layout = section(template, "layout")
        axis_paint = {"gridcolor": self.grid, "zerolinecolor": self.grid, "linecolor": self.grid}
        repainted: Json = {
            **layout,
            "paper_bgcolor": self.paper,
            "plot_bgcolor": self.plot,
            "font": {**section(layout, "font"), "color": self.text, "family": self.font, "size": self.metrics.size_label},
            "colorway": self.colorway(),
            "margin": {**section(layout, "margin"), "t": TITLELESS_TOP_MARGIN},
            "xaxis": {**section(layout, "xaxis"), **axis_paint},
            "yaxis": {**section(layout, "yaxis"), **axis_paint},
        }

        return {**template, "layout": repainted}

    def install(self) -> None:
        """Registers this palette's plotly template as the default, so every figure built afterwards is painted in it."""
        pio.templates[TEMPLATE] = self.plotly_template()  # pyright: ignore[reportUnknownMemberType]  # plotly's template registry ships no stub
        pio.templates.default = TEMPLATE  # pyright: ignore[reportUnknownMemberType]

    def xterm_theme(self) -> dict[str, str]:
        """The xterm ITheme colours."""
        return {"background": self.paper, "foreground": self.text, "cursor": self.accent, "selectionBackground": self.tones().raised}
