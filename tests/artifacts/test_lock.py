"""DirLock: one claimant wins, a released claim is free again, heartbeats keep a holder alive, and a silent claim goes stale."""

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from femforge.artifacts import lock as lock_module
from femforge.artifacts.lock import OWNER_FILE, ArtifactClaim, DirLock, LockOwner, keep_alive


def _stale_owner(path: Path, *, hours: float) -> None:
    path.mkdir()
    then = datetime.now(timezone.utc) - timedelta(hours=hours)
    _ = (path / OWNER_FILE).write_text(
        LockOwner(
            host="elsewhere",
            pid=1,
            started_at=then,
            heartbeat_at=then,
        ).model_dump_json(),
        encoding="utf-8",
    )


def test_only_one_claimant_wins_until_release(tmp_path: Path) -> None:
    lock = DirLock(tmp_path / "k.lock")
    assert lock.owner() is None
    assert lock.is_stale() is False
    assert lock.held is False

    assert lock.try_acquire() is True
    assert lock.held is True

    owner = lock.owner()
    assert owner is not None
    assert owner.pid == os.getpid()
    assert owner.heartbeat_at >= owner.started_at

    assert DirLock(tmp_path / "k.lock").try_acquire() is False

    lock.release()
    assert lock.held is False
    assert lock.owner() is None
    assert DirLock(tmp_path / "k.lock").try_acquire() is True


def test_heartbeat_refreshes_the_owner_file_only_while_held(tmp_path: Path) -> None:
    lock = DirLock(tmp_path / "k.lock")
    lock.heartbeat()
    assert not (tmp_path / "k.lock").exists()

    assert lock.try_acquire()
    first = lock.owner()
    assert first is not None

    time.sleep(0.01)
    lock.heartbeat()

    second = lock.owner()
    assert second is not None
    assert second.heartbeat_at > first.heartbeat_at
    assert second.started_at == first.started_at

    (tmp_path / "k.lock" / OWNER_FILE).unlink()
    lock.heartbeat()
    assert lock.owner() is not None


def test_a_claim_without_heartbeats_goes_stale_and_is_broken(tmp_path: Path) -> None:
    path = tmp_path / "k.lock"
    _stale_owner(path, hours=2)

    lock = DirLock(path, stale_after_s=120)
    assert lock.is_stale() is True
    assert lock.try_acquire() is True
    owner = lock.owner()
    assert owner is not None
    assert owner.pid == os.getpid()


def test_a_fresh_claim_is_not_stale(tmp_path: Path) -> None:
    path = tmp_path / "k.lock"
    holder = DirLock(path, stale_after_s=120)
    assert holder.try_acquire()
    assert DirLock(path, stale_after_s=120).is_stale() is False
    assert DirLock(path, stale_after_s=120).try_acquire() is False


def test_a_stale_claim_that_cannot_be_removed_stays_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "k.lock"
    _stale_owner(path, hours=2)

    def cannot_remove(_path: Path, ignore_errors: bool = False) -> None:
        _ = ignore_errors

    monkeypatch.setattr(lock_module.shutil, "rmtree", cannot_remove)
    assert DirLock(path, stale_after_s=120).try_acquire() is False


def test_a_claim_without_an_owner_file_ages_by_mtime(tmp_path: Path) -> None:
    path = tmp_path / "k.lock"
    path.mkdir()
    _ = (path / OWNER_FILE).write_text("not json", encoding="utf-8")

    fresh = DirLock(path, stale_after_s=120)
    assert fresh.owner() is None
    assert fresh.is_stale() is False
    assert fresh.try_acquire() is False

    old = 1_000_000_000
    os.utime(path, (old, old))
    assert DirLock(path, stale_after_s=120).try_acquire() is True


def test_keep_alive_beats_from_a_thread_until_the_block_ends(tmp_path: Path) -> None:
    lock = DirLock(tmp_path / "k.lock")
    assert lock.try_acquire()
    before = lock.owner()
    assert before is not None

    with keep_alive(lock, interval_s=0.01):
        time.sleep(0.08)
    during = lock.owner()
    assert during is not None
    assert during.heartbeat_at > before.heartbeat_at

    time.sleep(0.05)
    after = lock.owner()
    assert after is not None
    assert after.heartbeat_at == during.heartbeat_at


def test_keep_alive_survives_a_failing_heartbeat() -> None:
    class _Flaky(ArtifactClaim):
        def __init__(self) -> None:
            self.attempts: int = 0

        def heartbeat(self) -> None:
            self.attempts += 1
            raise OSError("disk gone")

        def release(self) -> None:
            pass

    claim = _Flaky()
    with keep_alive(claim, interval_s=0.01):
        time.sleep(0.05)
    assert claim.attempts >= 1
