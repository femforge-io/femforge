"""A naive grid search: walk the Cartesian product of the grid through the driver and record everything in a [`Study`][femforge.optimize.study.Study]."""

from __future__ import annotations

import itertools
from typing import Protocol

from femforge.common.names import bundle_name
from femforge.config import SimulationConfig
from femforge.optimize.objective import Objective
from femforge.optimize.parameterization import Parameterization
from femforge.optimize.study import Study
from femforge.run.manifest import cache_key
from femforge.run.result import Result


class DriverLike(Protocol):
    """The slice of [`run.Driver`][femforge.run.Driver] a grid search needs: ``evaluate``."""

    def evaluate(self, config: SimulationConfig) -> Result:
        """Solve ``config`` and return its result."""
        ...


class GridSearch:
    """Walk the full Cartesian product of ``grid`` (knob name -> sweep values) through the driver, recording one [`StudyRecord`][femforge.optimize.study.StudyRecord] per point."""

    def __init__(self, *, parameterization: Parameterization, grid: dict[str, list[float]], objective: Objective) -> None:
        """Sweep ``grid`` through ``parameterization``, scoring each point with ``objective``.

        Args:
            parameterization: Turns each grid point into a config.
            grid: The values to sweep, by knob name.
            objective: Scores each result.
        """
        self.parameterization: Parameterization = parameterization
        self.grid: dict[str, list[float]] = grid
        self.objective: Objective = objective

    def run(self, driver: DriverLike) -> Study:
        """Evaluate every grid point and return the populated [`Study`][femforge.optimize.study.Study].

        Each record keeps the ``run_id`` the driver stamped, recomputed with [`run.cache_key`][femforge.run.cache_key] when a stand-in stamps none.
        """
        study: Study = Study(space=self.parameterization.space())
        names: list[str] = list(self.grid.keys())

        for combo in itertools.product(*(self.grid[name] for name in names)):
            point: dict[str, float] = dict(zip(names, combo))
            config: SimulationConfig = self.parameterization.to_config(point)
            result: Result = driver.evaluate(config)

            run_id: str = result.run_id if result.run_id is not None else bundle_name(cache_key(config))
            study.record(
                point=point,
                run_id=run_id,
                result=result,
                objective=self.objective(result),
            )

        return study
