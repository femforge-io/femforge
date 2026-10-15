"""A geometry authored as a build123d tagged assembly."""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from femforge.geometry.config import GeometryArea


if TYPE_CHECKING:
    from femforge.geometry.tags import TaggedCompound


class Build123dGeometry(GeometryArea):
    """A [`GeometryArea`][] whose STEP is the export of [`model`][], the tagged assembly in the authored frame."""

    @abstractmethod
    def model(self) -> TaggedCompound:
        """The tagged assembly in the authored frame."""

    def build_step(self, path: Path) -> Path:
        """Export [`model`][] as a STEP at ``path`` and return it."""
        self.model().export_step(str(path))
        return path
