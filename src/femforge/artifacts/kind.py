"""An artifact kind: how its spec keys, what a complete artifact contains, and optionally how to make one; passed by reference, never looked up by name."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, Protocol

from pydantic import BaseModel

from femforge.artifacts.sidecar import ToolVersions


_KIND_NAME = re.compile(r"[a-z0-9_]+")


class BuiltinKind(StrEnum):
    """Store path segments of the built-in kinds; the only place these strings are spelled."""

    STEP = "step"
    """The solve-frame STEP a geometry builds."""

    ELMER_MESH = "elmer_mesh"
    """The Elmer mesh files a config meshes into."""

    RUN_BUNDLE = "run_bundle"
    """A finished run bundle."""


@dataclass(frozen=True)
class Produced:
    """What a producer reports: the files it wrote under its workdir (relative, ``/``-separated) and the tool versions it knows."""

    files: tuple[str, ...]
    """The files written, relative to the workdir and ``/``-separated."""

    tools: ToolVersions | None = None
    """The tool versions that made them; None when the producer knows none."""


class Producer[S: BaseModel](Protocol):
    """The cache-miss callback: populate ``workdir`` for ``spec`` and report the files written."""

    def __call__(self, spec: S, workdir: Path) -> Produced: ...


@dataclass(frozen=True)
class ArtifactKind[S: BaseModel]:
    """One class of artifact; a module-level constant callers pass by reference."""

    name: str
    """The store path segment (``[a-z0-9_]+``; case-insensitive filesystems are in play)."""

    spec_type: type[S]
    """The pydantic model whose instances key this kind."""

    producer_version: int = 1
    """Bump to invalidate every existing key of this kind when the producer's output semantics change."""

    required_files: tuple[str, ...] = ()
    """Files a complete artifact must contain; publish refuses a producer that left any out."""

    produce: Producer[S] | None = None
    """The default cache-miss producer; None means the caller supplies one per request."""

    key: Callable[[S], str] | None = None
    """An identity override (for a kind whose key must coincide with an existing hash); else the generic hash applies."""

    in_place: bool = False
    """The producer works in the consumer's own directory and publish copies out of it (a run bundle), rather than in a staging dir."""

    def __post_init__(self) -> None:
        if not _KIND_NAME.fullmatch(self.name):
            raise ValueError(f"artifact kind name {self.name!r} must match {_KIND_NAME.pattern}")
