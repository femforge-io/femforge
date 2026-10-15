"""An in-memory ArtifactStore: proves the Protocol needs no filesystem and stands in for a remote tier in tests."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from pydantic import BaseModel

from femforge.artifacts.keys import ArtifactKey
from femforge.artifacts.kind import ArtifactKind
from femforge.artifacts.lock import ArtifactClaim
from femforge.artifacts.sidecar import ArtifactRecord
from femforge.artifacts.store import ArtifactMissing, ArtifactStore


class MemoryClaim(ArtifactClaim):
    """A claim held in the store's own set; counts its heartbeats."""

    def __init__(self, store: MemoryArtifactStore, slot: tuple[str, str]) -> None:
        self.store: MemoryArtifactStore = store
        self.slot: tuple[str, str] = slot
        self.beats: int = 0

    def heartbeat(self) -> None:
        self.beats += 1

    def release(self) -> None:
        self.store.claims.discard(self.slot)


class MemoryArtifactStore(ArtifactStore):
    """Artifacts as ``{(kind name, key): (record, {name: bytes})}`` plus a set of held claims."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], tuple[ArtifactRecord, dict[str, bytes]]] = {}
        self.claims: set[tuple[str, str]] = set()
        self.puts: int = 0

    def record[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> ArtifactRecord | None:
        stored = self.items.get((kind.name, key))
        return stored[0] if stored is not None else None

    def exists[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> bool:
        return (kind.name, key) in self.items

    def fetch[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S], into: Path, *, verify: bool = False) -> ArtifactRecord:
        stored = self.items.get((kind.name, key))
        if stored is None:
            raise ArtifactMissing(f"{kind.name}/{key} not in memory")

        record, blobs = stored
        for name, data in blobs.items():
            target = into / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = target.write_bytes(data)
        return record

    def put[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S], source: Path, record: ArtifactRecord) -> bool:
        self.puts += 1
        if (kind.name, key) in self.items:
            return False

        self.items[(kind.name, key)] = (record, {entry.name: (source / entry.name).read_bytes() for entry in record.files})
        return True

    def delete[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> None:
        _ = self.items.pop((kind.name, key), None)

    def keys[S: BaseModel](self, kind: ArtifactKind[S]) -> Iterator[ArtifactKey[S]]:
        for stored_kind, key in self.items:
            if stored_kind == kind.name:
                yield ArtifactKey[S](key)

    def try_claim[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> ArtifactClaim | None:
        slot = (kind.name, str(key))
        if slot in self.claims:
            return None

        self.claims.add(slot)
        return MemoryClaim(self, slot)
