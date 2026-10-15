"""The directory-backed store: a staging dir plus one rename publishes atomically, and a sidecar written inside the staging dir is the commit marker."""

from __future__ import annotations

import os
import re
import shutil
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, ValidationError

from femforge.artifacts.keys import ArtifactKey
from femforge.artifacts.kind import ArtifactKind
from femforge.artifacts.lock import LOCK_SUFFIX, STALE_AFTER_S, ArtifactClaim, DirLock
from femforge.artifacts.sidecar import ARTIFACT_FILE, ArtifactRecord
from femforge.artifacts.store import ArtifactCorrupt, ArtifactError, ArtifactMissing, ArtifactStore
from femforge.common.files import link_or_copy, sha256_file, write_atomic
from femforge.common.log import log
from femforge.common.names import bundle_name


STAGING_DIR = ".staging"
"""The directory under each kind that holds producer workdirs."""

CORRUPT_MARK = ".corrupt-"
"""The infix in the name of a quarantined artifact directory."""

_HEX_REF = re.compile(r"[0-9a-f]{8,64}")


def _rename_dir(src: Path, dst: Path) -> None:
    """Move a directory into place; fails when ``dst`` already exists, which is the first-wins signal."""
    os.replace(src, dst)


def artifact_dir_of(path: Path) -> Path | None:
    """The artifact directory ``path`` is or lies inside (the nearest ancestor holding a sidecar), or None."""
    for candidate in (path, *path.parents):
        if (candidate / ARTIFACT_FILE).is_file():
            return candidate

    return None


def store_root_of(path: Path) -> Path | None:
    """The store root two levels above the artifact directory ``path`` is or lies inside, or None."""
    directory: Path | None = artifact_dir_of(path)
    return directory.parent.parent if directory is not None else None


@dataclass(frozen=True)
class StoredArtifact:
    """One committed artifact found on disk: its directory and the sidecar inside it."""

    path: Path
    """The artifact's directory."""

    record: ArtifactRecord
    """The sidecar inside it."""

    @property
    def kind(self) -> str:
        """The artifact kind name."""
        return self.record.kind

    @property
    def key(self) -> str:
        """The content key the artifact is stored under."""
        return self.record.key

    @property
    def name(self) -> str:
        """The artifact's store directory name."""
        return self.path.name


