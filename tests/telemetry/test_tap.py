"""Tests for telemetry.tap: the bounded ring buffer and fan-out onto asyncio subscriber queues."""

from __future__ import annotations

import asyncio
import logging
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from femforge.common.log import LogLevel, _reset_for_tests, log, setup_logging
from femforge.telemetry.schemas import LogColumn
from femforge.telemetry.tap import LiveLogTap


if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def clean_logging() -> Iterator[None]:
    """Snapshot stdlib root logging and restore it plus common.log module state around each test."""
    prev_handlers = logging.root.handlers[:]
    prev_level = logging.root.level
    _reset_for_tests()
    yield
    _reset_for_tests()
    logging.root.handlers[:] = prev_handlers
    logging.root.setLevel(prev_level)


@pytest.fixture
def tap() -> Iterator[LiveLogTap]:
    """A tap attached to loguru at DEBUG, rendering the terminal into a buffer."""
    setup_logging(level=LogLevel.INFO, console=Console(file=StringIO(), width=300))
    attached = LiveLogTap(process="workbench", maxlen=3)
    sink_id = log.add(
        attached,
        format="{message}",
        colorize=False,
        level=LogLevel.DEBUG,
    )
    yield attached
    log.remove(sink_id)


@pytest.mark.usefixtures("clean_logging")
def test_the_buffer_keeps_the_most_recent_records(tap: LiveLogTap) -> None:
    """A late subscriber sees recent history, bounded by maxlen."""
    for index in range(5):
        log.info("record {}", index)

    messages = [row[LogColumn.MESSAGE] for row in tap.snapshot()]

    assert messages == ["record 2", "record 3", "record 4"]


@pytest.mark.usefixtures("clean_logging")
def test_the_rows_carry_the_taps_process_name(tap: LiveLogTap) -> None:
    """The tap builds the same row shape as the parquet sink, under its own process name."""
    log.info("shaped")

    row = tap.snapshot()[0]

    assert row[LogColumn.PROCESS] == "workbench"
    assert row[LogColumn.LEVEL] == LogLevel.INFO


@pytest.mark.usefixtures("clean_logging")
def test_a_subscriber_receives_records_on_its_own_loop(tap: LiveLogTap) -> None:
    """Records logged from another thread arrive in the loop's queue via call_soon_threadsafe."""

    async def collect() -> list[object]:
        loop = asyncio.get_running_loop()
        queue = tap.subscribe(loop)

        await asyncio.to_thread(log.info, "from a worker thread")

        row = await asyncio.wait_for(queue.get(), timeout=5.0)
        tap.unsubscribe(queue)

        return [row[LogColumn.MESSAGE]]

    assert asyncio.run(collect()) == ["from a worker thread"]


@pytest.mark.usefixtures("clean_logging")
def test_unsubscribing_stops_delivery(tap: LiveLogTap) -> None:
    """A closed panel stops being fed, and unsubscribing an unknown queue is harmless."""

    async def drop() -> bool:
        loop = asyncio.get_running_loop()
        queue = tap.subscribe(loop)
        tap.unsubscribe(queue)
        tap.unsubscribe(asyncio.Queue())

        await asyncio.to_thread(log.info, "unheard")
        await asyncio.sleep(0)

        return queue.empty()

    assert asyncio.run(drop())


@pytest.mark.usefixtures("clean_logging")
def test_a_full_subscriber_queue_drops_rather_than_blocking(tap: LiveLogTap) -> None:
    """A stalled subscriber must never stall the thread that is logging."""

    async def overflow() -> int:
        loop = asyncio.get_running_loop()
        queue = tap.subscribe(loop, maxsize=1)

        for index in range(3):
            await asyncio.to_thread(log.info, "record {}", index)
            await asyncio.sleep(0)

        tap.unsubscribe(queue)

        return queue.qsize()

    assert asyncio.run(overflow()) == 1


@pytest.mark.usefixtures("clean_logging")
def test_a_dead_loop_unsubscribes_itself(tap: LiveLogTap) -> None:
    """A workbench that has gone away is dropped instead of raising into the logging call."""

    async def register() -> asyncio.Queue[dict[str, object]]:
        return tap.subscribe(asyncio.get_running_loop())

    queue = asyncio.run(register())
    log.info("after the loop closed")

    assert queue.empty()
    assert tap.snapshot()[-1][LogColumn.MESSAGE] == "after the loop closed"
