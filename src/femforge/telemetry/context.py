"""The sweep point a thread or task is working on, carried on a contextvar and bound onto every log record it emits."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, NamedTuple

from femforge.common.log import log
from femforge.telemetry.schemas import LogColumn


if TYPE_CHECKING:
    from collections.abc import Iterator


__all__ = ["SweepPoint", "current_sweep_point", "sweep_point"]


class SweepPoint(NamedTuple):
    """Which sweep, and which point of it, the current context belongs to."""

    sweep_id: str
    """The sweep's id."""

    point_key: str
    """The point's key within the sweep."""


_current: ContextVar[SweepPoint | None] = ContextVar("femforge_sweep_point", default=None)


def current_sweep_point() -> SweepPoint | None:
    """The sweep point in scope, or None outside any ``sweep_point`` block."""
    return _current.get()


@contextmanager
def sweep_point(sweep_id: str, point_key: str) -> Iterator[SweepPoint]:
    """Mark this context as one sweep point, binding both ids onto every log record emitted inside it."""
    point = SweepPoint(sweep_id, point_key)
    token = _current.set(point)

    bound = {LogColumn.SWEEP_ID: sweep_id, LogColumn.POINT_KEY: point_key}
    try:
        with log.contextualize(**bound):
            yield point
    finally:
        _current.reset(token)
