"""The mesh artifact's spec: the plain data that decides the Elmer mesh files and their group names."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from femforge.common.tags import TagDict
from femforge.meshing.gmsh_options import GmshOptions


class TagSelectorSpec(BaseModel):
    """The grouping-relevant half of an ``EntityTag``: its selector and per-region size, never its conditions."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    selector: TagDict = Field(default_factory=dict)
    """The key=value pairs an entity must carry to join the group."""

    mesh_size: float | None = None
    """`h` `[m]`: The per-region element size, when set."""


class MeshSpec(BaseModel):
    """Everything that decides the Elmer mesh files and their group names, and nothing that only decides the solve."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    step_key: str
    """The geometry identity (``step_key``)."""

    model_dim: Literal[2, 3]
    """Body dimension of the STEP, from the frame: 2D cross-section or 3D solids."""

    global_mesh_size: float
    """`h` `[m]`: Domain-wide target element size."""

    gmsh_options: GmshOptions
    """The gmsh knobs (thread count and terminal output are ``HashExclude`` and drop out)."""

    deterministic: bool
    """Whether meshing was pinned to one thread for reproducibility."""

    tags: list[TagSelectorSpec] = Field(default_factory=list)
    """The domain's tags in domain order (an unnamed body's group name concatenates tag names in that order)."""

    materials: list[str] = Field(default_factory=list)
    """The sorted material tags; an unnamed body's group name starts with its material tag."""
