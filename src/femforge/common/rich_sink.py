"""Rich-powered Loguru sink that renders each record as a fixed-column row via a Rich Console."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from rich.align import Align
from rich.console import Console, RenderableType
from rich.measure import Measurement
from rich.table import Table
from rich.text import Text
from rich.traceback import Traceback


if TYPE_CHECKING:
    # Message/Record/RecordException exist only in loguru's type stub, not at runtime.
    from loguru import Message, Record, RecordException


class RichSink:
    """Loguru sink callable; add with ``format="{message}"`` and ``colorize=False`` so Rich owns all styling."""

    LEVEL_STYLES: ClassVar[dict[str, str]] = {
        "TRACE": "dim cyan",
        "DEBUG": "cyan",
        "INFO": "bold blue",
        "SUCCESS": "bold green",
        "WARNING": "bold yellow",
        "ERROR": "bold red",
        "CRITICAL": "bold white on red",
    }

    CTX_MIN: ClassVar[int] = 50

    console: Console
    time_format: str
    show_path: bool
    path_width: int
    rich_tracebacks: bool
    show_locals: bool
    markup: bool
    hide_keys: frozenset[str]

    def __init__(
        self,
        console: Console | None = None,
        *,
        time_format: str = "%H:%M:%S.%f",
        show_path: bool = True,
        path_width: int = 0,
        rich_tracebacks: bool = True,
        show_locals: bool = False,
        markup: bool = False,
        hide_keys: frozenset[str] = frozenset(),
    ) -> None:
        """Configure the sink's columns and styling.

        Args:
            console: The console to print to; None prints to a new stderr console.
            time_format: The ``strftime`` format of the time column.
            show_path: Show the ``module:function`` column.
            path_width: Truncate the location column to this width, keeping its tail; 0 sizes it to its content.
            rich_tracebacks: Render a logged exception as a Rich traceback.
            show_locals: Include local variables in Rich tracebacks.
            markup: Parse Rich markup in messages.
            hide_keys: The bound ``extra`` keys to leave out of the context column.
        """
        self.console = console if console is not None else Console(stderr=True)
        self.time_format = time_format
        self.show_path = show_path
        self.path_width = path_width
        self.rich_tracebacks = rich_tracebacks
        self.show_locals = show_locals
        self.markup = markup
        self.hide_keys = hide_keys

    def _location(self, record: Record) -> Text:
        """The ``name:function`` location, left-truncated (tail kept) when longer than ``path_width``."""
        name = str(record["name"])
        function = str(record["function"])
        location = f"{name}:{function}"

        if 0 < self.path_width < len(location):
            location = "…" + location[-(self.path_width - 1) :]

        # Style split at the last colon; truncation can eat it, leaving a bare function tail.
        head, colon, tail = location.rpartition(":")
        if colon:
            return Text(head, style="dim green4") + Text(":", style="dim") + Text(tail, style="yellow3 dim")
        return Text(location, style="yellow3 dim")

    def __call__(self, message: Message) -> None:
        record: Record = message.record

        level_name: str = record["level"].name
        style: str = self.LEVEL_STYLES.get(level_name, "white")

        # %f is microseconds (6 digits); trim to centiseconds so the stamp fills its column exactly.
        time_str: str = record["time"].strftime(self.time_format)
        if self.time_format.endswith("%f"):
            time_str = time_str[:-4]

        # Structured context from logger.bind(...) / logger.contextualize(...).
        bound: dict[str, object] = cast("dict[str, object]", record["extra"])
        extra: dict[str, object] = {k: v for k, v in bound.items() if k not in self.hide_keys}

        grid = Table(padding=(0, 1), box=None, show_header=False, expand=True)
        grid.add_column("time", style="dim", width=11, no_wrap=True)
        grid.add_column("level", style=style, justify="center", width=3, no_wrap=True)

        if self.show_path:
            # path_width=0 means unfixed: size the column to its content.
            grid.add_column("location", style="dim", justify="left", width=self.path_width + 1 if self.path_width > 0 else None)

        if extra:
            ctx_max = max(self.CTX_MIN, int(self.console.width * 0.55))  # context never crowds out the message
            grid.add_column("message", style=style, justify="left", ratio=1, min_width=self.CTX_MIN)
            grid.add_column("context", justify="left", min_width=self.CTX_MIN, max_width=ctx_max, overflow="fold")
        else:
            grid.add_column("message", style=style, justify="left", ratio=1)

        row: list[RenderableType] = [time_str, record["level"].icon]

        if self.show_path:
            row.append(self._location(record))

        # Literal Text by default: a bare str cell would be markup-parsed by the Table, eating bracketed text.
        if self.markup:
            row.append(Text.from_markup(record["message"], style=style))
        else:
            row.append(Text(record["message"], style=style))

        if extra:
            row.append(Align.right(ContextFormatter.format_root(self.console, extra, width=ctx_max)))

        grid.add_row(*row)
        self.console.print(grid)

        # Fully populated only with diagnose=True / backtrace=True on logger.add.
        exc: RecordException | None = record["exception"]
        if self.rich_tracebacks and exc is not None and exc.type is not None and exc.value is not None:
            tb: Traceback = Traceback.from_exception(
                exc.type,
                exc.value,
                exc.traceback,
                show_locals=self.show_locals,
                width=self.console.width or 100,
            )
            self.console.print(tb)


class ContextFormatter:
    """Formats loguru ``extra`` values — plain data plus nested Rich renderables — into styled Text."""

    KEY = "bold sky_blue2"
    STR = "green"
    NUM = "bright_cyan"
    BOOL = "italic magenta"
    NONE = "dim italic"
    PUNCT = "grey50"
    OTHER = "white"

    INDENT = "  "
    INLINE_MAX = 60

    @staticmethod
    def is_renderable(value: object) -> bool:
        """True for Rich renderables, as opposed to plain data values."""
        return hasattr(value, "__rich_console__") or hasattr(value, "__rich__")

    @classmethod
    def flatten(cls, renderable: RenderableType, console: Console, width: int) -> Text:
        """Render a block renderable down to a flat, styled Text.

        Args:
            renderable: The block to render.
            console: The console whose options drive the render.
            width: The widest the rendered block may be.
        """
        options = console.options.update(width=width)
        natural = Measurement.get(console, options, renderable).maximum
        options = console.options.update(width=max(1, min(width, natural)))

        out = Text()
        for i, line in enumerate(console.render_lines(renderable, options, pad=False)):
            if i:
                out.append("\n")
            for segment in line:
                if not segment.control and segment.text:
                    out.append(segment.text, style=segment.style)
        out.rstrip()
        return out

    @classmethod
    def indent(cls, text: Text, levels: int = 1) -> Text:
        """Prefix every line of ``text`` with ``levels`` units of indentation."""
        pad = cls.INDENT * levels
        out = Text(pad)
        for i, line in enumerate(text.split("\n", allow_blank=True)):
            if i:
                out.append("\n" + pad)
            out.append_text(line)
        return out

    @classmethod
    def parts(
        cls,
        container: dict[object, object] | list[object] | tuple[object, ...],
        console: Console,
        width: int,
        inline_max: int,
    ) -> list[Text]:
        """Format each child of a container into its own Text.

        Args:
            container: A dict, list or tuple of values.
            console: The console that renders nested Rich renderables.
            width: The width available to the container.
            inline_max: The widest a child container may be and still sit on one line.
        """
        is_mapping = isinstance(container, dict)
        items = container.items() if is_mapping else enumerate(container)

        parts: list[Text] = []
        for key, value in items:
            child = cls.format(console, value, max(1, width - len(cls.INDENT)), inline_max)
            part = Text()
            if is_mapping:
                part.append(str(key), style=cls.KEY)
                part.append(":", style=cls.PUNCT)
                # Block renderables start on their own line, indented under the key.
                if "\n" in child.plain and cls.is_renderable(value):
                    part.append("\n")
                    part.append_text(cls.indent(child))
                    parts.append(part)
                    continue
                part.append(" ", style=cls.PUNCT)
            part.append_text(child)
            parts.append(part)
        return parts

    @classmethod
    def assemble(
        cls,
        parts: list[Text],
        open_char: str,
        close_char: str,
        inline_max: int,
        bare: bool,
    ) -> Text:
        """Join formatted parts, inline if short enough, otherwise one per line.

        Args:
            parts: The formatted children.
            open_char: The opening brace or bracket.
            close_char: The closing brace or bracket.
            inline_max: The widest the joined parts may be and still sit on one line.
            bare: Leave out the braces.
        """
        multiline = any("\n" in part.plain for part in parts)
        flat_width = sum(part.cell_len for part in parts) + 2 * len(parts) + 2

        if not multiline and flat_width <= inline_max:
            out = Text() if bare else Text(open_char, style=cls.PUNCT)
            for i, part in enumerate(parts):
                if i:
                    out.append(", ", style=cls.PUNCT)
                out.append_text(part)
            if not bare:
                out.append(close_char, style=cls.PUNCT)
            return out

        if bare:
            out = Text()
            for i, part in enumerate(parts):
                if i:
                    out.append("\n")
                out.append_text(part)
            return out

        out = Text(open_char + "\n", style=cls.PUNCT)
        for i, part in enumerate(parts):
            out.append_text(cls.indent(part))
            out.append(",\n" if i < len(parts) - 1 else "\n", style=cls.PUNCT)
        out.append(close_char, style=cls.PUNCT)
        return out

    @classmethod
    def format(
        cls,
        console: Console,
        value: object,
        width: int = 80,
        inline_max: int | None = None,
    ) -> Text:
        """Format any value into styled Text, braces included.

        Args:
            console: The console that renders nested Rich renderables.
            value: The value to format.
            width: The width available to the value.
            inline_max: The widest a container may be and still sit on one line; None uses ``INLINE_MAX``.
        """
        inline_max = cls.INLINE_MAX if inline_max is None else inline_max

        if isinstance(value, Text):
            return value.copy()
        if isinstance(value, str):
            return Text(f"{value!r}", style=cls.STR)
        if isinstance(value, bool):
            return Text(repr(value), style=cls.BOOL)
        if value is None:
            return Text("None", style=cls.NONE)
        if isinstance(value, (int, float)):
            return Text(repr(value), style=cls.NUM)
        if cls.is_renderable(value):
            return cls.flatten(cast("RenderableType", value), console, width)
        if not isinstance(value, (dict, list, tuple)):
            return Text(repr(value), style=cls.OTHER)

        container = cast("dict[object, object] | list[object] | tuple[object, ...]", value)
        open_char, close_char = ("{", "}") if isinstance(container, dict) else ("[", "]")
        if not container:
            return Text(open_char + close_char, style=cls.PUNCT)

        parts = cls.parts(container, console, width, inline_max)
        return cls.assemble(parts, open_char, close_char, inline_max, bare=False)

    @classmethod
    def format_root(
        cls,
        console: Console,
        value: object,
        width: int = 80,
        inline_max: int | None = None,
        braces: bool = False,
    ) -> Text:
        """Top-level entry point; strips the outermost braces by default.

        Args:
            console: The console that renders nested Rich renderables.
            value: The value to format.
            width: The width available to the value.
            inline_max: The widest a container may be and still sit on one line; None uses ``INLINE_MAX``.
            braces: Keep the outermost braces.
        """
        inline_max = cls.INLINE_MAX if inline_max is None else inline_max

        if braces or not isinstance(value, (dict, list, tuple)):
            return cls.format(console, value, width, inline_max)

        container = cast("dict[object, object] | list[object] | tuple[object, ...]", value)
        if not container:
            return Text()

        open_char, close_char = ("{", "}") if isinstance(container, dict) else ("[", "]")
        parts = cls.parts(container, console, width, inline_max)
        return cls.assemble(parts, open_char, close_char, inline_max, bare=True)
