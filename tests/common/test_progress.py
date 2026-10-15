"""Tests for common.progress timers."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from femforge.common.log import _reset_for_tests, get_console
from femforge.common.progress import StepTimer, TimerRenderable, fmt_time


def test_fmt_time_seconds_only() -> None:
    """Sub-minute durations render as seconds, right-justified."""
    assert fmt_time(5.25) == "   5.2"


def test_fmt_time_with_minutes() -> None:
    """Durations over a minute render as m:ss.s."""
    assert fmt_time(65.0) == "1:05.0"


def test_timer_renderable_running() -> None:
    """While running the row shows the arrow, title, and message."""
    tr = TimerRenderable("Meshing", "coarse pass")
    assert tr.elapsed() >= 0
    plain = tr.__rich__().plain
    assert "Meshing" in plain
    assert "coarse pass" in plain
    assert "➡" in plain


def test_timer_renderable_completed_and_no_message() -> None:
    """Completion drops the arrow, and an empty message adds no tail."""
    tr = TimerRenderable("Solve", "")
    tr.completed = True
    plain = tr.__rich__().plain
    assert "Solve" in plain
    assert "➡" not in plain
    assert plain.rstrip().endswith("]")


def test_step_timer_marks_completed_on_exit() -> None:
    """Exiting the context flips the renderable to completed and prints the row."""
    buf = StringIO()
    console = Console(file=buf, width=300)
    with StepTimer("stepname", "working", console=console) as timer:
        assert not timer._timer_renderable.completed
    assert timer._timer_renderable.completed
    assert "stepname" in buf.getvalue()


def test_step_timer_defaults_to_shared_console() -> None:
    """Without an explicit console StepTimer uses get_console()."""
    _reset_for_tests()
    try:
        timer = StepTimer("shared")
        assert timer.console is get_console()
    finally:
        _reset_for_tests()
