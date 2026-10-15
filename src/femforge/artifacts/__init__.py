"""Content-addressed artifacts: a spec keys each one, a store holds its files, and the cache serves, pulls through or produces it."""

from __future__ import annotations

from femforge.artifacts.cache import ArtifactCache, ArtifactHandle
from femforge.artifacts.keys import ArtifactKey, artifact_key, sha256_json, spec_payload
from femforge.artifacts.kind import ArtifactKind, BuiltinKind, Produced, Producer
from femforge.artifacts.local import LocalArtifactStore, StoredArtifact, artifact_dir_of, store_root_of
from femforge.artifacts.lock import ArtifactClaim, DirLock, LockOwner, keep_alive
from femforge.artifacts.records import RecordKind, RecordOutcome, json_record_kind
from femforge.artifacts.sidecar import ARTIFACT_FILE, ArtifactOrigin, ArtifactRecord, ArtifactRef, FileEntry, ToolVersions
from femforge.artifacts.store import ArtifactCorrupt, ArtifactError, ArtifactMissing, ArtifactStore


__all__ = [
    "ARTIFACT_FILE",
    "ArtifactCache",
    "ArtifactClaim",
    "ArtifactCorrupt",
    "ArtifactError",
    "ArtifactHandle",
    "ArtifactKey",
    "ArtifactKind",
    "ArtifactMissing",
    "ArtifactOrigin",
    "ArtifactRecord",
    "ArtifactRef",
    "ArtifactStore",
    "BuiltinKind",
    "DirLock",
    "FileEntry",
    "LocalArtifactStore",
    "LockOwner",
    "Produced",
    "Producer",
    "RecordKind",
    "RecordOutcome",
    "StoredArtifact",
    "ToolVersions",
    "artifact_dir_of",
    "artifact_key",
    "json_record_kind",
    "keep_alive",
    "sha256_json",
    "spec_payload",
    "store_root_of",
]
