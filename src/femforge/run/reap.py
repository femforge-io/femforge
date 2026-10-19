"""Abandoned runs: the bundles left ``running`` with no live claim on them, marked so and their claims broken."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import psutil

from femforge.artifacts.lock import LOCK_SUFFIX, DirLock, LockOwner
from femforge.common.files import BundleFile
from femforge.common.log import log
from femforge.run.bundle import write_manifest
from femforge.run.manifest import RunManifest
from femforge.run.result import LifecycleStatus


if TYPE_CHECKING:
    from collections.abc import Callable

    from femforge.run.result import ManifestStatus


__all__ = ["Alive", "ReapedRun", "reap_runs"]

type Alive = Callable[[int], bool]
"""Whether a process id is alive on this host."""

HOST = socket.gethostname()


@dataclass(frozen=True)
class ReapedRun:
    """One run [`reap_runs`][femforge.run.reap.reap_runs] settled."""

    run_id: str
    """The run's bundle name."""

    sim_dir: Path
    """The run's bundle directory."""

    owner: LockOwner | None
    """Who held the run's claim and when it last beat; None when nobody did."""

    status: ManifestStatus | None
    """The status the run's bundle carries now; None without a readable manifest."""


def reap_runs(runs_dir: str | Path, *, alive: Alive = psutil.pid_exists, host: str = HOST) -> list[ReapedRun]:
    """Mark every bundle under ``runs_dir`` left ``running`` with no live claim on it as ``abandoned``, and break every claim that went silent.

    Args:
        runs_dir: The runs directory holding the bundles and their claims.
        alive: Whether a process id is alive on this host.
        host: This host's name; a claim held here by a process that is no longer alive counts as silent.
    """
    directory = Path(runs_dir)
    if not directory.is_dir():
        return []

    def silent(lock: DirLock, owner: LockOwner | None) -> bool:
        if not lock.path.is_dir() or lock.is_stale():
            return True

        return owner is not None and owner.host == host and not alive(owner.pid)

    def claims_running(sim_dir: Path) -> bool:
        """Whether the manifest says ``running``, read without validating it: one key out of thousands of bundles."""
        try:
            data = cast("object", json.loads((sim_dir / BundleFile.MANIFEST).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return False

        return isinstance(data, dict) and cast("dict[str, object]", data).get("status") == LifecycleStatus.RUNNING

    def settle(sim_dir: Path) -> ManifestStatus | None:
        try:
            manifest = RunManifest.load(sim_dir)
        except (OSError, ValueError):
            return None

        if manifest.status is not LifecycleStatus.RUNNING:
            return manifest.status

        manifest.status = LifecycleStatus.ABANDONED
        _ = write_manifest(sim_dir, manifest)

        return manifest.status

    with os.scandir(directory) as entries:
        names = sorted(entry.name for entry in entries if entry.is_dir())

    claimed = {name.removesuffix(LOCK_SUFFIX) for name in names if name.endswith(LOCK_SUFFIX)}
    running = {name for name in names if not name.endswith(LOCK_SUFFIX) and claims_running(directory / name)}

    reaped: list[ReapedRun] = []
    for run_id in sorted(claimed | running):
        sim_dir = directory / run_id
        lock = DirLock(directory / f"{run_id}{LOCK_SUFFIX}")
        owner = lock.owner()
        if not silent(lock, owner):
            continue

        status = settle(sim_dir)
        lock.release()

        held = owner.model_dump(mode="json") if owner is not None else {}
        log.bind(run_id=run_id, status=status, **held).info("Abandoned run reaped.")
        reaped.append(ReapedRun(run_id, sim_dir, owner, status))

    return reaped
