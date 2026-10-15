"""The sidecar record and the consumer ref carry the store directory name beside the key, always derived from it."""

import json
from pathlib import Path
from typing import cast

from femforge.artifacts.sidecar import ArtifactOrigin, ArtifactRecord, ArtifactRef
from femforge.common.names import bundle_name


KEY = "8acca40980b48a2fa62d9cac89fbeddb1b1e57f97dbf9f0e98c079045895511e"


def test_ref_name_is_derived_from_the_key_and_serialized_beside_it() -> None:
    ref = ArtifactRef(kind="elmer_mesh", key=KEY, origin=ArtifactOrigin.PRODUCED)
    assert ref.name == bundle_name(KEY) == "dark-mamba-8acca409"

    payload = cast("dict[str, object]", json.loads(ref.model_dump_json()))
    assert list(payload)[:3] == ["kind", "key", "name"]
    assert payload["name"] == "dark-mamba-8acca409"


def test_ref_name_given_on_input_is_recomputed() -> None:
    ref = ArtifactRef.model_validate({"kind": "step", "key": KEY, "name": "stale-name-00000000", "origin": "local"})
    assert ref.name == "dark-mamba-8acca409"
    assert ref == ArtifactRef(kind="step", key=KEY, origin=ArtifactOrigin.LOCAL)


def test_record_name_is_derived_and_survives_a_round_trip(tmp_path: Path) -> None:
    _ = (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    record = ArtifactRecord.build(
        kind="things",
        key=KEY,
        producer_version=1,
        spec_type="t:_Spec",
        spec={},
        workdir=tmp_path,
        files=["a.txt"],
    )
    assert record.name == "dark-mamba-8acca409"

    payload = cast("dict[str, object]", json.loads(record.model_dump_json()))
    assert list(payload)[:3] == ["kind", "key", "name"]

    payload["name"] = "edited-by-hand-00000000"
    assert ArtifactRecord.model_validate(payload).name == "dark-mamba-8acca409"
