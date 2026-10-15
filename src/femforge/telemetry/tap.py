"""An in-process live view of the log stream: a bounded ring buffer plus asyncio fan-out to the workbench."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from femforge.telemetry.sink import EXTRA_WIDTH, default_process, log_row, sanitize_process


if TYPE_CHECKING:
    from asyncio import AbstractEventLoop, Queue

    from loguru import Message


__all__ = ["LiveLogTap"]

BUFFER_SIZE = 5000
"""How many recent records the tap keeps for a subscriber that attaches late."""

QUEUE_SIZE = 1000
"""A subscriber queue this deep drops rather than blocking the logging thread."""

LogRow = dict[str, object]
"""One log record flattened onto the log table's columns."""


class LiveLogTap:
    """Loguru sink keeping the last records in memory and pushing each new one onto every asyncio subscriber queue."""

    def __init__(self, *, process: str | None = None, maxlen: int = BUFFER_SIZE) -> None:
        """Keep the last ``maxlen`` records, each tagged with ``process`` or, when None, the script's name."""
        self.process: str = sanitize_process(process) if process is not None else default_process()

        self._buffer: deque[LogRow] = deque(maxlen=maxlen)
        self._console: Console = Console(file=StringIO(), width=EXTRA_WIDTH)
        self._lock: threading.Lock = threading.Lock()
        self._subscribers: list[tuple[AbstractEventLoop, Queue[LogRow]]] = []

    def __call__(self, message: Message) -> None:
        """The loguru sink protocol; buffer the record, then hand it to each subscriber's loop."""

        def deliver(queue: Queue[LogRow], row: LogRow) -> None:
            try:
                queue.put_nowait(row)
            except asyncio.QueueFull:
                pass

        row = log_row(message.record, process=self.process, console=self._console)

        with self._lock:
            self._buffer.append(row)
            targets = list(self._subscribers)

        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(deliver, queue, row)
            except RuntimeError:
                self.unsubscribe(queue)

    def snapshot(self) -> list[LogRow]:
        """The buffered records, oldest first — what a subscriber renders before its first live record arrives."""
        with self._lock:
            return list(self._buffer)

    def subscribe(self, loop: AbstractEventLoop, *, maxsize: int = QUEUE_SIZE) -> Queue[LogRow]:
        """Register a queue fed on ``loop`` and return it; the caller passes it back to ``unsubscribe``."""
        queue: Queue[LogRow] = asyncio.Queue(maxsize=maxsize)

        with self._lock:
            self._subscribers.append((loop, queue))

        return queue

    def unsubscribe(self, queue: Queue[LogRow]) -> None:
        """Stop feeding ``queue``; a queue that was never registered is ignored."""
        with self._lock:
            self._subscribers = [entry for entry in self._subscribers if entry[1] is not queue]
