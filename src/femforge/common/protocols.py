"""Structural typing contracts shared across pipeline stages."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ElmerEmitter(Protocol):
    """Something that emits its own Elmer sif keywords; materials, conditions, and boundaries all satisfy it."""

    def to_elmer(self, **ctx: object) -> dict[str, object]:
        """This object's Elmer sif keywords."""
        ...


@runtime_checkable
class MeshGroupSource(Protocol):
    """Something that resolves to one or more gmsh physical groups."""

    def physical_group_name(self) -> str:
        """The stable group name the Elmer side references the region by."""
        ...
