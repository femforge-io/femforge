"""The Study: a queryable log of (point, result, objective, run_id) records across an optimization run."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from femforge.optimize.parameterization import ParamSpec
from femforge.physical.units import U
from femforge.run.result import Result


if TYPE_CHECKING:
    import pandas as pd


class StudyRecord(BaseModel):
    """One evaluated point: where in parameter space, the run it produced, the objective scalar, and the full typed result."""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    point: dict[str, float]
    """The parameter-space coordinate (knob name → value) that produced this run."""

    run_id: str
    """Content-addressed id of the run bundle (== run.cache_key of the evaluated config)."""

    objective: float
    """The objective scalar for this run (the quantity the search optimizes)."""

    result: Result
    """The full typed result, units intact; stripped to floats only at to_dataframe."""


class Study(BaseModel):
    """
    The queryable log of an optimization run: one [`StudyRecord`][] per evaluated point.

    [`best`][] is the argmax/argmin over the objective; [`to_dataframe`][] flattens to a units-stripped table for analysis and plotting.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    space: dict[str, ParamSpec] = Field(default_factory=dict)
    """The searched knobs' bounds and units by knob name, as [`Parameterization.space`][femforge.optimize.Parameterization.space] declared them."""

    records: list[StudyRecord] = Field(default_factory=list)
    """Every evaluated point, in evaluation order."""

    def record(self, *, point: dict[str, float], run_id: str, result: Result, objective: float) -> StudyRecord:
        """Append and return a [`StudyRecord`][] for one evaluated point.

        Args:
            point: The parameter-space coordinate, by knob name.
            run_id: The run bundle's content-addressed id.
            result: The run's typed result.
            objective: The run's objective scalar.
        """
        entry: StudyRecord = StudyRecord(point=point, run_id=run_id, result=result, objective=objective)
        self.records.append(entry)
        return entry

    def best(self, *, maximize: bool = True) -> StudyRecord:
        """The record with the extremal objective: argmax by default, argmin when ``maximize=False``."""
        if not self.records:
            raise ValueError("study has no records")
        chooser = max if maximize else min
        return chooser(self.records, key=lambda entry: entry.objective)

    def to_dataframe(self) -> pd.DataFrame:
        """Flatten the records to a pandas DataFrame, the units-stripping analysis boundary.

        One row per record: knob columns in the units ``attrs["units"]`` names, then ``objective``, ``run_id``, ``status`` and the result quantities as bare SI floats.
        """
        import pandas as pd

        rows: list[dict[str, object]] = []
        for entry in self.records:
            row: dict[str, object] = dict(entry.point)
            row["run_id"] = entry.run_id
            row["objective"] = entry.objective
            row["status"] = entry.result.status.value

            force = entry.result.force
            row["force"] = force.to(U.N).magnitude.magnitude() if force is not None else None
            row["force_x"] = force.to(U.N).magnitude.x if force is not None else None
            row["force_y"] = force.to(U.N).magnitude.y if force is not None else None
            row["force_z"] = force.to(U.N).magnitude.z if force is not None else None

            torque = entry.result.torque
            row["torque"] = torque.to(U.N * U.m).magnitude.magnitude() if torque is not None else None

            flux_density_max = entry.result.flux_density_max
            row["flux_density_max"] = flux_density_max.to(U.T).magnitude if flux_density_max is not None else None

            row["residual"] = entry.result.residual
            row["wall_time"] = entry.result.wall_time
            rows.append(row)

        frame = pd.DataFrame(rows)
        frame.attrs["units"] = {name: spec.unit for name, spec in self.space.items()}
        return frame
