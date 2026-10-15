"""The escape-hatch solver block for anything the typed hierarchy doesn't model yet."""

from __future__ import annotations

from typing import Literal

from femforge.elmer.config.common.solver_base import LinearSystem, NonlinearSystem, SolverBase, SteadyState


class GenericSolver(SolverBase, LinearSystem, NonlinearSystem, SteadyState):
    """
    Escape hatch: an arbitrary Elmer solver block for anything the typed hierarchy doesn't model yet — set
    ``equation``/``procedure`` and author the rest through ``extra_keywords`` (plus the shared typed knobs).
    """

    kind: Literal["Generic"] = "Generic"
    """Discriminator that names this solver, doubling as its sif Solver label."""
