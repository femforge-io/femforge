"""The parametric-geometry config area: a geometry the driver materializes into a STEP before meshing."""

from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict

from femforge.common.config import ConfigArea


class GeometryArea(ConfigArea):
    """A parametric geometry the driver builds into a STEP in the authored frame; ``config.frame`` converts it for the solve."""

    # extra="allow" keeps a subclass's parameters (the run identity hashes them) readable when a manifest loads back as this base.
    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True, extra="allow")

    def build_step(self, path: Path) -> Path:
        """Write the geometry as a STEP at ``path``, in the authored frame, and return it."""
        raise NotImplementedError
