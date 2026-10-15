"""Optimization seam: a ``Parameterization`` turns a point into a ``SimulationConfig``, an ``Objective`` scores its ``Result``, and ``GridSearch`` records each point in a ``Study``."""

from __future__ import annotations

from femforge.optimize.objective import NetForceMagnitude, Objective, PhaseResistanceFromPower
from femforge.optimize.parameterization import GlobalMeshSize, Knob, Parameterization, ParamSpec, RegionMeshSize
from femforge.optimize.search import DriverLike, GridSearch
from femforge.optimize.study import Study, StudyRecord


__all__ = [
    "Parameterization",
    "ParamSpec",
    "Knob",
    "GlobalMeshSize",
    "RegionMeshSize",
    "Objective",
    "NetForceMagnitude",
    "PhaseResistanceFromPower",
    "Study",
    "StudyRecord",
    "GridSearch",
    "DriverLike",
]
