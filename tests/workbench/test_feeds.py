"""QueueFeed hands everything queued to one publish, starts once and unsubscribes on stop."""

import asyncio
from typing import cast

from trame.app import get_server

from femforge_workbench.feeds import QueueFeed
from femforge_workbench.panels import PanelComponent


class _Owner:
    """The id and state a feed publishes into."""

    def __init__(self) -> None:
        self.id = "feed"
        self.state = get_server("queue-feed", client_type="react").state


def test_items_queued_together_publish_as_one_burst() -> None:
    queue: asyncio.Queue[int] = asyncio.Queue()
    dropped: list[asyncio.Queue[int]] = []
    bursts: list[list[int]] = []

    def subscribe(_: asyncio.AbstractEventLoop) -> asyncio.Queue[int]:
        for item in (1, 2, 3):
            queue.put_nowait(item)

        return queue

    async def scenario() -> None:
        feed = QueueFeed(cast("PanelComponent", _Owner()), subscribe, dropped.append, bursts.append)
        feed.start()
        task = feed.task
        feed.start()
        assert feed.task is task

        while feed.bursts == 0:
            await asyncio.sleep(0.01)

        feed.stop()
        feed.stop()

    asyncio.run(scenario())

    assert bursts == [[1, 2, 3]] and dropped == [queue]
