"""Tests for common.rich_sink RichSink rendering and ContextFormatter styling."""

from __future__ import annotations

import re
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.tree import Tree

from femforge.common.log import log
from femforge.common.rich_sink import ContextFormatter, RichSink


if TYPE_CHECKING:
    from collections.abc import Callable


def _capture(sink: RichSink, buf: StringIO, emit: Callable[[], None]) -> str:
    sink_id = log.add(sink, format="{message}", colorize=False, backtrace=True, diagnose=True, level="TRACE")
    try:
        emit()
    finally:
        log.remove(sink_id)
    return buf.getvalue()


def _sink_console() -> tuple[Console, StringIO]:
    buf = StringIO()
    # force_terminal=False: a FORCE_COLOR env var would otherwise push ANSI escapes into the captured text.
    return Console(file=buf, width=300, force_terminal=False), buf


def test_message_renders() -> None:
    """The message text appears on the rendered row."""
    console, buf = _sink_console()
    out = _capture(RichSink(console), buf, lambda: log.warning("warn msg"))
    assert "warn msg" in out


def test_default_console_is_stderr() -> None:
    """Without an injected console the sink builds its own stderr Console."""
    sink = RichSink()
    assert sink.console.stderr


def test_extras_render_as_key_value() -> None:
    """bind() extras render in the context column as key: value pairs."""
    console, buf = _sink_console()
    out = _capture(RichSink(console), buf, lambda: log.bind(request_id="abc123").info("with extra"))
    assert "request_id: 'abc123'" in out


def test_hide_keys_suppresses_named_extras() -> None:
    """hide_keys extras are dropped while the rest still render."""
    console, buf = _sink_console()
    sink = RichSink(console, hide_keys=frozenset({"run_id"}))
    out = _capture(sink, buf, lambda: log.bind(run_id="r1", step="mesh").info("busy"))
    assert "run_id" not in out
    assert "step: 'mesh'" in out


def test_hide_keys_can_hide_all_extras() -> None:
    """When every extra is hidden no context column is rendered at all."""
    console, buf = _sink_console()
    sink = RichSink(console, hide_keys=frozenset({"run_id"}))
    out = _capture(sink, buf, lambda: log.bind(run_id="r1").info("solo"))
    assert "run_id" not in out
    assert "r1" not in out


def test_markup_opt_in() -> None:
    """markup=True parses Rich markup in the message; the default keeps brackets literal."""
    console, buf = _sink_console()
    out = _capture(RichSink(console, markup=True), buf, lambda: log.info("[bold]styled[/bold]"))
    assert "styled" in out
    assert "[bold]" not in out

    console2, buf2 = _sink_console()
    out2 = _capture(RichSink(console2), buf2, lambda: log.info("[bold]literal[/bold]"))
    assert "[bold]literal[/bold]" in out2


def test_path_column_pads_short_locations() -> None:
    """The name:function location renders inside a path_width column, leaving a gap before the message."""
    console, buf = _sink_console()

    def emit_padded() -> None:
        log.info("padded")

    out = _capture(RichSink(console, path_width=80), buf, emit_padded)
    assert "emit_padded" in out
    location_and_gap = re.search(r"emit_padded(\s+)padded", out)
    assert location_and_gap is not None
    assert len(location_and_gap.group(1)) > 1


def test_path_column_truncates_keeping_tail() -> None:
    """A location longer than path_width is left-truncated (… prefix) so the function name survives."""
    console, buf = _sink_console()

    def emit_with_a_deliberately_long_function_name() -> None:
        log.info("truncated")

    out = _capture(RichSink(console, path_width=20), buf, emit_with_a_deliberately_long_function_name)
    assert "…" in out
    assert "test_rich_sink" not in out  # the module head was truncated away
    assert "function_name" in out  # the informative tail survives


def test_path_truncation_can_eat_the_colon() -> None:
    """A path_width shorter than the function name leaves a bare tail with no name:function colon."""
    console, buf = _sink_console()

    def another_long_function_name() -> None:
        log.info("bare tail")

    out = _capture(RichSink(console, path_width=8), buf, another_long_function_name)
    assert "…" in out
    assert "bare tail" in out


def test_path_column_is_fixed_width() -> None:
    """Two records from different-length locations start their messages at the same column."""
    console, buf = _sink_console()
    width = 30

    def short() -> None:
        log.info("MSG1")

    def a_rather_longer_function_name_here() -> None:
        log.info("MSG2")

    _ = _capture(RichSink(console, path_width=width), buf, short)
    _ = _capture(RichSink(console, path_width=width), buf, a_rather_longer_function_name_here)

    lines = [line for line in buf.getvalue().splitlines() if "MSG" in line]
    assert len(lines) == 2
    assert lines[0].index("MSG1") == lines[1].index("MSG2")


def test_show_path_disabled() -> None:
    """show_path=False omits the name:function column."""
    console, buf = _sink_console()
    out = _capture(RichSink(console, show_path=False), buf, lambda: log.info("bare"))
    assert "test_rich_sink" not in out


def test_time_format_centisecond_trim() -> None:
    """The default %f format is trimmed from microseconds to centiseconds."""
    console, buf = _sink_console()
    out = _capture(RichSink(console), buf, lambda: log.info("timed"))
    assert re.search(r"^\s*\d{2}:\d{2}:\d{2}\.\d{2}\s", out)


def test_time_format_custom() -> None:
    """A format not ending in %f is used verbatim."""
    console, buf = _sink_console()
    out = _capture(RichSink(console, time_format="%H:%M"), buf, lambda: log.info("timed"))
    assert re.search(r"^\s*\d{2}:\d{2}\s", out)


