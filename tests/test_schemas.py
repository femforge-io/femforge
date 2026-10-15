"""The schema export covers every registered model and writes valid JSON."""

import json
from pathlib import Path
from typing import cast

import pytest

import femforge.schemas as schemas


def test_all_schemas_exports_every_model() -> None:
    out: dict[str, dict[str, object]] = schemas.all_schemas()
    assert set(out) == set(schemas.SCHEMA_MODELS)


def test_model_schema_unknown_name_raises() -> None:
    # An unregistered model name raises KeyError naming the available models.
    with pytest.raises(KeyError, match="unknown schema"):
        schemas.model_schema("NoSuchModel")


def test_dump_schemas_writes_files(tmp_path: Path) -> None:
    out_dir: Path = tmp_path
    written: list[Path] = schemas.dump_schemas(out_dir)
    assert {p.name for p in written} == {f"{n}.json" for n in schemas.SCHEMA_MODELS}
    # Each written file is real, non-empty, and valid JSON.
    for path in written:
        assert path.is_file()
        parsed: object = cast("object", json.loads(path.read_text(encoding="utf-8")))
        assert isinstance(parsed, dict)


def test_main_prints_all_schemas(capsys: pytest.CaptureFixture[str]) -> None:
    # No args prints every schema as one JSON object.
    rc: int = schemas.main([])
    assert rc == 0
    printed: object = cast("object", json.loads(capsys.readouterr().out))
    assert isinstance(printed, dict)
    assert set(cast("dict[str, object]", printed)) == set(schemas.SCHEMA_MODELS)


def test_main_prints_single_schema(capsys: pytest.CaptureFixture[str]) -> None:
    # A single model name prints just that model's schema.
    rc: int = schemas.main(["SimulationConfig"])
    assert rc == 0
    printed: object = cast("object", json.loads(capsys.readouterr().out))
    assert printed == schemas.model_schema("SimulationConfig")


def test_main_writes_schemas_to_out_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # --out DIR writes <Name>.json for every model and reports each path.
    rc: int = schemas.main(["--out", str(tmp_path)])
    assert rc == 0
    for name in schemas.SCHEMA_MODELS:
        assert (tmp_path / f"{name}.json").is_file()
    out: str = capsys.readouterr().out
    assert "wrote" in out
