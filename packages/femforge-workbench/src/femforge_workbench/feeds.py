"""A panel's refresh loop: one task per panel, polling off the server loop and publishing on it."""

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from trame_server.utils.asynchronous import create_task

from femforge.common.log import log


if TYPE_CHECKING:
    from femforge_workbench.panels import PanelComponent


class Feed[T]:
    """Runs ``poll`` in a worker thread every ``period`` seconds and hands what it returns to ``publish`` inside a state context."""

    def __init__(self, panel: "PanelComponent", period: float, poll: Callable[[], T], publish: Callable[[T], None]) -> None:
        """Sets the loop up stopped; `start` runs it.

        Args:
            panel: The panel whose state the publish runs in.
            period: Seconds between polls.
            poll: Reads what changed, off the server loop.
            publish: Writes a poll's result into the panel, on the server loop.
        """
        self.panel = panel
        self.period = period
        self.poll = poll
        self.publish = publish
        self.task: asyncio.Task[None] | None = None
        self.polls = 0
        """How many polls have completed; a panel shows it as its refresh count and the tests wait on it."""

    @property
    def running(self) -> bool:
        """Whether the loop runs."""
        return self.task is not None and not self.task.done()

    def start(self) -> None:
        """Starts the loop unless it already runs; the first poll happens immediately."""
        if self.running:
            return

        self.task = create_task(self.cycle())

        log.bind(panel=self.panel.id, period=self.period).info("Feed started.")

    def stop(self) -> None:
        """Cancels the loop, so a hidden panel costs nothing."""
        if self.task is None:
            return

        _ = self.task.cancel()
        self.task = None

        log.bind(panel=self.panel.id, polls=self.polls).info("Feed stopped.")

    async def cycle(self) -> None:
        """Poll, publish, sleep — until the task is cancelled; a failed poll is logged and the loop carries on."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                value: T = await loop.run_in_executor(None, self.poll)
            except Exception:
                log.opt(exception=True).warning("Feed poll failed.")
                await asyncio.sleep(self.period)
                continue

            self.polls += 1
            with self.panel.state:
                self.publish(value)

            await asyncio.sleep(self.period)


class QueueFeed[T]:
    """Drains an asyncio queue another party feeds on the server loop, handing each burst of items to ``publish`` inside a state context."""

    def __init__(
        self,
        panel: "PanelComponent",
        subscribe: Callable[[asyncio.AbstractEventLoop], asyncio.Queue[T]],
        unsubscribe: Callable[[asyncio.Queue[T]], None],
        publish: Callable[[list[T]], None],
    ) -> None:
        """Sets the drain up stopped; `start` subscribes and runs it.

        Args:
            panel: The panel whose state the publish runs in.
            subscribe: Hands out a queue fed on the given loop.
            unsubscribe: Stops feeding a queue `subscribe` handed out.
            publish: Writes a burst of items into the panel, on the server loop.
        """
        self.panel = panel
        self.subscribe = subscribe
        self.unsubscribe = unsubscribe
        self.publish = publish
        self.task: asyncio.Task[None] | None = None
        self.queue: asyncio.Queue[T] | None = None
        self.bursts = 0
        """How many bursts have been published; the tests wait on it."""

    @property
    def running(self) -> bool:
        """Whether the drain runs."""
        return self.task is not None and not self.task.done()

    def start(self) -> None:
        """Subscribes and starts draining unless it already runs."""
        if self.running:
            return

        self.task = create_task(self.cycle())

        log.bind(panel=self.panel.id).info("Queue feed started.")

    def stop(self) -> None:
        """Cancels the drain and unsubscribes, so a hidden panel is no longer fed."""
        if self.task is None:
            return

        _ = self.task.cancel()
        self.task = None
        if self.queue is not None:
            self.unsubscribe(self.queue)
            self.queue = None

        log.bind(panel=self.panel.id, bursts=self.bursts).info("Queue feed stopped.")

    async def cycle(self) -> None:
        """Wait for an item, take every other one already queued, publish them together — until the task is cancelled."""
        queue = self.subscribe(asyncio.get_running_loop())
        self.queue = queue
        while True:
            batch: list[T] = [await queue.get()]
            while not queue.empty():
                batch.append(queue.get_nowait())

            self.bursts += 1
            with self.panel.state:
                self.publish(batch)
