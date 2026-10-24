"""One run's convergence log read into a frame, with the sif-authored targets that give its progress a meaning."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self, cast

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from femforge.common.files import BundleFile, write_atomic
from femforge.elmer.solver_log import LoopTargets, convergence_progress, normalize_solver_key
from femforge.telemetry.journal import journal_lines, records_frame
from femforge.telemetry.schemas import CONVERGENCE_SCHEMA, ConvergenceColumn, ConvergenceLoop


if TYPE_CHECKING:
    from femforge.elmer.solver_log import ConvergenceTargets


__all__ = ["ConvergenceTargetsFile", "DerivedColumn", "LoopTarget", "scan_convergence"]


class DerivedColumn(StrEnum):
    """The columns ``scan_convergence`` derives on top of what the bundle recorded."""

    FIRST_RELC = "first_relc"
    PROGRESS = "progress"


class LoopTarget(BaseModel):
    """One solver loop's authored iteration cap and tolerance, in the form the bundle persists them."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    solver: str
    """The sif ``Equation`` name Elmer tags this loop's ComputeChange lines with, normalized."""

    loop: ConvergenceLoop
    """Which loop the targets apply to: the nonlinear inner loop or the steady-state outer one."""

    max_iterations: int | None = None
    """The loop's iteration cap; None when the sif left Elmer's default."""

    tolerance: float | None = None
    """`Δ_tol` `[-]`: The relative-change tolerance the loop converges to; None when the sif left Elmer's default."""


class ConvergenceTargetsFile(BaseModel):
    """The iteration caps and tolerances one run's sif authored, written beside its convergence log so the log reads in context."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    loops: list[LoopTarget] = Field(default_factory=list)
    """One entry per (solver, loop) the solved stack authored targets for."""

    @classmethod
    def of(cls, targets: ConvergenceTargets) -> Self:
        """The persistable form of the mapping ``convergence_targets`` builds from a solver stack."""
        loops: list[LoopTarget] = []
        for (solver, loop), target in targets.items():
            loops.append(
                LoopTarget(
                    solver=solver,
                    loop=ConvergenceLoop(loop),
                    max_iterations=target.max_iterations,
                    tolerance=target.tolerance,
                )
            )

        return cls(loops=loops)

    @classmethod
    def load(cls, path: str | Path) -> Self:
        """Load a bundle's ``convergence_targets.json`` (pass the bundle directory or the file itself); a bundle without one reads as no targets."""
        file: Path = Path(path)
        if file.is_dir():
            file = file / BundleFile.CONVERGENCE_TARGETS

        if not file.is_file():
            return cls()

        return cls.model_validate_json(file.read_text(encoding="utf-8"))

    def write(self, sim_dir: str | Path) -> Path:
        """Write the targets into ``sim_dir`` as ``convergence_targets.json`` and return the path."""
        path: Path = Path(sim_dir) / BundleFile.CONVERGENCE_TARGETS
        write_atomic(path, self.model_dump_json(indent=2))

        return path

    def targets(self) -> ConvergenceTargets:
        """The renderer's mapping again, keyed the way Elmer tags ComputeChange lines."""
        mapping: dict[tuple[str, Literal["NS", "SS"]], LoopTargets] = {}
        for entry in self.loops:
            loop: Literal["NS", "SS"] = "NS" if entry.loop is ConvergenceLoop.NS else "SS"
            mapping[(normalize_solver_key(entry.solver), loop)] = LoopTargets(max_iterations=entry.max_iterations, tolerance=entry.tolerance)

        return mapping


def frame_schema(*, with_progress: bool) -> pl.Schema:
    """The schema ``scan_convergence`` returns, so an empty bundle still hands back a typed frame."""
    if not with_progress:
        return CONVERGENCE_SCHEMA

    return pl.Schema({**CONVERGENCE_SCHEMA, DerivedColumn.FIRST_RELC: pl.Float64, DerivedColumn.PROGRESS: pl.Float64})


def scan_convergence(sim_dir: str | Path, *, with_progress: bool = True) -> pl.DataFrame:
    """A run's ``convergence.jsonl`` as a typed frame, carrying each loop's first relative change and how far it has come toward its tolerance."""
    bundle: Path = Path(sim_dir)
    path: Path = bundle / BundleFile.CONVERGENCE
    if not path.is_file() or path.stat().st_size == 0:
        return pl.DataFrame(schema=frame_schema(with_progress=with_progress))

    # The run monitor reads this file while the solve appends to it, so a torn last line is skipped, not fatal.
    samples: pl.DataFrame = records_frame(journal_lines(path)[0], CONVERGENCE_SCHEMA).collect()
    if not with_progress:
        return samples

    # Iteration 1 (re)sets the baseline, so a nonlinear loop restarting inside a steady-state sweep is measured from its own start.
    baseline = pl.when(pl.col(ConvergenceColumn.ITER) == 1).then(pl.col(ConvergenceColumn.RELC)).otherwise(None)
    scored: pl.DataFrame = samples.with_columns(baseline.forward_fill().over([ConvergenceColumn.SOLVER, ConvergenceColumn.LOOP]).alias(DerivedColumn.FIRST_RELC))

    lookup: ConvergenceTargets = ConvergenceTargetsFile.load(bundle).targets()
    solvers: list[str | None] = cast("list[str | None]", scored[ConvergenceColumn.SOLVER].to_list())
    loops: list[str | None] = cast("list[str | None]", scored[ConvergenceColumn.LOOP].to_list())
    firsts: list[float | None] = cast("list[float | None]", scored[DerivedColumn.FIRST_RELC].to_list())
    changes: list[float | None] = cast("list[float | None]", scored[ConvergenceColumn.RELC].to_list())

    def tolerance(solver: str | None, loop: str | None) -> float | None:
        """What this sample's loop was authored to converge to, or None when the sif left Elmer's default."""
        target: LoopTargets | None = lookup.get((normalize_solver_key(solver or ""), cast("Literal['NS', 'SS']", loop)))

        return target.tolerance if target is not None else None

    covered: list[float | None] = []
    for solver, loop, first, relc in zip(solvers, loops, firsts, changes, strict=True):
        covered.append(convergence_progress(first, relc, tolerance(solver, loop)) if relc is not None else None)

    return scored.with_columns(pl.Series(DerivedColumn.PROGRESS, covered, dtype=pl.Float64))
