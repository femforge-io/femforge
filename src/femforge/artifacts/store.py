"""The store contract: a byte store addressed by ``(kind, key)`` with a commit-marker sidecar and heartbeat claims, so any backend (a directory, S3) can implement it."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Protocol

from pydantic import BaseModel

from femforge.artifacts.keys import ArtifactKey
from femforge.artifacts.kind import ArtifactKind
from femforge.artifacts.lock import ArtifactClaim
from femforge.artifacts.sidecar import ArtifactRecord


class ArtifactError(RuntimeError):
    """An artifact-store failure."""


class ArtifactMissing(ArtifactError):
    """The requested artifact is in no tier and no producer was given."""


class ArtifactCorrupt(ArtifactError):
    """A stored artifact disagrees with its sidecar."""


class ArtifactStore(Protocol):
    """One storage tier: an artifact exists exactly when its sidecar can be read, and ``put`` is first-wins and atomic."""

    def record[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> ArtifactRecord | None:
        """The committed sidecar of ``key``, or None when this tier lacks it."""
        ...

    def exists[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> bool:
        """Whether this tier holds ``key``."""
        ...

    def fetch[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S], into: Path, *, verify: bool = False) -> ArtifactRecord:
        """Materialize every file ``key`` lists into ``into`` and return its record.

        Args:
            kind: The artifact kind.
            key: The artifact's content key.
            into: The directory to write the files into.
            verify: Also check each file's sha256.
        """
        ...

    def put[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S], source: Path, record: ArtifactRecord) -> bool:
        """Commit the files ``record`` lists under ``source`` as ``key``; False when the key already exists.

        Args:
            kind: The artifact kind.
            key: The artifact's content key.
            source: The directory holding the files.
            record: The sidecar to commit, listing the files.
        """
        ...

    def delete[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> None:
        """Remove ``key`` from this tier."""
        ...

    def keys[S: BaseModel](self, kind: ArtifactKind[S]) -> Iterator[ArtifactKey[S]]:
        """Every committed key of ``kind`` in this tier."""
        ...

    def try_claim[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> ArtifactClaim | None:
        """Claim ``key`` for production so other producers wait; None when another live claim holds it."""
        ...