class LocalArtifactStore(ArtifactStore):
    """A directory store: ``<root>/<kind>/<adjective-noun-hash8>/`` holds each committed artifact beside its ``artifact.json`` sidecar."""

    def __init__(self, root: str | Path, *, link: bool = False, claim_stale_s: float = STALE_AFTER_S) -> None:
        """Address the store rooted at ``root``.

        Args:
            root: The store root, holding one directory per kind.
            link: Hardlink files instead of copying them, where the volume allows.
            claim_stale_s: Seconds a claim may go without a heartbeat before another producer breaks it.
        """
        self.root: Path = Path(root)
        self.link: bool = link
        self.claim_stale_s: float = claim_stale_s

    def path[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> Path:
        """Where a committed artifact lives."""
        return self.root / kind.name / key.name

    def staging_dir[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> Path:
        """A fresh, unique workdir for producing ``key``; ``put`` renames it into place."""
        directory: Path = self.root / kind.name / STAGING_DIR / f"{key.name}.{socket.gethostname()}.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        directory.mkdir(parents=True)
        return directory

    def _is_staging[S: BaseModel](self, kind: ArtifactKind[S], source: Path) -> bool:
        return source.parent == self.root / kind.name / STAGING_DIR

    def _quarantine[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> None:
        """Move an artifact directory whose sidecar cannot be read aside, so a fresh publish can land."""
        stamp: str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        target: Path = self.root / kind.name / f"{key.name}.corrupt-{stamp}"
        try:
            _rename_dir(self.path(kind, key), target)
        except OSError as error:
            log.bind(kind=kind.name, key=key.short, error=str(error)).warning("Corrupt artifact could not be quarantined.")
            return

        log.bind(kind=kind.name, key=key.short, quarantined=str(target)).warning("Corrupt artifact quarantined.")

    def _sidecar(self, directory: Path) -> ArtifactRecord | None:
        sidecar: Path = directory / ARTIFACT_FILE
        if not sidecar.is_file():
            return None

        try:
            return ArtifactRecord.model_validate_json(sidecar.read_text(encoding="utf-8"))
        except (ValidationError, ValueError):
            return None

    def record[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> ArtifactRecord | None:
        """The committed sidecar, or None when absent, unreadable (then quarantined), or belonging to a different key that shares the name."""
        home: Path = self.path(kind, key)
        if not (home / ARTIFACT_FILE).is_file():
            return None

        record: ArtifactRecord | None = self._sidecar(home)
        if record is None:
            self._quarantine(kind, key)
            return None

        if record.key != key:
            log.bind(
                kind=kind.name,
                key=key.short,
                existing=record.key[:12],
                name=key.name,
            ).error("Artifact name collision.")
            return None

        return record

    def exists[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> bool:
        """Whether ``key`` is committed under ``kind`` with a readable sidecar."""
        return self.record(kind, key) is not None

    def _materialize(self, src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        if self.link:
            _ = link_or_copy(src, dst)
        else:
            _ = shutil.copy2(src, dst)

    def _copy_out(self, home: Path, record: ArtifactRecord, into: Path, *, verify: bool) -> list[Path]:
        """Materialize every file ``record`` lists from ``home`` into ``into``; sizes are always checked, hashes when ``verify``."""
        label: str = f"{record.kind}/{home.name}"
        copied: list[Path] = []
        for entry in record.files:
            src: Path = home / entry.name
            if not src.is_file() or src.stat().st_size != entry.size:
                raise ArtifactCorrupt(f"{label}: {entry.name} is missing or the wrong size in {self.root}")

            if verify and sha256_file(src) != entry.sha256:
                raise ArtifactCorrupt(f"{label}: {entry.name} fails its sha256 in {self.root}")

            self._materialize(src, into / entry.name)
            copied.append(into / entry.name)

        return copied

    def fetch[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S], into: Path, *, verify: bool = False) -> ArtifactRecord:
        """Materialize every file ``key`` lists into ``into`` and return its record.

        Args:
            kind: The artifact kind.
            key: The artifact's content key.
            into: The directory to write the files into.
            verify: Also check each file's sha256; sizes are always checked.
        """
        record: ArtifactRecord | None = self.record(kind, key)
        if record is None:
            raise ArtifactMissing(f"{kind.name}/{key.name} is not in {self.root}")

        _ = self._copy_out(self.path(kind, key), record, into, verify=verify)
        return record

    def materialize(self, artifact: StoredArtifact, into: Path, *, verify: bool = False) -> list[Path]:
        """Copy (or link) every listed file of a scanned artifact into ``into``; returns the paths written.

        Args:
            artifact: The scanned artifact.
            into: The directory to write the files into.
            verify: Also check each file's sha256; sizes are always checked.
        """
        return self._copy_out(artifact.path, artifact.record, into, verify=verify)

    def put[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S], source: Path, record: ArtifactRecord) -> bool:
        """Commit ``source`` as ``key`` by renaming a staging dir into place; returns False, discarding the copy, when the key already exists.

        Args:
            kind: The artifact kind.
            key: The artifact's content key.
            source: A staging dir, pruned to the listed files, or any other directory whose listed files are first linked or copied into one.
            record: The sidecar to commit, listing the files.
        """
        final: Path = self.path(kind, key)
        existing: ArtifactRecord | None = self._sidecar(final)
        if existing is not None and existing.key != key:
            raise ArtifactError(f"{kind.name}/{key.name}: name collision with key {existing.key[:12]}; move that artifact aside")

        if existing is not None:
            if self._is_staging(kind, source):
                shutil.rmtree(source, ignore_errors=True)
            return False

        if self._is_staging(kind, source):
            staging: Path = source
            keep: set[Path] = {(staging / entry.name).resolve() for entry in record.files}
            for path in sorted(staging.rglob("*"), reverse=True):
                if path.is_file() and path.resolve() not in keep and path.name != ARTIFACT_FILE:
                    path.unlink()
                elif path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
        else:
            staging = self.staging_dir(kind, key)
            for entry in record.files:
                self._materialize(source / entry.name, staging / entry.name)

        write_atomic(staging / ARTIFACT_FILE, record.model_dump_json(indent=2))

        try:
            _rename_dir(staging, final)
        except OSError:
            # Another publisher won the rename; content-addressed, so its copy is as good as ours.
            shutil.rmtree(staging, ignore_errors=True)
            return False

        log.bind(
            kind=kind.name,
            key=key.short,
            name=key.name,
            files=len(record.files),
            bytes=record.total_bytes,
        ).info("Artifact published.")
        return True

    def delete[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> None:
        """Remove the artifact directory of ``key``; a missing one is not an error."""
        shutil.rmtree(self.path(kind, key), ignore_errors=True)

    def committed(self, kind: str) -> Iterator[Path]:
        """The artifact directories under one kind directory: no staging, claims, or quarantines."""
        home: Path = self.root / kind
        if not home.is_dir():
            return

        for entry in sorted(home.iterdir()):
            if entry.is_dir() and not entry.name.startswith(".") and not entry.name.endswith(LOCK_SUFFIX) and CORRUPT_MARK not in entry.name:
                yield entry

    def keys[S: BaseModel](self, kind: ArtifactKind[S]) -> Iterator[ArtifactKey[S]]:
        """Every committed key of ``kind``, read from the sidecars."""
        for entry in self.committed(kind.name):
            record: ArtifactRecord | None = self._sidecar(entry)
            if record is not None and record.kind == kind.name:
                yield ArtifactKey[S](record.key)

    def kinds(self) -> list[str]:
        """The kind directories present under the root."""
        if not self.root.is_dir():
            return []

        return sorted(entry.name for entry in self.root.iterdir() if entry.is_dir() and not entry.name.startswith("."))

    def read(self, directory: Path) -> StoredArtifact | None:
        """The artifact committed in ``directory``, or None when it holds no readable sidecar."""
        record: ArtifactRecord | None = self._sidecar(directory)
        return StoredArtifact(path=directory, record=record) if record is not None else None

    def scan(self, kind: str | None = None) -> Iterator[StoredArtifact]:
        """Every committed artifact under the root (or under one kind), by kind then name."""
        for kind_name in self.kinds() if kind is None else [kind]:
            for directory in self.committed(kind_name):
                artifact: StoredArtifact | None = self.read(directory)
                if artifact is not None and artifact.kind == kind_name:
                    yield artifact

    def _candidates(self, kind: str, text: str, is_hex: bool) -> list[Path]:
        """Where ``text`` could live under one kind directory: a name is a path, a full key names its directory, a prefix needs a scan."""
        home: Path = self.root / kind
        if not is_hex:
            return [home / text]

        if len(text) >= 18:
            return [home / bundle_name(text)]

        return [entry for entry in self.committed(kind) if entry.name.endswith(f"-{text[:8]}")]

    def locate(self, ref: str) -> list[StoredArtifact]:
        """The committed artifacts a human reference names: an artifact directory, a full key, a key prefix of 8+ hex chars, or a ``<adjective>-<noun>-<hash8>`` name."""
        directory = Path(ref)
        if (directory / ARTIFACT_FILE).is_file():
            artifact: StoredArtifact | None = self.read(directory)
            return [artifact] if artifact is not None else []

        text: str = ref.strip().lower()
        is_hex: bool = _HEX_REF.fullmatch(text) is not None

        matches: list[StoredArtifact] = []
        for kind_name in self.kinds():
            for candidate in self._candidates(kind_name, text, is_hex):
                found: StoredArtifact | None = self.read(candidate)
                if found is None or found.kind != kind_name:
                    continue

                if not is_hex or found.key.startswith(text):
                    matches.append(found)

        return matches

    def try_claim[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> ArtifactClaim | None:
        """Claim ``key`` for production; None when another live claim holds it."""
        lock = DirLock(self.root / kind.name / f"{key.name}{LOCK_SUFFIX}", stale_after_s=self.claim_stale_s)
        return lock if lock.try_acquire() else None
