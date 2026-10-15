"""Tests for telemetry.context: the sweep point contextvar and the ids it binds onto log records."""

from __future__ import annotations

from femforge.telemetry.context import SweepPoint, current_sweep_point, sweep_point


def test_no_sweep_point_outside_a_block() -> None:
    """Ordinary code reports no sweep point at all."""
    assert current_sweep_point() is None


def test_the_block_publishes_its_point_and_restores_the_previous_one() -> None:
    """Entering sets the point, leaving puts back whatever was in scope, so nesting works."""
    with sweep_point("sweep-a", "point-1") as outer:
        assert current_sweep_point() == SweepPoint("sweep-a", "point-1")
        assert outer.sweep_id == "sweep-a"

        with sweep_point("sweep-b", "point-2"):
            assert current_sweep_point() == SweepPoint("sweep-b", "point-2")

        assert current_sweep_point() == SweepPoint("sweep-a", "point-1")

    assert current_sweep_point() is None


def test_the_point_is_restored_after_a_failure() -> None:
    """A raising point must not leak its ids into the next one."""
    try:
        with sweep_point("sweep-a", "point-1"):
            raise RuntimeError("solve failed")
    except RuntimeError:
        pass

    assert current_sweep_point() is None
