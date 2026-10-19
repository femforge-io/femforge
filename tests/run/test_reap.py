"""Reaping the runs a killed driver left behind: a running bundle nobody live holds reads abandoned, silent claims go, and everything else is left alone."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from femforge.artifacts.lock import LOCK_SUFFIX, OWNER_FILE, DirLock, LockOwner
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.run.bundle import write_manifest
from femforge.run.manifest import RunManifest
from femforge.run.reap import reap_runs
from femforge.run.result import LifecycleStatus, ManifestStatus, RunStatus


HOST = "box"
CREATED = datetime(2026, 9, 5, 13, 20, tzinfo=timezone.utc)


def _bundle(runs_dir: Path, run_id: str, status: ManifestStatus = LifecycleStatus.RUNNING) -> Path:
    sim_dir = runs_dir / run_id
    manifest = RunManifest.build(
        SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step")),
        status=status,
        run_id=run_id,
        code_sha="sha1",
        created_at=CREATED,
    )
    _ = write_manifest(sim_dir, manifest)

    return sim_dir


def _claim(runs_dir: Path, run_id: str, *, pid: int, host: str = HOST, silent_for: timedelta = timedelta(0)) -> Path:
    """A claim as a driver leaves one, whose last heartbeat was ``silent_for`` ago."""
    lock = runs_dir / f"{run_id}{LOCK_SUFFIX}"
    lock.mkdir(parents=True)
    beat = datetime.now(timezone.utc) - silent_for
    owner = LockOwner(
        host=host,
        pid=pid,
        started_at=beat - timedelta(minutes=1),
        heartbeat_at=beat,
    )
    _ = (lock / OWNER_FILE).write_text(owner.model_dump_json(), encoding="utf-8")

    return lock


def _status(sim_dir: Path) -> str:
    return RunManifest.load(sim_dir).status


def test_a_stale_claim_is_broken_and_its_running_bundle_marked_abandoned(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "killed-1111")
    lock = _claim(tmp_path, "killed-1111", pid=4242, silent_for=timedelta(minutes=10))

    reaped = reap_runs(tmp_path, alive=lambda _: True, host=HOST)

    assert [run.run_id for run in reaped] == ["killed-1111"]
    assert reaped[0].sim_dir == bundle
    assert reaped[0].owner is not None and reaped[0].owner.pid == 4242
    assert reaped[0].status == "abandoned"
    assert _status(bundle) == "abandoned"
    assert not lock.exists()


def test_a_claim_whose_process_is_gone_from_this_host_is_reaped_before_it_goes_stale(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "dead-2222")
    _ = _claim(tmp_path, "dead-2222", pid=4242)

    reaped = reap_runs(tmp_path, alive=lambda pid: pid != 4242, host=HOST)

    assert [run.run_id for run in reaped] == ["dead-2222"]
    assert _status(bundle) == "abandoned"


def test_a_running_bundle_nobody_claims_is_abandoned(tmp_path: Path) -> None:
    """The driver claims before it writes ``running``, so a running manifest with no claim at all was killed before claims existed or after its claim was broken."""
    bundle = _bundle(tmp_path, "orphan-9999")

    reaped = reap_runs(tmp_path, alive=lambda _: True, host=HOST)

    assert [run.run_id for run in reaped] == ["orphan-9999"]
    assert reaped[0].owner is None
    assert reaped[0].status == "abandoned"
    assert _status(bundle) == "abandoned"


def test_live_claims_and_another_hosts_fresh_claim_are_left_alone(tmp_path: Path) -> None:
    """A fresh heartbeat from a process this host still runs, or from any process on another host, is a run in flight."""
    mine = _bundle(tmp_path, "mine-3333")
    theirs = _bundle(tmp_path, "theirs-4444")
    locks = [_claim(tmp_path, "mine-3333", pid=1), _claim(tmp_path, "theirs-4444", pid=1, host="elsewhere")]

    reaped = reap_runs(tmp_path, alive=lambda pid: pid == 1, host=HOST)

    assert reaped == []
    assert all(lock.is_dir() for lock in locks)
    assert _status(mine) == "running" and _status(theirs) == "running"


def test_a_settled_bundle_keeps_its_status_and_only_a_lingering_claim_on_it_goes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "done-5555", status=RunStatus.OK)
    lock = _claim(tmp_path, "done-5555", pid=4242, silent_for=timedelta(minutes=10))
    untouched = _bundle(tmp_path, "quiet-0000", status=RunStatus.ERROR)

    reaped = reap_runs(tmp_path, alive=lambda _: True, host=HOST)

    assert [run.run_id for run in reaped] == ["done-5555"]
    assert reaped[0].status == "ok"
    assert _status(bundle) == "ok" and _status(untouched) == "error"
    assert not lock.exists()


def test_a_claim_without_a_readable_bundle_is_broken_and_reported_without_a_status(tmp_path: Path) -> None:
    """A claim taken before the first manifest write, and one on a manifest the model no longer reads, both go."""
    _ = _claim(tmp_path, "unwritten-6666", pid=4242, silent_for=timedelta(minutes=10))
    broken = tmp_path / "broken-7777"
    broken.mkdir()
    _ = (broken / "manifest.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    _ = _claim(tmp_path, "broken-7777", pid=4242, silent_for=timedelta(minutes=10))
    _ = (tmp_path / "junk-8888").mkdir()
    _ = (tmp_path / "junk-8888" / "manifest.json").write_text("[]", encoding="utf-8")
    _ = (tmp_path / "garbled-9999").mkdir()
    _ = (tmp_path / "garbled-9999" / "manifest.json").write_text("{not json", encoding="utf-8")
    _ = (tmp_path / "bare-0000").mkdir()

    reaped = reap_runs(tmp_path, alive=lambda _: True, host=HOST)

    assert {run.run_id: run.status for run in reaped} == {"unwritten-6666": None, "broken-7777": None}
    assert not list(tmp_path.glob("*" + LOCK_SUFFIX))


def test_an_ownerless_claim_is_judged_by_its_directory_age(tmp_path: Path) -> None:
    """A claim directory with no owner file is stale by its mtime alone, as DirLock already judges it."""
    _ = _bundle(tmp_path, "mute-8888")
    lock = DirLock(tmp_path / f"mute-8888{LOCK_SUFFIX}")
    assert lock.try_acquire()
    (lock.path / OWNER_FILE).unlink()

    assert reap_runs(tmp_path, alive=lambda _: True, host=HOST) == []
    assert lock.path.is_dir()


def test_a_missing_runs_directory_reaps_nothing(tmp_path: Path) -> None:
    assert reap_runs(tmp_path / "nowhere") == []
