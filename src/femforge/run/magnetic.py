"""Per-component magnetic quantities of a magnetostatics run, joined from the SaveScalars columns in ``Result.raw`` and ``components.yml``."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Mapping

from femforge.common.vector import Vec3
from femforge.elmer.variables import ElmerVariable
from femforge.run.electrical import read_boundary_names


# The trailing component index on a vector column name, e.g. "res: magnet force 2".
_COMPONENT_RE = re.compile(r"(.*?)\s+([123])\s*$")


def _vector_triples(raw: Mapping[str, float], keyword: str) -> dict[str, Vec3]:
    """Column stem -> Vec3 for every ``... <keyword> 1/2/3`` triple, excluding the global ``nodal <keyword>`` sum (that one is ``Result.force``/``.torque``)."""
    components: dict[str, dict[int, float]] = {}
    for name, value in raw.items():
        match: re.Match[str] | None = _COMPONENT_RE.match(name)
        if match is None:
            continue
        stem: str = match.group(1).strip()
        low: str = stem.lower()
        if keyword not in low or f"nodal {keyword}" in low:
            continue
        components.setdefault(stem, {})[int(match.group(2))] = value
    return {stem: Vec3(triple.get(1, 0.0), triple.get(2, 0.0), triple.get(3, 0.0)) for stem, triple in components.items()}


def force_triples(raw: Mapping[str, float]) -> dict[str, Vec3]:
    """Column stem -> per-component force Vec3 (N, per radian in an axisymmetric run) from the SaveScalars columns in ``Result.raw``."""
    return _vector_triples(raw, "force")


def revolve(vec: Vec3) -> Vec3:
    """Full-revolution total of a per-radian axisymmetric vector integral: the axial (y) component picks up 2π and the transverse ones cancel to zero."""
    return Vec3(0.0, math.tau * vec.y, 0.0)


def _join_components(triples: Mapping[str, Vec3], names_by_component: Mapping[int, str]) -> dict[str, Vec3]:
    """Component name -> Vec3, matched by the name (case-insensitive) or ``component <id>`` appearing in a column stem — deliberately loose, Elmer's exact column wording is unpinned in-repo."""
    out: dict[str, Vec3] = {}
    for component_id, name in names_by_component.items():
        for stem, vec in triples.items():
            low: str = stem.lower()
            if name.lower() in low or f"component {component_id}" in low:
                out[name] = vec
                break
    return out


def component_forces(raw: Mapping[str, float], names_by_component: Mapping[int, str]) -> dict[str, Vec3]:
    """Component name -> lumped magnetic force (N, per radian in an axisymmetric run), joining the force triples against the ``components.yml`` map."""
    return _join_components(force_triples(raw), names_by_component)


def component_torques(raw: Mapping[str, float], names_by_component: Mapping[int, str]) -> dict[str, Vec3]:
    """Component name -> lumped magnetic torque (N*m about the authored origin/axis, per radian in an axisymmetric run), joining the torque triples against the ``components.yml`` map."""
    return _join_components(_vector_triples(raw, "torque"), names_by_component)


def magnetic_field_energy(raw: Mapping[str, float]) -> float | None:
    """The total magnetic field energy (J, per radian in an axisymmetric run), or None when the run exported none."""
    return next((value for name, value in raw.items() if ElmerVariable.MAGNETIC_FIELD_ENERGY.lower() in name.lower()), None)


def eddy_current_power(raw: Mapping[str, float]) -> float | None:
    """The total eddy current power (W, per radian in an axisymmetric run), or None when the run exported none (static solves have no eddy losses)."""
    return next((value for name, value in raw.items() if ElmerVariable.EDDY_CURRENT_POWER.lower() in name.lower()), None)


def read_component_names(path: str | Path) -> dict[int, str]:
    """The ``components.yml`` map the Sim writes (``<component id>: <Component name>``) — the same flat format as ``boundaries.yml``."""
    return read_boundary_names(path)
