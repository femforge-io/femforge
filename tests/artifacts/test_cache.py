"""ArtifactCache: hit, remote pull-through, produce under a heartbeat claim, lost publish races, waiting on a claim, push failures, rehydrate."""

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel

from femforge.artifacts.cache import ArtifactCache
from femforge.artifacts.keys import ArtifactKey
from femforge.artifacts.kind import ArtifactKind, Produced, Producer
from femforge.artifacts.local import LocalArtifactStore
from femforge.artifacts.lock import LOCK_SUFFIX, OWNER_FILE, ArtifactClaim, DirLock, LockOwner
from femforge.artifacts.sidecar import ArtifactOrigin, ArtifactRecord, ArtifactRef, ToolVersions
from femforge.artifacts.store import ArtifactError, ArtifactMissing
from tests.artifacts.memory_store import MemoryArtifactStore, MemoryClaim


class _Spec(BaseModel):
    n: int = 1


class _Counter(Producer[_Spec]):
    """A producer that counts its calls and writes one file."""

    def __init__(self) -> None:
        self.calls: int = 0

    def __call__(self, spec: _Spec, workdir: Path) -> Produced:
        self.calls += 1
        _ = (workdir / "out.txt").write_text(f"n={spec.n}", encoding="utf-8")
        return Produced(files=("out.txt",), tools=ToolVersions(gmsh="4.13"))


def _kind(counter: _Counter, *, in_place: bool = False) -> ArtifactKind[_Spec]:
    return ArtifactKind(
        name="counter",
        spec_type=_Spec,
        produce=counter,
        required_files=("out.txt",),
        in_place=in_place,
    )


def _sneak(store: LocalArtifactStore, kind: ArtifactKind[_Spec], key: ArtifactKey[_Spec], text: str) -> None:
    """Publish ``text`` as ``key`` behind the cache's back."""
    staging = store.staging_dir(kind, key)
    _ = (staging / "out.txt").write_text(text, encoding="utf-8")
    record = ArtifactRecord.build(
        kind=kind.name,
        key=key,
        producer_version=1,
        spec_type="x",
        spec={},
        workdir=staging,
        files=["out.txt"],
    )
    assert store.put(
        kind,
        key,
        staging,
        record,
    )


def test_miss_produces_then_hits_locally(tmp_path: Path) -> None:
    counter = _Counter()
    kind = _kind(counter)
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"))
    into = tmp_path / "run"

    first = cache.get_or_produce(kind, _Spec(), into)

    assert first.origin is ArtifactOrigin.PRODUCED
    assert first.kind is kind
    assert first.path == into
    assert first.files == ("out.txt",)
    assert (into / "out.txt").read_text(encoding="utf-8") == "n=1"
    assert first.record.tools.gmsh == "4.13"
    assert first.ref == ArtifactRef(
        kind="counter",
        key=first.key,
        origin=ArtifactOrigin.PRODUCED,
        files=["out.txt"],
    )

    second = cache.get_or_produce(kind, _Spec(), tmp_path / "other")
    assert second.origin is ArtifactOrigin.LOCAL
    assert (tmp_path / "other" / "out.txt").is_file()
    assert counter.calls == 1

    stored = cache.get_or_produce(kind, _Spec(), None)

    assert stored.path == cache.local.path(kind, first.key)
    assert cache.lookup(kind, _Spec()) is not None
    assert cache.lookup(kind, _Spec(n=2)) is None
    assert cache.fetch(kind, _Spec(n=2), None) is None
    assert cache.fetch(kind, _Spec(), None) is not None
    assert not list((tmp_path / "store" / "counter").glob("*" + LOCK_SUFFIX))


def test_no_producer_and_in_place_without_a_directory_are_errors(tmp_path: Path) -> None:
    cache = ArtifactCache(LocalArtifactStore(tmp_path))
    with pytest.raises(ArtifactMissing, match="no producer"):
        _ = cache.get_or_produce(ArtifactKind(name="bare", spec_type=_Spec), _Spec(), None)
    with pytest.raises(ArtifactError, match="in-place"):
        _ = cache.get_or_produce(_kind(_Counter(), in_place=True), _Spec(), None)


def test_a_producer_that_leaves_out_a_required_file_is_refused(tmp_path: Path) -> None:
    def lazy(spec: _Spec, workdir: Path) -> Produced:
        _ = (spec, workdir)
        return Produced(files=())

    kind = ArtifactKind(
        name="lazy",
        spec_type=_Spec,
        produce=lazy,
        required_files=("out.txt",),
    )
    cache = ArtifactCache(LocalArtifactStore(tmp_path))
    with pytest.raises(ArtifactError, match="required files"):
        _ = cache.get_or_produce(kind, _Spec(), None)
    key = cache.key(kind, _Spec())
    assert not cache.local.path(kind, key).exists()
    assert cache.local.try_claim(kind, key) is not None