def _boom() -> None:
    try:
        _ = 1 / 0
    except ZeroDivisionError:
        log.exception("math is hard")


def test_exception_renders_rich_traceback() -> None:
    """log.exception renders a Rich traceback after the message row."""
    console, buf = _sink_console()
    out = _capture(RichSink(console), buf, _boom)
    assert "math is hard" in out
    assert "ZeroDivisionError" in out


def test_exception_traceback_opt_out() -> None:
    """rich_tracebacks=False prints the message row only."""
    console, buf = _sink_console()
    out = _capture(RichSink(console, rich_tracebacks=False), buf, _boom)
    assert "math is hard" in out
    assert "ZeroDivisionError" not in out


def _console() -> Console:
    return Console(width=80, force_terminal=False)


def test_format_scalars() -> None:
    """Each scalar kind gets its own repr and style."""
    console = _console()
    assert ContextFormatter.format(console, "x").plain == "'x'"
    assert ContextFormatter.format(console, 42).plain == "42"
    assert ContextFormatter.format(console, 2.5).plain == "2.5"
    assert ContextFormatter.format(console, None).plain == "None"

    flag = ContextFormatter.format(console, True)
    assert flag.plain == "True"
    assert flag.style == ContextFormatter.BOOL  # bool wins over its int-ness


def test_format_text_passthrough_copies() -> None:
    """A rich Text value passes through as an independent copy."""
    console = _console()
    original = Text("styled", style="bold")
    result = ContextFormatter.format(console, original)
    assert result.plain == "styled"
    assert result is not original


def test_format_arbitrary_object_uses_repr() -> None:
    """A non-data, non-renderable value falls back to repr."""
    console = _console()

    class Widget:
        def __repr__(self) -> str:
            return "<Widget>"

    result = ContextFormatter.format(console, Widget())
    assert result.plain == "<Widget>"
    assert result.style == ContextFormatter.OTHER


def test_format_empty_containers() -> None:
    """Empty dict and list render as bare braces."""
    console = _console()
    assert ContextFormatter.format(console, {}).plain == "{}"
    assert ContextFormatter.format(console, []).plain == "[]"


def test_format_inline_containers() -> None:
    """Short containers render inline with braces and comma separators."""
    console = _console()
    assert ContextFormatter.format(console, {"a": 1, "b": 2}).plain == "{a: 1, b: 2}"
    assert ContextFormatter.format(console, [1, 2]).plain == "[1, 2]"
    assert ContextFormatter.format(console, (1, 2)).plain == "[1, 2]"


def test_format_long_dict_goes_multiline() -> None:
    """A container wider than inline_max renders one entry per line, indented inside braces."""
    console = _console()
    value = {"first": "x" * 40, "second": "y" * 40}
    plain = ContextFormatter.format(console, value).plain
    lines = plain.splitlines()
    assert lines[0] == "{"
    assert lines[-1] == "}"
    assert any(line.startswith(f"{ContextFormatter.INDENT}first:") for line in lines)
    assert any(line.endswith(",") for line in lines)


def test_format_renderable_flattens() -> None:
    """A Rich renderable nested as a value is rendered down to styled text lines."""
    console = _console()
    tree = Tree("root")
    _ = tree.add("leaf")
    plain = ContextFormatter.format(console, tree).plain
    assert "root" in plain
    assert "leaf" in plain
    assert "\n" in plain


def test_format_renderable_wraps_to_width() -> None:
    """A renderable wider than the given width is wrapped, not truncated."""
    console = _console()
    plain = ContextFormatter.format(console, Panel("word " * 20), width=30).plain
    assert all(len(line) <= 30 for line in plain.splitlines())


def test_block_renderable_under_key_indents() -> None:
    """A multi-line renderable inside a dict starts on its own line, indented under its key."""
    console = _console()
    tree = Tree("root")
    _ = tree.add("leaf")
    plain = ContextFormatter.format_root(console, {"shape": tree}).plain
    lines = plain.splitlines()
    assert lines[0] == "shape:"
    assert all(line.startswith(ContextFormatter.INDENT) for line in lines[1:])


def test_format_root_strips_braces() -> None:
    """The root container renders bare: no outer braces, comma-joined when inline."""
    console = _console()
    assert ContextFormatter.format_root(console, {"a": 1, "b": 2}).plain == "a: 1, b: 2"


def test_format_root_multiline_joins_bare_lines() -> None:
    """A long root container renders one entry per line with no braces or indent."""
    console = _console()
    value = {"first": "x" * 40, "second": "y" * 40}
    lines = ContextFormatter.format_root(console, value).plain.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("first:")
    assert lines[1].startswith("second:")


def test_format_root_braces_opt_in() -> None:
    """braces=True keeps the outermost braces on the root container."""
    console = _console()
    assert ContextFormatter.format_root(console, {"a": 1}, braces=True).plain == "{a: 1}"


def test_format_root_non_container_and_empty() -> None:
    """A scalar root formats like any value; an empty root renders nothing."""
    console = _console()
    assert ContextFormatter.format_root(console, 5).plain == "5"
    assert ContextFormatter.format_root(console, {}).plain == ""


def test_indent_prefixes_every_line() -> None:
    """indent() pads each line of a multi-line Text."""
    result = ContextFormatter.indent(Text("a\nb"), levels=2)
    pad = ContextFormatter.INDENT * 2
    assert result.plain == f"{pad}a\n{pad}b"
