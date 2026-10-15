"""The typed front door: key a spec, serve it from the local tier, then the remote tier, else produce it under a heartbeat claim and publish to both."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel

from femforge.artifacts.keys import ArtifactKey, artifact_key, spec_payload
from femforge.artifacts.kind import ArtifactKind, Produced, Producer
from femforge.artifacts.local import LocalArtifactStore
from femforge.artifacts.lock import HEARTBEAT_S, ArtifactClaim, keep_alive
from femforge.artifacts.sidecar import ArtifactOrigin, ArtifactRecord, ArtifactRef, ToolVersions
from femforge.artifacts.store import ArtifactError, ArtifactMissing, ArtifactStore
from femforge.common.log import log


@dataclass(frozen=True)
class ArtifactHandle[S: BaseModel]:
    """A served artifact: where its files are, where they came from, and the record behind them."""

    kind: ArtifactKind[S]
    """The kind of artifact served."""

    key: ArtifactKey[S]
    """The content key it is stored under."""

    origin: ArtifactOrigin
    """Whether the local store held it, a remote tier sent it, or a producer just made it."""

    path: Path
    """The local directory that holds its files."""

    record: ArtifactRecord
    """The sidecar record that describes it."""

    @property
    def files(self) -> tuple[str, ...]:
        """The artifact-relative names of its files."""
        return self.record.file_names

    @property
    def ref(self) -> ArtifactRef:
        """The pointer a consumer records."""
        return ArtifactRef(
            kind=self.kind.name,
            key=self.key,
            origin=self.origin,
            files=list(self.files),
        )


def _spec_type_name(spec: BaseModel) -> str:
    return f"{type(spec).__module__}:{type(spec).__qualname__}"


class ArtifactCache:
    """Local tier always; an optional remote tier is pulled through on a hit and pushed to after a publish."""

    def __init__(
        self,
        local: LocalArtifactStore,
        remote: ArtifactStore | None = None,
        *,
        poll_s: float = 2.0,
        heartbeat_s: float = HEARTBEAT_S,
        verify: bool = False,
        push_required: bool = False,
    ) -> None:
        """Serve from ``local`` first, then from ``remote`` when one is given.

        Args:
            local: The local tier, always consulted first and always published to.
            remote: The optional remote tier, pulled through on a hit and pushed to after a publish.
            poll_s: Seconds a producer waits between checks while another claim holds its key.
            heartbeat_s: Seconds between refreshes of a held claim while producing.
            verify: Verify file hashes when pulling from the remote tier.
            push_required: Raise when a publish cannot reach the remote tier, instead of logging a warning.
        """
        self.local: LocalArtifactStore = local
        self.remote: ArtifactStore | None = remote
        self.poll_s: float = poll_s
        self.heartbeat_s: float = heartbeat_s
        self.verify: bool = verify
        self.push_required: bool = push_required

    def key[S: BaseModel](self, kind: ArtifactKind[S], spec: S) -> ArtifactKey[S]:
        """The content key of ``spec`` under ``kind``."""
        return artifact_key(kind, spec)

    def lookup[S: BaseModel](self, kind: ArtifactKind[S], spec: S) -> ArtifactRecord | None:
        """The record of an existing artifact in any tier, without materializing it."""
        key: ArtifactKey[S] = self.key(kind, spec)
        record: ArtifactRecord | None = self.local.record(kind, key)
        if record is None and self.remote is not None:
            record = self.remote.record(kind, key)
        return record

    def _handle[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S], origin: ArtifactOrigin, into: Path | None, record: ArtifactRecord) -> ArtifactHandle[S]:
        return ArtifactHandle(
            kind=kind,
            key=key,
            origin=origin,
            path=into if into is not None else self.local.path(kind, key),
            record=record,
        )

    def _fetch_existing[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S], into: Path | None) -> ArtifactHandle[S] | None:
        """Serve ``key`` from the local tier, else pull it through from the remote tier; None when neither has it."""
        record: ArtifactRecord | None = self.local.record(kind, key)
        if record is not None:
            if into is not None:
                record = self.local.fetch(kind, key, into)
            log.bind(kind=kind.name, key=key.short, origin=ArtifactOrigin.LOCAL).info("Artifact hit.")
            return self._handle(
                kind,
                key,
                ArtifactOrigin.LOCAL,
                into,
                record,
            )

        if self.remote is None or self.remote.record(kind, key) is None:
            return None

        staging: Path = self.local.staging_dir(kind, key)
        record = self.remote.fetch(
            kind,
            key,
            staging,
            verify=self.verify,
        )
        _ = self.local.put(
            kind,
            key,
            staging,
            record,
        )
        if into is not None:
            record = self.local.fetch(kind, key, into)
        log.bind(
            kind=kind.name,
            key=key.short,
            origin=ArtifactOrigin.REMOTE,
            bytes=record.total_bytes,
        ).info("Artifact pulled through.")
        return self._handle(
            kind,
            key,
            ArtifactOrigin.REMOTE,
            into,
            record,
        )

    def fetch[S: BaseModel](self, kind: ArtifactKind[S], spec: S, into: Path | None) -> ArtifactHandle[S] | None:
        """A hit-only request: the artifact from whichever tier holds it, else None.

        Args:
            kind: The artifact kind.
            spec: The spec that keys the artifact.
            into: The directory to materialize the files into; None leaves them in the local store.
        """
        return self._fetch_existing(kind, self.key(kind, spec), into)

    def get_or_produce[S: BaseModel](self, kind: ArtifactKind[S], spec: S, into: Path | None, produce: Producer[S] | None = None) -> ArtifactHandle[S]:
        """Serve ``spec`` from a tier, or produce it under an in-flight claim and publish it.

        Args:
            kind: The artifact kind.
            spec: The spec that keys the artifact.
            into: The directory that receives the files, and where an ``in_place`` kind produces; None keeps them in the local store only.
            produce: The producer to run on a miss; None uses the kind's own.
        """
        key: ArtifactKey[S] = self.key(kind, spec)
        producer: Producer[S] | None = produce if produce is not None else kind.produce

        handle: ArtifactHandle[S] | None = self._fetch_existing(kind, key, into)
        if handle is not None:
            return handle

        if producer is None:
            raise ArtifactMissing(f"{kind.name}/{key.name}: not in any tier and the kind has no producer")

        if kind.in_place and into is None:
            raise ArtifactError(f"{kind.name}: an in-place kind needs a directory to produce into")

        authority: ArtifactStore = self.remote if self.remote is not None else self.local
        claim: ArtifactClaim | None = authority.try_claim(kind, key)
        while claim is None:
            time.sleep(self.poll_s)
            handle = self._fetch_existing(kind, key, into)
            if handle is not None:
                return handle

            claim = authority.try_claim(kind, key)

        try:
            handle = self._fetch_existing(kind, key, into)
            if handle is not None:
                return handle

            with keep_alive(claim, self.heartbeat_s):
                return self._produce(
                    kind,
                    spec,
                    key,
                    into,
                    producer,
                )
        finally:
            claim.release()

    def _produce[S: BaseModel](self, kind: ArtifactKind[S], spec: S, key: ArtifactKey[S], into: Path | None, producer: Producer[S]) -> ArtifactHandle[S]:
        workdir: Path = into if kind.in_place and into is not None else self.local.staging_dir(kind, key)
        started: float = time.monotonic()
        produced: Produced = producer(spec, workdir)

        missing: list[str] = [name for name in kind.required_files if not (workdir / name).is_file()]
        if missing:
            raise ArtifactError(f"{kind.name}: the producer left out required files {missing}")

        record: ArtifactRecord = ArtifactRecord.build(
            kind=kind.name,
            key=key,
            producer_version=kind.producer_version,
            spec_type=_spec_type_name(spec),
            spec=spec_payload(spec),
            workdir=workdir,
            files=produced.files,
            tools=produced.tools,
        )
        won: bool = self.local.put(
            kind,
            key,
            workdir,
            record,
        )
        log.bind(
            kind=kind.name,
            key=key.short,
            won=won,
            duration_s=round(time.monotonic() - started, 2),
            bytes=record.total_bytes,
        ).info("Artifact produced.")

        if into is not None and not kind.in_place:
            record = self.local.fetch(kind, key, into)
        if won and self.remote is not None:
            self.push(kind, key)

        return self._handle(
            kind,
            key,
            ArtifactOrigin.PRODUCED if won else ArtifactOrigin.LOCAL,
            into,
            record,
        )

    def publish[S: BaseModel](self, kind: ArtifactKind[S], spec: S, source: Path, files: Sequence[str], tools: ToolVersions | None = None) -> ArtifactHandle[S]:
        """Commit files that already exist under ``source`` (an in-place kind after the fact); first-wins like any publish.

        Args:
            kind: The artifact kind.
            spec: The spec that keys the artifact.
            source: The directory holding the files.
            files: The files to commit, relative to ``source``.
            tools: The tool versions to record; None records only femforge's.
        """
        key: ArtifactKey[S] = self.key(kind, spec)
        record: ArtifactRecord = ArtifactRecord.build(
            kind=kind.name,
            key=key,
            producer_version=kind.producer_version,
            spec_type=_spec_type_name(spec),
            spec=spec_payload(spec),
            workdir=source,
            files=files,
            tools=tools,
        )
        won: bool = self.local.put(
            kind,
            key,
            source,
            record,
        )
        if won and self.remote is not None:
            self.push(kind, key)
        return self._handle(
            kind,
            key,
            ArtifactOrigin.PRODUCED if won else ArtifactOrigin.LOCAL,
            source,
            record,
        )

    def push[S: BaseModel](self, kind: ArtifactKind[S], key: ArtifactKey[S]) -> None:
        """Copy a locally committed artifact to the remote tier; a failure warns unless ``push_required``."""
        if self.remote is None:
            return

        record: ArtifactRecord | None = self.local.record(kind, key)
        if record is None:
            raise ArtifactMissing(f"{kind.name}/{key.name} is not in the local tier")

        try:
            _ = self.remote.put(
                kind,
                key,
                self.local.path(kind, key),
                record,
            )
        except Exception as error:  # noqa: BLE001 - the remote tier is best-effort unless required
            if self.push_required:
                raise ArtifactError(f"{kind.name}/{key.name}: remote publish failed: {error}") from error

            log.bind(kind=kind.name, key=key.short, error=str(error)).warning("Remote publish failed.")

    def rehydrate[S: BaseModel](self, ref: ArtifactRef, into: Path, kind: ArtifactKind[S]) -> ArtifactHandle[S]:
        """Materialize one referenced artifact into ``into`` again; raises [`ArtifactMissing`][femforge.artifacts.store.ArtifactMissing] when no tier holds it.

        Args:
            ref: The reference a consumer recorded.
            into: The directory to materialize the files into.
            kind: The kind whose name the reference must carry.
        """
        if ref.kind != kind.name:
            raise ArtifactError(f"{ref.kind}/{ref.key[:12]}: the reference is not a {kind.name!r} artifact")

        handle: ArtifactHandle[S] | None = self._fetch_existing(kind, ArtifactKey[S](ref.key), into)
        if handle is None:
            raise ArtifactMissing(f"{ref.kind}/{ref.key[:12]} is in no tier")

        return handle
