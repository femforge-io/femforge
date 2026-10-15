"""The sidecar that commits an artifact (``artifact.json``: file list, hashes, spec, provenance) and the reference a consumer keeps."""

from __future__ import annotations

import os
import socket
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Self, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

import femforge
from femforge.common.files import sha256_file
from femforge.common.names import bundle_name


ARTIFACT_FILE = "artifact.json"
"""The sidecar filename; an artifact exists in a store exactly when this file does, so it is always written last."""


class ArtifactOrigin(StrEnum):
    """Where a served artifact came from."""

    LOCAL = "local"
    """The local store already held it."""

    REMOTE = "remote"
    """A remote tier sent it, and the local store now holds a copy."""

    PRODUCED = "produced"
    """A producer made it on this cache miss."""


class FileEntry(BaseModel):
    """One file of an artifact: its store-relative name, size, and content hash."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    name: str
    """Artifact-relative posix path."""

    size: int
    """Bytes."""

    sha256: str
    """Hex SHA-256 of the content."""


class ToolVersions(BaseModel):
    """Provenance of the tools that produced an artifact; never part of its key."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    femforge: str = femforge.__version__
    """The femforge release that produced it."""

    code_sha: str | None = None
    """The producing checkout's commit (``-dirty`` when uncommitted changes were present)."""

    gmsh: str | None = None
    """gmsh version, when meshing was involved."""

    elmer: str | None = None
    """ElmerSolver version, when a solve was involved."""


class ArtifactRecord(BaseModel):
    """The commit marker plus provenance of one stored artifact."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    kind: str
    """The artifact kind name."""

    key: str
    """The content key the artifact is stored under."""

    name: str = ""
    """The ``<adjective>-<noun>-<hash8>`` store directory name; always derived from ``key``, never read from input."""

    producer_version: int
    """The kind's producer version at publish time."""

    spec_type: str
    """``module:QualName`` of the spec model, for humans reading the store."""

    spec: dict[str, object]
    """The exact payload that was hashed into ``key``."""

    files: list[FileEntry] = Field(default_factory=list)
    """Every file the artifact consists of."""

    created_at: datetime
    """When it was published (UTC)."""

    producer_host: str
    """Hostname of the machine that produced it."""

    producer_pid: int
    """Process id of the producer, for correlating logs."""

    tools: ToolVersions = Field(default_factory=ToolVersions)
    """Tool versions involved."""

    @model_validator(mode="after")
    def _derive_name(self) -> Self:
        self.name = bundle_name(self.key)
        return self

    @classmethod
    def build(
        cls,
        *,
        kind: str,
        key: str,
        producer_version: int,
        spec_type: str,
        spec: dict[str, object],
        workdir: Path,
        files: Sequence[str],
        tools: ToolVersions | None = None,
    ) -> ArtifactRecord:
        """Assemble the record for the files under ``workdir``, sizing and hashing each.

        Args:
            kind: The artifact kind name.
            key: The content key the artifact is stored under.
            producer_version: The kind's producer version.
            spec_type: ``module:QualName`` of the spec model.
            spec: The exact payload hashed into ``key``.
            workdir: The directory holding the artifact's files.
            files: The artifact-relative names of the files.
            tools: The tool versions involved; None records the running femforge release only.
        """
        entries: list[FileEntry] = []
        for name in files:
            path: Path = workdir / name
            entries.append(FileEntry(name=Path(name).as_posix(), size=path.stat().st_size, sha256=sha256_file(path)))
        return cls(
            kind=kind,
            key=key,
            producer_version=producer_version,
            spec_type=spec_type,
            spec=spec,
            files=entries,
            created_at=datetime.now(timezone.utc),
            producer_host=socket.gethostname(),
            producer_pid=os.getpid(),
            tools=tools if tools is not None else ToolVersions(),
        )

    @property
    def file_names(self) -> tuple[str, ...]:
        """The artifact-relative names, in record order."""
        return tuple(entry.name for entry in self.files)

    @property
    def total_bytes(self) -> int:
        """The sum of every file size."""
        return sum(entry.size for entry in self.files)

    def verify(self, directory: Path, *, hashes: bool = False) -> list[str]:
        """The names missing under ``directory`` or differing in size (and in content, when ``hashes``)."""
        problems: list[str] = []
        for entry in self.files:
            path: Path = directory / entry.name
            if not path.is_file() or path.stat().st_size != entry.size:
                problems.append(entry.name)
            elif hashes and sha256_file(path) != entry.sha256:
                problems.append(entry.name)
        return problems


class ArtifactRef(BaseModel):
    """A consumer's pointer to a store artifact: enough to re-fetch it and to know which of its files are re-fetchable."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    kind: str
    """The artifact kind name."""

    key: str
    """The content key."""

    name: str = ""
    """The ``<adjective>-<noun>-<hash8>`` store directory name; always derived from ``key``, never read from input."""

    origin: ArtifactOrigin
    """Whether this consumer got a hit, pulled it through from the remote tier, or produced it."""

    files: list[str] = Field(default_factory=list)
    """The consumer-relative posix paths the artifact materialized."""

    @model_validator(mode="after")
    def _derive_name(self) -> Self:
        self.name = bundle_name(self.key)
        return self