def test_remote_hit_pulls_through_to_the_local_tier(tmp_path: Path) -> None:
    remote = MemoryArtifactStore()
    counter = _Counter()
    kind = _kind(counter)

    producer_cache = ArtifactCache(LocalArtifactStore(tmp_path / "a"), remote, heartbeat_s=0.01)
    produced = producer_cache.get_or_produce(kind, _Spec(), None)
    assert produced.origin is ArtifactOrigin.PRODUCED
    assert remote.exists(kind, produced.key)
    assert remote.claims == set()

    consumer_cache = ArtifactCache(LocalArtifactStore(tmp_path / "b"), remote, verify=True)
    pulled = consumer_cache.get_or_produce(kind, _Spec(), tmp_path / "run")

    assert pulled.origin is ArtifactOrigin.REMOTE
    assert (tmp_path / "run" / "out.txt").read_text(encoding="utf-8") == "n=1"
    assert consumer_cache.local.exists(kind, produced.key)
    assert consumer_cache.get_or_produce(kind, _Spec(), None).origin is ArtifactOrigin.LOCAL
    assert consumer_cache.lookup(kind, _Spec(n=3)) is None
    assert counter.calls == 1


def test_production_beats_the_authoritative_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote = MemoryArtifactStore()
    seen: list[MemoryClaim] = []
    real_claim = remote.try_claim

    def observing(kind: ArtifactKind[_Spec], key: ArtifactKey[_Spec]) -> ArtifactClaim | None:
        claim = real_claim(kind, key)
        if isinstance(claim, MemoryClaim):
            seen.append(claim)
        return claim

    monkeypatch.setattr(remote, "try_claim", observing)

    def slow(spec: _Spec, workdir: Path) -> Produced:
        _ = spec
        time.sleep(0.05)
        _ = (workdir / "out.txt").write_text("slow", encoding="utf-8")
        return Produced(files=("out.txt",))

    kind = ArtifactKind(
        name="slow",
        spec_type=_Spec,
        produce=slow,
        required_files=("out.txt",),
    )
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "a"), remote, heartbeat_s=0.01)
    _ = cache.get_or_produce(kind, _Spec(), None)
    assert len(seen) == 1
    assert seen[0].beats >= 1
    assert remote.claims == set()


def test_remote_publish_failure_warns_unless_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote = MemoryArtifactStore()

    def broken(_kind: ArtifactKind[_Spec], _key: ArtifactKey[_Spec], _source: Path, _record: ArtifactRecord) -> bool:
        raise ConnectionError("no route")

    monkeypatch.setattr(remote, "put", broken)
    kind = _kind(_Counter())

    lenient = ArtifactCache(LocalArtifactStore(tmp_path / "a"), remote)
    assert lenient.get_or_produce(kind, _Spec(), None).origin is ArtifactOrigin.PRODUCED

    strict = ArtifactCache(LocalArtifactStore(tmp_path / "b"), remote, push_required=True)
    with pytest.raises(ArtifactError, match="remote publish failed"):
        _ = strict.get_or_produce(kind, _Spec(), None)

    with pytest.raises(ArtifactMissing):
        strict.push(kind, ArtifactKey[_Spec]("0" * 64))
    ArtifactCache(LocalArtifactStore(tmp_path / "c")).push(kind, ArtifactKey[_Spec]("0" * 64))


def test_publish_commits_in_place_files_first_wins(tmp_path: Path) -> None:
    kind = ArtifactKind(name="bundle", spec_type=_Spec, in_place=True)
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"), MemoryArtifactStore())
    source = tmp_path / "run"
    source.mkdir()
    _ = (source / "result.json").write_text("{}", encoding="utf-8")

    first = cache.publish(
        kind,
        _Spec(),
        source,
        ["result.json"],
        tools=ToolVersions(elmer="9.0"),
    )
    assert first.origin is ArtifactOrigin.PRODUCED
    assert first.path == source
    assert (cache.local.path(kind, first.key) / "result.json").is_file()
    assert first.record.tools.elmer == "9.0"

    second = cache.publish(
        kind,
        _Spec(),
        source,
        ["result.json"],
    )
    assert second.origin is ArtifactOrigin.LOCAL


