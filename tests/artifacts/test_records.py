"""RecordKind: one JSON per spec, produced once, loadable by spec and enumerable across the local tier."""

from pathlib import Path

from pydantic import BaseModel

from femforge.artifacts.cache import ArtifactCache
from femforge.artifacts.keys import ArtifactKey
from femforge.artifacts.local import LocalArtifactStore
from femforge.artifacts.records import RECORD_FILE, json_record_kind
from femforge.artifacts.sidecar import ARTIFACT_FILE, ArtifactOrigin


class _Params(BaseModel):
    diameter: float = 12.7


class _Outcome(BaseModel):
    force: float


POINTS = json_record_kind("points", spec_type=_Params, result_type=_Outcome)


def test_get_or_produce_runs_once_per_spec(tmp_path: Path) -> None:
    cache = ArtifactCache(LocalArtifactStore(tmp_path))
    calls: list[float] = []

    def solve(params: _Params) -> _Outcome:
        calls.append(params.diameter)
        return _Outcome(force=params.diameter * 2)

    first = POINTS.get_or_produce(cache, _Params(), solve)
    assert first.value == _Outcome(force=25.4)
    assert first.origin is ArtifactOrigin.PRODUCED
    assert first.key == POINTS.key(_Params())
    assert isinstance(first.key, ArtifactKey)

    again = POINTS.get_or_produce(cache, _Params(), solve)
    assert again.value == first.value
    assert again.origin is ArtifactOrigin.LOCAL
    assert calls == [12.7]

    assert (cache.local.path(POINTS.kind, first.key) / RECORD_FILE).is_file()
    assert POINTS.kind.required_files == (RECORD_FILE,)


def test_load_and_load_all(tmp_path: Path) -> None:
    cache = ArtifactCache(LocalArtifactStore(tmp_path))
    assert POINTS.load(cache, _Params()) is None

    _ = POINTS.get_or_produce(cache, _Params(diameter=1.0), lambda p: _Outcome(force=p.diameter))
    _ = POINTS.get_or_produce(cache, _Params(diameter=2.0), lambda p: _Outcome(force=p.diameter))
    assert POINTS.load(cache, _Params(diameter=2.0)) == _Outcome(force=2.0)

    broken = tmp_path / "points" / ArtifactKey[_Params]("f" * 64).name
    broken.mkdir()
    _ = (broken / ARTIFACT_FILE).write_text("{garbage", encoding="utf-8")

    loaded = list(POINTS.load_all(cache))
    assert sorted(value.force for _, value in loaded) == [1.0, 2.0]
    assert {record.spec["diameter"] for record, _ in loaded} == {1.0, 2.0}
