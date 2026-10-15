"""Pipeline stage vocabulary: the driver's stages in execution order, plus the timed event a run records per stage."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Stage(StrEnum):
    """The driver's pipeline stages, in execution order."""

    PREPARE_GEOMETRY = "prepare_geometry"
    """Build or fetch the solve-frame STEP."""

    PRE_MESH = "pre_mesh"
    """A hook before meshing."""

    MESH = "mesh"
    """Mesh the STEP and convert it with ElmerGrid, or fetch the cached mesh."""

    POST_MESH = "post_mesh"
    """A hook between meshing and building the sim."""

    BUILD_SIM = "build_sim"
    """Build the typed sim from the resolved solver stack."""

    CONFIGURE_SIM = "configure_sim"
    """A hook to edit the solver stack before the sif is written."""

    WRITE_SIF = "write_sif"
    """Write the sif into the run directory."""

    RUN_SOLVER = "run_solver"
    """Run ElmerSolver, partitioning the mesh first for a parallel solve."""

    MESH_STATS = "mesh_stats"
    """Record the mesh size summary."""

    PUBLISH = "publish"
    """Commit the bundle to the artifact cache."""

    CLEANUP = "cleanup"
    """Apply the retention policy."""


class StageEvent(BaseModel):
    """One stage's timing record: created at stage start, mutated with ``finished_at`` when the stage completes."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    stage: Stage
    """Which pipeline stage this event times."""

    started_at: datetime
    """When the stage started (UTC)."""

    finished_at: datetime | None = None
    """When the stage finished (UTC); None while it runs — or forever, if it crashed."""
