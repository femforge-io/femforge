"""The ElmerGrid argument builders and the checks on what a conversion or a partition run leaves behind."""

from __future__ import annotations

from pathlib import Path

import pytest

from femforge.elmer.runtime.commands import check_partitions, finish_grid_convert, grid_convert_args, grid_partition_args
from femforge.execution.streaming import CommandError, CommandResult


def _result(log_path: Path, *, returncode: int = 0, timed_out: bool = False, cancelled: bool = False) -> CommandResult:
    _ = log_path.write_text("elmergrid output\n", encoding="utf-8")

    return CommandResult(
        returncode=returncode,
        duration_s=0.0,
        log_path=log_path,
        timed_out=timed_out,
        cancelled=cancelled,
    )


def _converted(mesh_dir: Path) -> Path:
    produced = mesh_dir / "model"
    produced.mkdir()
    for name in ("mesh.header", "mesh.nodes"):
        _ = (produced / name).write_text(name, encoding="utf-8")

    return produced


def test_grid_convert_args_add_the_scale_and_autoclean_on_request() -> None:
    assert grid_convert_args("model.msh") == ["14", "2", "model.msh"]
    assert grid_convert_args("model.msh", scale=0.001, autoclean=True) == ["14", "2", "model.msh", "-scale", "0.001", "0.001", "0.001", "-autoclean"]


def test_finish_grid_convert_moves_the_mesh_files_into_out_dir(tmp_path: Path) -> None:
    produced = _converted(tmp_path)
    out_dir = tmp_path / "bundle"
    out_dir.mkdir()
    _ = (out_dir / "mesh.header").write_text("stale", encoding="utf-8")
    result = _result(out_dir / "elmergrid.log")

    finished = finish_grid_convert(result, tmp_path, "model.msh", out_dir=out_dir, keep_mesh_dir=False)

    assert finished is result
    assert (out_dir / "mesh.header").read_text(encoding="utf-8") == "mesh.header"
    assert (out_dir / "mesh.nodes").is_file()
    assert not produced.exists()


def test_finish_grid_convert_keeps_the_mesh_dir_on_request(tmp_path: Path) -> None:
    produced = _converted(tmp_path)

    _ = finish_grid_convert(_result(tmp_path / "elmergrid.log"), tmp_path, "model.msh", out_dir=tmp_path, keep_mesh_dir=True)

    assert (produced / "mesh.header").is_file()


def test_finish_grid_convert_raises_on_a_failed_run(tmp_path: Path) -> None:
    _ = _converted(tmp_path)

    with pytest.raises(CommandError, match="ElmerGrid exited with code 3") as raised:
        _ = finish_grid_convert(_result(tmp_path / "elmergrid.log", returncode=3), tmp_path, "model.msh", out_dir=tmp_path, keep_mesh_dir=False)

    assert raised.value.returncode == 3
    assert "elmergrid output" in raised.value.log_tail

    with pytest.raises(CommandError, match="after timeout"):
        _ = finish_grid_convert(_result(tmp_path / "elmergrid.log", returncode=-9, timed_out=True), tmp_path, "model.msh", out_dir=tmp_path, keep_mesh_dir=False)


def test_finish_grid_convert_raises_when_no_mesh_dir_appeared(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="no mesh directory"):
        _ = finish_grid_convert(_result(tmp_path / "elmergrid.log"), tmp_path, "model.msh", out_dir=tmp_path, keep_mesh_dir=False)


def test_grid_partition_args_name_the_mesh_and_add_halos_on_request() -> None:
    assert grid_partition_args("case", 4, halobc=False) == ["2", "2", "case", "-metiskway", "4"]
    assert grid_partition_args("case", 8, halobc=True) == ["2", "2", "case", "-metiskway", "8", "-halobc"]


def test_grid_partition_args_refuse_a_dotted_mesh_name() -> None:
    with pytest.raises(ValueError, match="first dot"):
        _ = grid_partition_args("run.v2", 4, halobc=False)


def test_check_partitions_needs_a_clean_run_and_the_partition_dir(tmp_path: Path) -> None:
    result = _result(tmp_path / "elmergrid_partition.log")

    with pytest.raises(CommandError, match="does not exist"):
        _ = check_partitions(result, tmp_path, 4)

    (tmp_path / "partitioning.4").mkdir()
    assert check_partitions(result, tmp_path, 4) is result

    with pytest.raises(CommandError, match="after cancellation"):
        _ = check_partitions(_result(tmp_path / "elmergrid_partition.log", returncode=-9, cancelled=True), tmp_path, 4)
