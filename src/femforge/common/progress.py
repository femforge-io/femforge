"""Progress UI: live per-step elapsed-time rows rendered on the shared console."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.console import Console, RichCast
from rich.live import Live
from rich.text import Text

from femforge.common.log import get_console


if TYPE_CHECKING:
    from types import TracebackType


def fmt_time(seconds: float, width: int = 6) -> str:
    """Format a duration in seconds as ``m:ss.s`` or ``s.s``, right-justified to ``width``."""
    minutes = int(seconds // 60)
    seconds = seconds % 60
    if minutes > 0:
        return f"{minutes}:{seconds:04.1f}".rjust(width)
    return f"{seconds:.1f}".rjust(width)


class TimerRenderable(RichCast):
    """Live-updating timer row; Rich calls ``__rich__`` on every refresh tick."""

    def __init__(self, title: str, msg: str) -> None:
        """Start timing the step ``title``, shown with the status ``msg``."""
        self.title = title
        self.message = msg
        self.start_time = time.time()
        self.completed = False

    def elapsed(self) -> float:
        """Seconds since the timer started."""
        return time.time() - self.start_time

    def elapsed_str(self) -> str:
        """The elapsed time as [`fmt_time`][femforge.common.progress.fmt_time] formats it."""
        return fmt_time(self.elapsed())

    def __rich__(self) -> Text:
        status_str = "  " if self.completed else "➡️ "
        message_str = f" {self.message}" if self.message else ""
        time_str = self.elapsed_str()
        title_str = self.title.ljust(22)
        return Text.from_markup(f"{status_str} [magenta][ {time_str} ][/magenta] [cyan][ {title_str} ][/cyan]{message_str}")


class StepTimer(Live):
    """Context manager showing a live elapsed-time line for one pipeline step."""

    def __init__(self, title: str, msg: str = "", console: Console | None = None) -> None:
        """Prepare the live line for one step.

        Args:
            title: The step name.
            msg: A status message shown after the step name.
            console: The console to render on; None uses the shared one.
        """
        self._timer_renderable = TimerRenderable(title, msg)
        super().__init__(
            self._timer_renderable,
            console=console if console is not None else get_console(),
            refresh_per_second=4,
            auto_refresh=True,
        )

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None) -> None:
        # Flip before the final render so it shows the completed marker, not the running arrow.
        self._timer_renderable.completed = True
        super().__exit__(exc_type, exc_val, exc_tb)
