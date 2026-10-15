"""Reconcile gmsh physical-group ids with the ids ElmerGrid assigns, read back from the ``mesh.names`` file it writes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


# Section markers ElmerGrid writes in `mesh.names`; matched case-insensitively as substrings so minor format drift between Elmer versions doesn't break parsing.
_BODIES_HEADER = "names for bodies"
_BOUNDARIES_HEADER = "names for boundaries"


@dataclass(frozen=True)
class MeshNames:
    """The `name -> Elmer id` maps ElmerGrid records in `mesh.names`, one per section (one continuing id sequence: boundary ids start after the body ids)."""

    bodies: dict[str, int]
    """Body name to Elmer body id."""

    boundaries: dict[str, int]
    """Boundary name to Elmer boundary id."""


class _IdentifiedGroup(Protocol):
    """A mesh group the sif writer targets by integer id: both `PhysicalGroup` and `BoundaryGroup` satisfy this (shared `name` + writable `id`)."""

    name: str
    id: int


def parse_mesh_names(text: str) -> MeshNames:
    """
    Parse an ElmerGrid ``mesh.names`` file (``$ AIR__EXT = 1`` lines under per-section headers) into its body and
    boundary ``name -> id`` maps; anything before the first recognized header, and any malformed line, is ignored.
    """
    bodies: dict[str, int] = {}
    boundaries: dict[str, int] = {}
    current: dict[str, int] | None = None

    for raw in text.splitlines():
        line: str = raw.strip()
        if line.startswith("!"):
            lowered: str = line.lower()
            if _BODIES_HEADER in lowered:
                current = bodies
            elif _BOUNDARIES_HEADER in lowered:
                current = boundaries
            continue

        if current is None or not line.startswith("$") or "=" not in line:
            continue

        name, _, value = line[1:].partition("=")
        try:
            current[name.strip()] = int(value.strip())
        except ValueError:
            continue  # a non-integer rhs isn't an id mapping; skip it rather than fail the whole parse.

    return MeshNames(bodies=bodies, boundaries=boundaries)


def reconcile_ids(names: MeshNames, physical_groups: Iterable[_IdentifiedGroup], boundary_groups: Iterable[_IdentifiedGroup]) -> list[str]:
    """Overwrite each group's gmsh-tag ``id`` in place with the Elmer id ElmerGrid assigned to its name; returns the names ``mesh.names`` lacks.

    Args:
        names: The ids parsed from ``mesh.names``.
        physical_groups: The body groups to renumber.
        boundary_groups: The boundary groups to renumber.
    """
    missing: list[str] = []
    for group in physical_groups:
        if group.name in names.bodies:
            group.id = names.bodies[group.name]
        else:
            missing.append(group.name)
    for group in boundary_groups:
        if group.name in names.boundaries:
            group.id = names.boundaries[group.name]
        else:
            missing.append(group.name)
    return missing
