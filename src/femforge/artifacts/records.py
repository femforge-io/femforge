"""A kind whose artifact is one JSON file holding a typed result: the sweep-point memo, generalized."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from pydantic import BaseModel

from femforge.artifacts.cache import ArtifactCache, ArtifactHandle
from femforge.artifacts.keys import ArtifactKey, artifact_key
from femforge.artifacts.kind import ArtifactKind, Produced
from femforge.artifacts.sidecar import ArtifactOrigin, ArtifactRecord
from femforge.common.files import write_atomic


RECORD_FILE = "record.json"
"""The one file a record kind's artifact holds."""


@dataclass(frozen=True)
class RecordOutcome[S: BaseModel, R: BaseModel]:
    """A served record: the typed value, where it came from, and its key."""

    value: R
    """The typed record."""

    origin: ArtifactOrigin
    """Whether the local store held it, a remote tier sent it, or ``produce`` just made it."""

    key: ArtifactKey[S]
    """The content key it is stored under."""


@dataclass(frozen=True)
class RecordKind[S: BaseModel, R: BaseModel]:
    """Pairs an artifact kind with the result model its single JSON file holds."""

    kind: ArtifactKind[S]
    """The artifact kind the records are stored as."""

    result_type: type[R]
    """The model the record file is read back into."""

    def key(self, spec: S) -> ArtifactKey[S]:
        """The content key of ``spec`` under this kind."""
        return artifact_key(self.kind, spec)

    def _read(self, handle: ArtifactHandle[S]) -> R:
        return self.result_type.model_validate_json((handle.path / RECORD_FILE).read_text(encoding="utf-8"))

    def get_or_produce(self, cache: ArtifactCache, spec: S, produce: Callable[[S], R]) -> RecordOutcome[S, R]:
        """The stored value for ``spec``, else ``produce(spec)`` written and published under a claim.

        Args:
            cache: The cache to serve from and publish to.
            spec: The spec that keys the record.
            produce: Computes the value on a miss.
        """

        def _write(spec: S, workdir: Path) -> Produced:
            value: R = produce(spec)
            write_atomic(workdir / RECORD_FILE, value.model_dump_json(indent=2))
            return Produced(files=(RECORD_FILE,))

        handle: ArtifactHandle[S] = cache.get_or_produce(
            self.kind,
            spec,
            into=None,
            produce=_write,
        )
        return RecordOutcome(value=self._read(handle), origin=handle.origin, key=handle.key)

    def load(self, cache: ArtifactCache, spec: S) -> R | None:
        """The stored value for ``spec``, or None when no tier holds it."""
        handle: ArtifactHandle[S] | None = cache.fetch(self.kind, spec, into=None)
        return self._read(handle) if handle is not None else None

    def load_all(self, cache: ArtifactCache) -> Iterator[tuple[ArtifactRecord, R]]:
        """Every value of this kind in the local tier, with the record that identifies it."""
        for key in cache.local.keys(self.kind):
            record: ArtifactRecord | None = cache.local.record(self.kind, key)
            if record is None:  # pragma: no cover - keys() only yields keys whose sidecar just read cleanly
                continue

            yield record, self.result_type.model_validate_json((cache.local.path(self.kind, key) / RECORD_FILE).read_text(encoding="utf-8"))


def json_record_kind[S: BaseModel, R: BaseModel](name: str, *, spec_type: type[S], result_type: type[R], producer_version: int = 1) -> RecordKind[S, R]:
    """Declare a record kind: ``name`` keys ``spec_type`` instances to one JSON ``result_type``.

    Args:
        name: The kind name, which is also its store directory.
        spec_type: The spec model that keys a record.
        result_type: The model the record's JSON file holds.
        producer_version: Bump to invalidate every stored record of this kind.
    """
    return RecordKind(
        kind=ArtifactKind(
            name=name,
            spec_type=spec_type,
            producer_version=producer_version,
            required_files=(RECORD_FILE,),
        ),
        result_type=result_type,
    )
