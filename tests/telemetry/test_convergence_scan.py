"""Tests for telemetry.convergence: the bundle's convergence log as a frame, and the targets file that gives its progress a meaning."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal, cast

from femforge.common.files import BundleFile
from femforge.elmer.solver_log import ConvergenceRenderer, ConvergenceSample, LoopTargets
from femforge.telemetry.convergence import ConvergenceTargetsFile, DerivedColumn, scan_convergence
from femforge.telemetry.schemas import CONVERGENCE_SCHEMA, ConvergenceColumn, ConvergenceLoop


if TYPE_CHECKING:
    from pathlib import Path

    from femforge.elmer.solver_log import ConvergenceTargets


SAMPLES: tuple[tuple[str, Literal["NS", "SS"], int, float], ...] = (
    ("heat equation", "NS", 1, 1e-1),
    ("heat equation", "NS", 2, 1e-3),
    ("heat equation", "NS", 1, 2e-1),
    ("heat equation", "NS", 2, 1e-6),
    ("mystery", "SS", 1, 1e-2),
)
"""Two solvers, one of them restarting its nonlinear loop; only the first has authored targets."""

TARGETS: ConvergenceTargets = {
    ("heat equation", "NS"): LoopTargets(max_iterations=20, tolerance=1e-5),
    ("heat equation", "SS"): LoopTargets(max_iterations=3, tolerance=1e-4),
}


def _bundle(tmp_path: Path, *, targets: bool = True, extra: str = "") -> Path:
    """A bundle holding a convergence log and, unless asked otherwise, the targets its sif authored."""
    sim_dir: Path = tmp_path / "run"
    sim_dir.mkdir()

    lines = [json.dumps({"wall_time": index / 10, "solver": solver, "loop": loop, "iter": iteration, "nrm": 1.0, "relc": relc}) for index, (solver, loop, iteration, relc) in enumerate(SAMPLES)]
    _ = (sim_dir / BundleFile.CONVERGENCE).write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")

    if targets:
        _ = ConvergenceTargetsFile.of(TARGETS).write(sim_dir)

    return sim_dir


def _renderer_percents() -> list[str]:
    """What the terminal renderer prints as the percent column for the same samples, in order."""
    renderer = ConvergenceRenderer(TARGETS)

    percents: list[str] = []
    for solver, loop, iteration, relc in SAMPLES:
        sample = ConvergenceSample(
            solver=solver,
            loop=loop,
            iteration=iteration,
            norm=1.0,
            relative_change=relc,
            wall_time=0.0,
        )
        line: str = renderer.render(sample)
        tail: str = line.rsplit(" ", 1)[-1]
        percents.append(tail if tail.endswith("%") else "")

    return percents


def test_the_log_reads_as_the_typed_frame_the_solver_wrote(tmp_path: Path) -> None:
    """Every key elmer.runtime.solve writes becomes a column, with the enum categories the store shares."""
    frame = scan_convergence(_bundle(tmp_path), with_progress=False)

    assert frame.schema == CONVERGENCE_SCHEMA
    assert frame.height == len(SAMPLES)
    assert frame.get_column(ConvergenceColumn.LOOP).to_list() == ["NS", "NS", "NS", "NS", "SS"]
    assert frame.get_column(ConvergenceColumn.ITER).to_list() == [1, 2, 1, 2, 1]


def test_the_first_relative_change_restarts_with_each_loop(tmp_path: Path) -> None:
    """Iteration 1 re-sets the baseline, so a nonlinear loop restarting inside a steady-state sweep measures from its own start."""
    frame = scan_convergence(_bundle(tmp_path))

    assert frame.get_column(DerivedColumn.FIRST_RELC).to_list() == [1e-1, 1e-1, 2e-1, 2e-1, 1e-2]


def test_progress_is_exactly_what_the_terminal_renderer_shows(tmp_path: Path) -> None:
    """The frame reuses the renderer's log-scale measure, so a stored run and a live one report the same number."""
    frame = scan_convergence(_bundle(tmp_path))
    covered = cast("list[float | None]", frame.get_column(DerivedColumn.PROGRESS).to_list())

    assert [f"{value * 100:.0f}%" if value is not None else "" for value in covered] == _renderer_percents()
    assert covered[3] == 1.0
    assert covered[4] is None


def test_a_sample_without_a_relative_change_has_no_progress(tmp_path: Path) -> None:
    """A malformed line still lands as a row; it just cannot be scored."""
    sim_dir = _bundle(tmp_path, extra=json.dumps({"wall_time": 9.0, "solver": "heat equation", "loop": "NS", "iter": 3, "nrm": 1.0, "relc": None}) + "\n")

    frame = scan_convergence(sim_dir)

    assert frame.height == len(SAMPLES) + 1
    assert cast("list[float | None]", frame.get_column(DerivedColumn.PROGRESS).to_list())[-1] is None


def test_a_half_written_last_line_is_skipped(tmp_path: Path) -> None:
    """The run monitor reads the log while the solve appends to it, so a torn tail reads as the samples before it."""
    sim_dir = _bundle(tmp_path, extra='{"wall_time": 9.0, "solver": "heat eq')

    assert scan_convergence(sim_dir).height == len(SAMPLES)


def test_a_bundle_without_targets_scores_nothing(tmp_path: Path) -> None:
    """A run solved before the targets file existed still reads; only its progress column is empty."""
    frame = scan_convergence(_bundle(tmp_path, targets=False))

    assert frame.get_column(DerivedColumn.PROGRESS).to_list() == [None] * len(SAMPLES)
    assert ConvergenceTargetsFile.load(tmp_path / "run").loops == []


def test_a_bundle_with_no_convergence_log_reads_as_a_typed_empty_frame(tmp_path: Path) -> None:
    """A missing or empty log is not an error; the frame still carries the columns a caller selects."""
    sim_dir = tmp_path / "run"
    sim_dir.mkdir()

    assert scan_convergence(sim_dir).height == 0
    assert scan_convergence(sim_dir, with_progress=False).schema == CONVERGENCE_SCHEMA
    assert DerivedColumn.PROGRESS in scan_convergence(sim_dir).columns

    _ = (sim_dir / BundleFile.CONVERGENCE).write_text("", encoding="utf-8")

    assert scan_convergence(sim_dir).height == 0


def test_the_targets_file_round_trips_through_the_bundle(tmp_path: Path) -> None:
    """What the sif authored is persisted beside the log, and reads back as the mapping the renderer takes."""
    sim_dir = tmp_path / "run"
    sim_dir.mkdir()

    path = ConvergenceTargetsFile.of(TARGETS).write(sim_dir)
    loaded = ConvergenceTargetsFile.load(path)

    assert path.name == BundleFile.CONVERGENCE_TARGETS
    assert loaded.targets() == TARGETS
    assert {entry.loop for entry in loaded.loops} == {ConvergenceLoop.NS, ConvergenceLoop.SS}
