"""Incremental reads of a growing text file, so a viewer re-reads only the bytes it has not seen."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path


__all__ = ["TextTail"]


class TextTail:
    """A byte-offset cursor over one growing text file; a truncation rewinds it and an unterminated last line is held back until it ends."""

    def __init__(self, path: Path) -> None:
        """Start a cursor at the beginning of ``path``."""
        self.path: Path = path
        self.offset: int = 0
        self._partial: str = ""

    def reset(self) -> None:
        """Forget the cursor so the next read starts from the beginning of the file."""
        self.offset = 0
        self._partial = ""

    def read_new(self) -> list[str]:
        """Every complete line written since the last call, newline stripped; empty when the file is absent or unchanged."""
        if not self.path.is_file():
            return []

        size = self.path.stat().st_size
        if size < self.offset:
            self.reset()

        with self.path.open("rb") as stream:
            _ = stream.seek(self.offset)
            chunk = stream.read()
            self.offset = stream.tell()

        if not chunk:
            return []

        text = self._partial + chunk.decode("utf-8", errors="replace")
        lines = text.split("\n")
        self._partial = lines.pop()

        return [line.removesuffix("\r") for line in lines]
