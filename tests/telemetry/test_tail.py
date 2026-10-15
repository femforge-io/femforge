"""Tests for telemetry.tail: byte-offset reads, a held-back partial line, and truncation rewinding the cursor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from femforge.telemetry.tail import TextTail


if TYPE_CHECKING:
    from pathlib import Path


def test_a_missing_file_yields_nothing(tmp_path: Path) -> None:
    """A tail on a file that does not exist yet is empty, not an error."""
    tail = TextTail(tmp_path / "run.log")

    assert tail.read_new() == []
    assert tail.offset == 0


def test_only_new_bytes_are_returned(tmp_path: Path) -> None:
    """The cursor advances, so the second read sees only what was appended after the first."""
    path = tmp_path / "run.log"
    path.write_text("one\ntwo\n", encoding="utf-8")
    tail = TextTail(path)

    assert tail.read_new() == ["one", "two"]
    assert tail.read_new() == []

    with path.open("a", encoding="utf-8") as stream:
        _ = stream.write("three\n")

    assert tail.read_new() == ["three"]


def test_a_partial_last_line_is_held_back_until_it_ends(tmp_path: Path) -> None:
    """A line still being written is never handed over half-formed."""
    path = tmp_path / "run.log"
    path.write_text("done\npart", encoding="utf-8")
    tail = TextTail(path)

    assert tail.read_new() == ["done"]

    with path.open("a", encoding="utf-8") as stream:
        _ = stream.write("ial\n")

    assert tail.read_new() == ["partial"]


def test_carriage_returns_are_stripped(tmp_path: Path) -> None:
    """A file written with Windows line endings reads back as bare lines."""
    path = tmp_path / "run.log"
    _ = path.write_bytes(b"one\r\ntwo\r\n")
    tail = TextTail(path)

    assert tail.read_new() == ["one", "two"]


def test_a_truncated_file_rewinds_the_cursor(tmp_path: Path) -> None:
    """A shorter file means it was replaced, so the tail re-reads it from the start."""
    path = tmp_path / "run.log"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    tail = TextTail(path)
    _ = tail.read_new()

    path.write_text("fresh\n", encoding="utf-8")

    assert tail.read_new() == ["fresh"]
    assert tail.offset == path.stat().st_size


def test_undecodable_bytes_are_replaced_rather_than_raising(tmp_path: Path) -> None:
    """A solver log carrying stray bytes still tails."""
    path = tmp_path / "solver.log"
    _ = path.write_bytes(b"ok\xff\n")

    assert TextTail(path).read_new() == ["ok�"]
