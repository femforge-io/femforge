"""Console formatting helpers: Rich color styles, Title Case labels and markup handling."""

import os
import re

from rich.text import Text


class COLORS:
    """For use in rich console output. Not for physical materials."""

    PROPERTY_NAME = "bright_cyan"
    PROPERTY_VALUE = "#FFB86D"

    MAGNITUDE = "#FFB86C"
    UNITS = "#E1E446"

    HEADER_1 = "#FF5555"
    HEADER_2 = "#FF5555"  # CE6534
    HEADER_3 = "#FF5555"  # 50FA7B

    @staticmethod
    def gradient(start_color: str, end_color: str, steps: int) -> list[str]:
        """Generate ``steps`` hex colors interpolating from ``start_color`` to ``end_color``.

        Args:
            start_color: The first color, as ``#RRGGBB``.
            end_color: The last color, as ``#RRGGBB``.
            steps: How many colors to generate.
        """
        start_rgb = tuple(int(start_color[i : i + 2], 16) for i in (1, 3, 5))
        end_rgb = tuple(int(end_color[i : i + 2], 16) for i in (1, 3, 5))

        gradient_colors: list[str] = []
        for step in range(steps):
            ratio = step / max(steps - 1, 1)
            intermediate_rgb = tuple(int(start + (end - start) * ratio) for start, end in zip(start_rgb, end_rgb))
            gradient_colors.append("#{:02X}{:02X}{:02X}".format(*intermediate_rgb))

        return gradient_colors

    @staticmethod
    def Prop(name: str, value: str) -> Text:
        """Format a property name/value pair with colors for rich console output."""
        if contains_rich_markup(value):
            return COLORS.PropName(name).append(Text(": ", style="bold")).append(preserve_and_format(value))

        return COLORS.PropName(name) + Text(": ", style="bold") + COLORS.PropValue(value)

    @staticmethod
    def PropName(name: str) -> Text:
        """A property name in the property-name style."""
        return Text(name, style=COLORS.PROPERTY_NAME)

    @staticmethod
    def PropValue(value: str) -> Text:
        """A property value in the property-value style."""
        return Text(value, style=COLORS.PROPERTY_VALUE)

    @staticmethod
    def H1(text: str) -> Text:
        """A top-level heading."""
        return Text(text, style=COLORS.HEADER_1 + " bold")

    @staticmethod
    def H2(text: str) -> Text:
        """A second-level heading."""
        return Text(text, style=COLORS.HEADER_2 + " bold")

    @staticmethod
    def H3(text: str) -> Text:
        """A third-level heading."""
        return Text(text, style=COLORS.HEADER_3 + " bold")

    @staticmethod
    def Units(text: str) -> Text:
        """A unit label in the units style."""
        return Text(text, style=COLORS.UNITS)


TITLE_ACRONYMS = frozenset({"2D", "3D", "CPU", "GB", "GPU", "ID", "KB", "MB", "MPI", "OK", "RZ", "SIF", "UTC", "VTU", "XY"})
"""Words a title writes in capitals whatever case they arrive in."""

TITLE_MINOR = frozenset({"a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on", "or", "per", "the", "to", "via", "vs"})
"""Joining words a title keeps lower case unless they lead it."""

CAMEL_HUMP = re.compile(r"(?<=[a-z])(?=[A-Z])")


def title(value: str) -> str:
    """A label or identifier in Title Case: underscores and camelCase humps become spaces, acronyms are capitalized, joining words stay lower case."""

    def cased(word: str, *, first: bool) -> str:
        if word.upper() in TITLE_ACRONYMS:
            return word.upper()

        if any(char.isupper() for char in word):
            return word

        if not first and word in TITLE_MINOR:
            return word

        return "-".join(part[:1].upper() + part[1:] for part in word.split("-"))

    words = CAMEL_HUMP.sub(" ", value.replace("_", " ")).split(" ")

    return " ".join(cased(word, first=index == 0) for index, word in enumerate(words))


def contains_rich_markup(text: str) -> bool:
    """Whether ``text`` looks like it carries Rich markup."""
    if not isinstance(text, str):
        return False
    # Checks for unbalanced/basic Rich markup sequences
    has_brackets = "[" in text and "]" in text
    style_markers = ["bold", "italic", "underline", "on ", "color", "dim", "[#", "[/]"]
    return has_brackets and any(style in text for style in style_markers)


def preserve_and_format(content: object, new_style: str = "bold") -> Text:
    """Preserves existing string formatting and applies a new style safely."""
    # Initialize a clean parent container holding the new global style
    container = Text(style=new_style)

    # 1. Handle already instantiated Rich Text objects
    if isinstance(content, Text):
        container.append(content)
        return container

    if not isinstance(content, str):
        container.append(str(content))
        return container

    # 2. Handle ANSI Escape Codes (e.g., \x1b[32m)
    ansi_regex = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    if ansi_regex.search(content):
        container.append(Text.from_ansi(content))
        return container

    # 3. Handle Rich Console Markup (e.g., [red]text[/red])
    if "[" in content and "]" in content:
        container.append(Text.from_markup(content))
        return container

    # 4. Handle plain text
    container.append(content)
    return container


def clear_terminal():
    """Clear the terminal screen."""
    # Clears the screen using 'cls' on Windows, and 'clear' on macOS/Linux
    os.system("cls" if os.name == "nt" else "clear")
