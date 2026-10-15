"""Per-phase electrical quantities of a current-conduction run, from the electrode flux columns in ``Result.raw`` and ``boundaries.yml``."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping


# A SaveScalars flux column: "diffusive flux: potential over bc 3" (wording tolerated loosely, index required).
_FLUX_BC_RE = re.compile(r"diffusive flux.*over bc\s*(\d+)", re.IGNORECASE)


def boundary_fluxes(raw: Mapping[str, float]) -> dict[int, float]:
    """Elmer bc index -> integrated conductive flux (A) from the SaveScalars columns in ``Result.raw``."""
    fluxes: dict[int, float] = {}
    for name, value in raw.items():
        match: re.Match[str] | None = _FLUX_BC_RE.search(name)
        if match:
            fluxes[int(match.group(1))] = value
    return fluxes


def read_boundary_names(path: str | Path) -> dict[int, str]:
    """The ``boundaries.yml`` map the Sim writes (``<bc index>: <boundary block name>``), as a dict."""
    names: dict[int, str] = {}
    for line in Path(path).read_text().splitlines():
        key, _, value = line.partition(":")
        if key.strip().isdigit() and value.strip():
            names[int(key.strip())] = value.strip()
    return names


def electrode_currents(raw: Mapping[str, float], names_by_bc: Mapping[int, str]) -> dict[str, float]:
    """Boundary block name -> terminal current (A), joining the flux columns against the bc-index name map."""
    return {names_by_bc[bc]: flux for bc, flux in boundary_fluxes(raw).items() if bc in names_by_bc}


# The phase index inside an electrode boundary name (the coil's PHASE0/1/2 tag names survive into the group names).
_PHASE_RE = re.compile(r"PHASE(\d+)", re.IGNORECASE)


def phase_currents(currents: Mapping[str, float]) -> dict[str, float]:
    """Phase index ("0", "1", ...) -> terminal current (A): the largest-magnitude current among each phase's named electrode currents."""
    best: dict[str, float] = {}
    for name, current in currents.items():
        match: re.Match[str] | None = _PHASE_RE.search(name)
        if match is None:
            continue
        phase: str = match.group(1)
        # Elmer integrates an internal electrode's flux from one side, and the pad side can read about zero.
        if phase not in best or abs(current) > abs(best[phase]):
            best[phase] = current
    return best


def resistance(delta_volts: float, current_amps: float) -> float:
    """R = ΔV / I for one phase; raises on a (numerically) zero current."""
    if abs(current_amps) < 1e-30:
        raise ValueError(f"electrode current is zero (got {current_amps!r}); the flux integral found no current through the electrode.")
    return abs(delta_volts / current_amps)