def test_in_place_production_and_a_lost_publish_race(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    cache = ArtifactCache(store)
    into = tmp_path / "run"
    into.mkdir()

    def racing(spec: _Spec, workdir: Path) -> Produced:
        _ = spec
        _ = (workdir / "out.txt").write_text("mine", encoding="utf-8")
        # A rival publishes the same key between our production and our publish.
        _sneak(
            store,
            kind,
            key,
            "theirs",
        )
        return Produced(files=("out.txt",))

    kind = ArtifactKind(
        name="race",
        spec_type=_Spec,
        produce=racing,
        in_place=True,
    )
    key = cache.key(kind, _Spec())

    handle = cache.get_or_produce(kind, _Spec(), into)
    assert handle.origin is ArtifactOrigin.LOCAL
    assert (into / "out.txt").read_text(encoding="utf-8") == "mine"
    assert (store.path(kind, key) / "out.txt").read_text(encoding="utf-8") == "theirs"


def test_a_waiter_gets_the_holders_artifact_without_producing(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    counter = _Counter()
    kind = _kind(counter)
    cache = ArtifactCache(store, poll_s=0.01)
    key = cache.key(kind, _Spec())

    held = store.try_claim(kind, key)
    assert held is not None

    def holder() -> None:
        time.sleep(0.05)
        _sneak(
            store,
            kind,
            key,
            "held",
        )
        held.release()

    thread = threading.Thread(target=holder)
    thread.start()
    handle = cache.get_or_produce(kind, _Spec(), tmp_path / "run")
    thread.join()

    assert handle.origin is ArtifactOrigin.LOCAL
    assert (tmp_path / "run" / "out.txt").read_text(encoding="utf-8") == "held"
    assert counter.calls == 0


def test_an_artifact_published_between_lookup_and_claim_is_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    counter = _Counter()
    kind = _kind(counter)
    cache = ArtifactCache(store)
    key = cache.key(kind, _Spec())
    real_claim = store.try_claim

    def publish_then_claim(kind_: ArtifactKind[_Spec], claimed: ArtifactKey[_Spec]) -> DirLock | None:
        if not store.exists(kind_, claimed):
            _sneak(
                store,
                kind_,
                claimed,
                "sneaked in",
            )
        claim = real_claim(kind_, claimed)
        assert claim is None or isinstance(claim, DirLock)
        return claim

    monkeypatch.setattr(store, "try_claim", publish_then_claim)
    handle = cache.get_or_produce(kind, _Spec(), tmp_path / "run")
    assert handle.origin is ArtifactOrigin.LOCAL
    assert (tmp_path / "run" / "out.txt").read_text(encoding="utf-8") == "sneaked in"
    assert counter.calls == 0
    assert store.try_claim(kind, key) is not None


def test_a_stale_claim_is_broken_and_production_proceeds(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store", claim_stale_s=1.0)
    counter = _Counter()
    kind = _kind(counter)
    cache = ArtifactCache(store, poll_s=0.01)
    key = cache.key(kind, _Spec())

    lock_dir = tmp_path / "store" / "counter" / f"{key.name}{LOCK_SUFFIX}"
    lock_dir.mkdir(parents=True)
    then = datetime.now(timezone.utc) - timedelta(hours=1)
    _ = (lock_dir / OWNER_FILE).write_text(
        LockOwner(
            host="gone",
            pid=1,
            started_at=then,
            heartbeat_at=then,
        ).model_dump_json(),
        encoding="utf-8",
    )

    handle = cache.get_or_produce(kind, _Spec(), None)
    assert handle.origin is ArtifactOrigin.PRODUCED
    assert counter.calls == 1
    assert not lock_dir.exists()


def test_rehydrate_refetches_every_reference(tmp_path: Path) -> None:
    cache = ArtifactCache(LocalArtifactStore(tmp_path / "store"))
    kind = _kind(_Counter())
    handle = cache.get_or_produce(kind, _Spec(), None)

    into = tmp_path / "again"
    restored = cache.rehydrate(handle.ref, into, kind)
    assert restored.origin is ArtifactOrigin.LOCAL
    assert (into / "out.txt").is_file()

    with pytest.raises(ArtifactError, match="not a 'counter' artifact"):
        _ = cache.rehydrate(
            ArtifactRef(kind="mystery", key="0" * 64, origin=ArtifactOrigin.LOCAL),
            into,
            kind,
        )
    with pytest.raises(ArtifactMissing, match="in no tier"):
        _ = cache.rehydrate(
            ArtifactRef(kind="counter", key="0" * 64, origin=ArtifactOrigin.LOCAL),
            into,
            kind,
        )
