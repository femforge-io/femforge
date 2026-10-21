"""The log panel: an xterm terminal fed line by line from the server loop, with a ring buffer replayed to clients that mount later."""

import asyncio
from collections import deque
from enum import StrEnum
from typing import TYPE_CHECKING

from trame.widgets import client, html, react

from femforge_workbench.panels import Cls, PanelComponent
from femforge_workbench.widgets.button import Button
from femforge_workbench.widgets.icon import Icon
from femforge_workbench.widgets.term import Term
from femforge_workbench.widgets.tip import Tip


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


SCROLLBACK = 5000
FLUSH_DELAY = 0.05
"""Seconds the lines of one burst are gathered before one write carries them all."""

NEWLINE = "\r\n"
BUTTON_SIZE = "icon-xs"
MOUNTED = "mounted"
"""The one life-cycle event the panels listen for: a client just mounted the subtree."""


class StateVar(StrEnum):
    """The per-panel state variables the terminal binds."""

    OPTIONS = "options"
    COUNT = "count"


def mounted(handler: react.Callback) -> None:
    """Calls `handler` whenever a client mounts the subtree this is built in; the terminal has no mount event of its own."""
    _ = client.LifeCycleMonitor(type="emit", events=[MOUNTED], mounted=handler)


class LogPanel(PanelComponent):
    """Shows lines as they arrive; `write` is called on the server loop, the clients see them within a flush delay."""

    def __init__(self, app: "Workbench", id: str, title: str, *, scrollback: int = SCROLLBACK) -> None:
        """Builds the terminal and the buffer replayed to it.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            scrollback: How many lines the buffer and the terminal keep.
        """
        self.scrollback = scrollback
        self.lines: deque[str] = deque(maxlen=scrollback)
        self.pending: list[str] = []
        self.flush_handle: asyncio.TimerHandle | None = None
        self.terminal: Term | None = None
        self.follow = False
        """Whether the next flush scrolls to the end: set by a clear, whose bulk refill xterm.js does not follow on its own."""

        super().__init__(app, id, title)

    def build(self) -> None:
        """Builds the toolbar over the read-only terminal."""
        with html.Div(classes=str(Cls.TOOLBAR)):
            html.Span([react.Bind(self.key(StateVar.COUNT)), " lines"], classes=str(Cls.CAPTION))
            html.Span(classes=str(Cls.SPACER))
            with (
                Tip(content="Scroll to the end"),
                Button(
                    size=BUTTON_SIZE,
                    variant="ghost",
                    aria_label="Scroll to the end",
                    on_click=self.callback(self.scroll_bottom),
                ),
            ):
                Icon("chevron-down")

            with Tip(content="Clear"), Button(size=BUTTON_SIZE, variant="ghost", aria_label="Clear", on_click=self.callback(self.clear)):
                Icon("trash-2")

        with html.Div(classes=str(Cls.FILL)):
            self.terminal = Term(options=react.Bind(self.key(StateVar.OPTIONS)))
            mounted(self.callback(self.on_opened))

        self.state[self.key(StateVar.OPTIONS)] = self.options()
        self.state[self.key(StateVar.COUNT)] = len(self.lines)

    def options(self) -> dict[str, object]:
        """xterm options: read-only, palette colours, the scrollback this panel keeps."""
        return {
            "disableStdin": True,
            "convertEol": True,
            "scrollback": self.scrollback,
            "cursorInactiveStyle": "none",
            "theme": self.app.palette.xterm_theme(),
        }

    def text(self) -> str:
        """Every buffered line, newline separated."""
        return "\n".join(self.lines)

    def write(self, line: str) -> None:
        """Appends a line, pushed to the clients with the others of the same burst."""
        self.lines.append(line)
        self.pending.append(line)

        if self.flush_handle is None and self.app.loop is not None:
            self.flush_handle = self.app.loop.call_later(FLUSH_DELAY, self.flush)

    def flush(self) -> None:
        """Writes the burst's lines to the terminal, scrolling to the end after a clear."""
        self.flush_handle = None
        if self.terminal is None or not self.pending:
            return

        text = NEWLINE.join(self.pending) + NEWLINE
        self.pending = []
        self.terminal.write(text)
        if self.follow:
            self.follow = False
            self.terminal.scroll_to_bottom()

        with self.state:
            self.state[self.key(StateVar.COUNT)] = len(self.lines)

    def clear(self) -> None:
        """Empties the buffer and the terminal."""
        self.lines.clear()
        self.pending = []
        self.follow = True
        if self.terminal is not None:
            self.terminal.clear()

        with self.state:
            self.state[self.key(StateVar.COUNT)] = 0

    def fit(self) -> None:
        """Fits the terminal to its panel."""
        if self.terminal is not None:
            self.terminal.fit()

    def scroll_bottom(self) -> None:
        """Scrolls the terminal to the newest line."""
        if self.terminal is not None:
            self.terminal.scroll_to_bottom()

    def on_opened(self) -> None:
        """A client's terminal just mounted: it gets the buffer so far."""
        if self.terminal is not None and self.lines:
            self.terminal.write(NEWLINE.join(self.lines) + NEWLINE)

        self.fit()

    def on_layout_changed(self) -> None:
        """Fits the terminal after the dock re-laid out."""
        self.fit()

    def on_visible(self, visible: bool) -> None:
        """Fits the terminal when the panel comes on screen."""
        if visible:
            self.fit()
