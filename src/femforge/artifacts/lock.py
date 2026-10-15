"""In-flight claims: a heartbeat-refreshed marker on one artifact key, held as a directory (``mkdir`` is atomic on NTFS, POSIX, and SMB)."""

from __future__ import annotations

import contextvars
import os
import shutil
import socket
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from femforge.common.log import log


LOCK_SUFFIX = ".lock"
"""The suffix of a claim directory's name."""

OWNER_FILE = "owner.json"
"""The file inside a claim directory that records its owner."""

HEARTBEAT_S = 10.0
"""`t` `[s]`: How often a holder refreshes its claim."""

STALE_AFTER_S = 120.0
"""`t` `[s]`: How long a claim may go without a heartbeat before another claimant breaks it (a dozen missed beats)."""


class ArtifactClaim(Protocol):
    """A held claim: refreshed by ``heartbeat`` while the holder works, dropped by ``release``."""

    def heartbeat(self) -> None:
        """Refresh the claim so it does not go stale."""
        ...

    def release(self) -> None:
        """Drop the claim."""
        ...


class LockOwner(BaseModel):
    """Who holds a claim, since when, and when it last beat."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    host: str
    """The machine the claiming process runs on."""

    pid: int
    """The claiming process's id on that machine."""

    started_at: datetime
    """When the claim was taken (UTC)."""

    heartbeat_at: datetime
    """When the claim last beat (UTC); a claim silent for longer than its stale limit is broken."""


class DirLock(ArtifactClaim):
    """A claim held as a directory with an owner file; stale (no heartbeat for ``stale_after_s``) claims are broken by the next claimant."""

    def __init__(self, path: Path, *, stale_after_s: float = STALE_AFTER_S) -> None:
        """Address the claim directory ``path``, which goes stale after ``stale_after_s`` seconds without a heartbeat."""
        self.path: Path = path
        self.stale_after_s: float = stale_after_s
        self._held: bool = False

    @property
    def held(self) -> bool:
        """Whether this object currently holds the claim."""
        return self._held

    def owner(self) -> LockOwner | None:
        """The recorded owner, or None when the claim is not held or its owner file is unreadable."""
        try:
            return LockOwner.model_validate_json((self.path / OWNER_FILE).read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError):
            return None

    def is_stale(self) -> bool:
        """Whether a held claim has gone ``stale_after_s`` without a heartbeat (the directory mtime stands in for a missing owner file)."""
        if not self.path.is_dir():
            return False

        owner: LockOwner | None = self.owner()
        last: datetime = owner.heartbeat_at if owner is not None else datetime.fromtimestamp(self.path.stat().st_mtime, tz=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() > self.stale_after_s

    def _write_owner(self, started_at: datetime) -> None:
        now: datetime = datetime.now(timezone.utc)
        owner = LockOwner(
            host=socket.gethostname(),
            pid=os.getpid(),
            started_at=started_at,
            heartbeat_at=now,
        )
        _ = (self.path / OWNER_FILE).write_text(owner.model_dump_json(), encoding="utf-8")

    def try_acquire(self) -> bool:
        """Claim the key; False when another live claim holds it. A stale claim is broken and retried once."""
        for _ in range(2):
            try:
                self.path.mkdir(parents=True)
            except FileExistsError:
                if not self.is_stale():
                    return False

                shutil.rmtree(self.path, ignore_errors=True)
                continue

            self._write_owner(datetime.now(timezone.utc))
            self._held = True
            return True
        return False

    def heartbeat(self) -> None:
        """Refresh the owner file's heartbeat; a no-op unless this object holds the claim."""
        if not self._held:
            return

        owner: LockOwner | None = self.owner()
        self._write_owner(owner.started_at if owner is not None else datetime.now(timezone.utc))

    def release(self) -> None:
        """Drop the claim directory: the holder letting go, or a reaper breaking a claim it has proven silent."""
        self._held = False
        shutil.rmtree(self.path, ignore_errors=True)


@contextmanager
def keep_alive(claim: ArtifactClaim, interval_s: float = HEARTBEAT_S) -> Iterator[None]:
    """Beat ``claim`` every ``interval_s`` from a daemon thread while the block runs, so a live holder never looks stale."""
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(interval_s):
            try:
                claim.heartbeat()
            except OSError as error:
                log.bind(error=str(error)).warning("Claim heartbeat failed.")

    # Copied context: a failed beat's warning keeps the caller's run_id and telemetry route.
    thread = threading.Thread(
        target=contextvars.copy_context().run,
        args=(_beat,),
        name="artifact-claim-heartbeat",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=interval_s + 1.0)
