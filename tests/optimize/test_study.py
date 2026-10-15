"""A Study tracks one record per evaluated point; best() is argmax/argmin and to_dataframe() strips units."""

from collections.abc import Hashable

import pandas as pd
import pytest

from femforge.common.frame import Solid
from femforge.common.vector import Vec3
from femforge.optimize.study import Study
from femforge.physical.units import U, quantity
from femforge.run.result import Result


def _rows(df: pd.DataFrame) -> list[dict[Hashable, object]]:
    """Cross the pandas boundary once into a plain typed structure."""
    return df.to_dict(orient="records")


def _study() -> Study:
    study = Study()
    study.record(point={"g": 0.5}, run_id="run-a", result=Result(frame=Solid(), status="ok", force=quantity(Vec3(2.0, 0.0, 0.0), U.N)), objective=2.0)
    study.record(point={"g": 1.0}, run_id="run-b", result=Result(frame=Solid(), status="ok", force=quantity(Vec3(5.0, 0.0, 0.0), U.N)), objective=5.0)
    study.record(point={"g": 2.0}, run_id="run-c", result=Result(frame=Solid(), status="ok", force=quantity(Vec3(1.0, 0.0, 0.0), U.N)), objective=1.0)
    return study


def test_best_is_argmax_by_default() -> None:
    best = _study().best()
    assert best.run_id == "run-b"
    assert best.objective == pytest.approx(5.0)


def test_best_argmin_when_not_maximizing() -> None:
    best = _study().best(maximize=False)
    assert best.run_id == "run-c"


def test_best_raises_on_empty_study() -> None:
    with pytest.raises(ValueError, match="no records"):
        Study().best()


def test_to_dataframe_shape_and_columns() -> None:
    df = _study().to_dataframe()
    assert len(df) == 3
    # The knob column, the bookkeeping columns, and the units-stripped result quantities are all present.
    for column in ("g", "run_id", "objective", "status", "force", "force_x", "torque", "flux_density_max", "residual", "wall_time"):
        assert column in df.columns
    # run_id links resolve: every record's id appears in the table.
    rows = _rows(df)
    assert {row["run_id"] for row in rows} == {"run-a", "run-b", "run-c"}
    # Force was stripped to a bare SI float (magnitude in N).
    row_b = next(row for row in rows if row["run_id"] == "run-b")
    assert row_b["force"] == pytest.approx(5.0)


def test_to_dataframe_missing_quantities_are_none() -> None:
    study = Study()
    study.record(point={"g": 0.5}, run_id="run-x", result=Result(frame=Solid(), status="error", force=None), objective=float("-inf"))
    row = _rows(study.to_dataframe())[0]
    assert row["force"] is None
    assert row["torque"] is None
    assert row["flux_density_max"] is None
