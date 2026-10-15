"""Parse Elmer outputs into a typed [`Result`][femforge.run.result.Result]: SaveScalars columns for the numbers, the solver log for status, residual and wall time."""

from __future__ import annotations

import re
from pathlib import Path

from femforge.common.files import BundleFile, BundleGlob
from femforge.common.frame import Axisymmetric, Frame, Solid
from femforge.common.log import log
from femforge.common.vector import Vec3
from femforge.elmer.variables import ElmerVariable
from femforge.physical.units import quantity
from femforge.run.magnetic import component_forces, component_torques, read_component_names, revolve
from femforge.run.manifest import RunManifest
from femforge.run.result import Result, RunStatus


# A signed integer / float / scientific-notation number, e.g. "12.5", "-3.4", "0.1483E+01".
_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"
# The trailing component index on a vector column name, e.g. "... force 3".
_COMPONENT_RE = re.compile(r"(.*?)(?:\s+([123]))\s*$")
# A parenthesized (NRM, RELC) pair on a ComputeChange line; we want the RELC (second) value.
_NRM_RELC_RE = re.compile(rf"\(\s*({_NUM})\s+({_NUM})\s*\)")
# "SOLVER TOTAL TIME(CPU,REAL):   4.55   5.12" → the REAL (second) time.
_TOTAL_TIME_RE = re.compile(rf"SOLVER TOTAL TIME\(CPU,REAL\):\s*({_NUM})\s+({_NUM})")


def result_vtu(sim_dir: str | Path) -> Path | None:
    """The run's newest ParaView output: the ``.pvtu`` master when the solve was partitioned, else the newest ``.vtu``; None when there is none.

    Use it instead of a ``*.vtu`` glob, which also matches a partitioned run's per-rank pieces.
    """
    sim_path: Path = Path(sim_dir)
    for pattern in (BundleGlob.PVTU, BundleGlob.VTU):
        matches: list[Path] = sorted(sim_path.glob(pattern))
        if matches:
            return matches[-1]

    return None


def _read_names(names_path: Path) -> list[str]:
    """Read an Elmer ``.dat.names`` sidecar into an ordered list of column names; keeps everything after the first colon of each ``"<idx>: <name>"`` line following the header."""
    names: list[str] = []
    collecting: bool = False
    for line in names_path.read_text().splitlines():
        if "Variables in columns of matrix" in line or "Data on different columns" in line:
            collecting = True
            continue

        if collecting:
            parts: list[str] = line.split(":")
            if len(parts) >= 2:
                names.append(":".join(parts[1:]).strip())
    return names


def _read_dat(dat_path: Path, names: list[str]) -> dict[str, float]:
    """
    Read the final data row of a ``.dat`` into ``{column_name: value}``.

    A sweep/transient appends rows; the *last* non-empty line is the converged state.
    """
    rows: list[str] = [ln for ln in dat_path.read_text().splitlines() if ln.strip()]
    if not rows:
        return {}

    values: list[float] = [float(tok) for tok in rows[-1].split()]
    return {name: value for name, value in zip(names, values)}


def _gather_vector(columns: dict[str, float], keyword: str) -> Vec3 | None:
    """
    Assemble a Vec3 from the component columns whose (stripped) name contains ``keyword`` and ends in a 1/2/3 index.

    Returns None when no such component columns are present (e.g. a thermal run exports no force), so the caller leaves the corresponding ``Result`` field None.
    """
    components: dict[int, float] = {}
    for name, value in columns.items():
        match: re.Match[str] | None = _COMPONENT_RE.match(name)
        if match and keyword in match.group(1).lower():
            components[int(match.group(2))] = value
    if not components:
        return None

    return Vec3(components.get(1, 0.0), components.get(2, 0.0), components.get(3, 0.0))


def _rank_dats(names_path: Path) -> list[Path]:
    """The per-rank ``<base>.dat.<rank>`` files an MPI SaveScalars writes beside the shared ``.names`` sidecar, rank-ordered."""
    base: Path = names_path.with_suffix("")
    siblings: list[Path] = list(base.parent.glob(base.name + ".*"))
    ranked: list[tuple[int, Path]] = [(int(path.suffix[1:]), path) for path in siblings if path.suffix[1:].isdigit()]
    return [path for _, path in sorted(ranked)]


def _parse_scalars(sim_dir: Path) -> dict[str, float]:
    """
    Read every ``.dat`` (+ ``.dat.names``) in the run dir into one ``{column: value}`` map. Empty when none were exported.

    An MPI solve writes per-rank ``.dat.<rank>`` files instead of the base ``.dat``; the exported ``res:`` values are
    already globally reduced (every rank writes the same row), so rank 0 stands in for the whole — never a sum.
    """
    columns: dict[str, float] = {}
    for names_path in sorted(sim_dir.glob(BundleGlob.DAT_NAMES)):
        names: list[str] = _read_names(names_path)
        dat_path: Path = names_path.with_suffix("")
        if dat_path.is_file():
            columns.update(_read_dat(dat_path, names))
            continue

        rank_paths: list[Path] = _rank_dats(names_path)
        if not rank_paths:
            continue

        rows: list[dict[str, float]] = [_read_dat(path, names) for path in rank_paths]
        if any(row != rows[0] for row in rows[1:]):
            log.bind(partitions=len(rank_paths), file=dat_path.name).warning(
                "Per-rank scalars disagree; using rank 0 — a SaveScalars operator without a global reduction is partition-local.",
            )
        columns.update(rows[0])
    return columns


def _parse_log(sim_dir: Path) -> tuple[RunStatus, float | None, float | None, str | None]:
    """
    Scan ``elmersolver.log`` for status, final residual, wall time, and Elmer version.

    Status: ``error`` if any ERROR line appears or the run never reached "ALL DONE"; ``diverged`` on a non-convergence marker; otherwise ``ok``.
    """
    log_path: Path = sim_dir / BundleFile.SOLVER_LOG
    if not log_path.is_file():
        return "error", None, None, None

    lines: list[str] = log_path.read_text().splitlines()

    has_error: bool = False
    diverged: bool = False
    done: bool = False
    residual: float | None = None
    wall_time: float | None = None
    elmer_version: str | None = None

    for line in lines:
        if "ERROR" in line:
            has_error = True

        if "FAILED to converge" in line or "NaN" in line or "Not converged" in line:
            diverged = True

        if "ALL DONE" in line:
            done = True

        if elmer_version is None and "elmer solver" in line.lower():
            version_match: re.Match[str] | None = re.search(
                r"elmer solver.*?\(v[a-z]*\s*([\w.\-]+)",
                line,
                re.IGNORECASE,
            )
            if version_match:
                elmer_version = version_match.group(1)

        if "RELC" in line:
            relc_match: re.Match[str] | None = _NRM_RELC_RE.search(line)
            if relc_match:
                residual = float(relc_match.group(2))

        time_match: re.Match[str] | None = _TOTAL_TIME_RE.search(line)
        if time_match:
            wall_time = float(time_match.group(2))

    status: RunStatus = "error" if (has_error or not done) else ("diverged" if diverged else "ok")
    return status, residual, wall_time, elmer_version


def parse_result(sim_dir: str | Path, frame: Frame | None = None) -> Result:
    """Parse a finished run directory into a typed [`Result`][femforge.run.result.Result], revolving force and torque to full-revolution totals under an ``Axisymmetric`` frame.

    ``frame=None`` reads the frame from the bundle's manifest, falling back to ``Solid`` for a directory without one.
    """
    sim_path: Path = Path(sim_dir)
    columns: dict[str, float] = _parse_scalars(sim_path)
    status, residual, wall_time, elmer_version = _parse_log(sim_path)

    # Narrowed to the global CalcFields sums so per-Component force/torque triples can never silently absorb into
    # Result.force/.torque (run.magnetic reads those from Result.raw).
    force_vec: Vec3 | None = _gather_vector(columns, "nodal force")
    torque_vec: Vec3 | None = _gather_vector(columns, "nodal torque")
    flux_density_max: float | None = next(
        (value for name, value in columns.items() if ElmerVariable.MAGNETIC_FLUX_DENSITY.lower() in name.lower()),
        None,
    )
    # The thermal and coupled joule-heating stacks export a "max ... temperature" SaveScalars reduction.
    temperature_max: float | None = next(
        (value for name, value in columns.items() if "max" in name.lower() and ElmerVariable.TEMPERATURE.lower() in name.lower()),
        None,
    )

    # Per-component force/torque triples join against the components.yml the Sim wrote; without it they stay in raw only.
    force_by_component: dict[str, Vec3] = {}
    torque_by_component: dict[str, Vec3] = {}
    components_file: Path = sim_path / BundleFile.COMPONENTS
    if components_file.is_file():
        names_by_component: dict[int, str] = read_component_names(components_file)
        force_by_component = component_forces(columns, names_by_component)
        torque_by_component = component_torques(columns, names_by_component)

    if frame is None:
        has_manifest: bool = (sim_path / BundleFile.MANIFEST).is_file()
        frame = RunManifest.load(sim_path).config.frame if has_manifest else Solid()

    if isinstance(frame, Axisymmetric):
        force_vec = revolve(force_vec) if force_vec is not None else None
        torque_vec = revolve(torque_vec) if torque_vec is not None else None
        force_by_component = {name: revolve(vec) for name, vec in force_by_component.items()}
        torque_by_component = {name: revolve(vec) for name, vec in torque_by_component.items()}

    return Result(
        status=status,
        frame=frame,
        force=quantity(force_vec, "newton") if force_vec is not None else None,
        torque=quantity(torque_vec, "newton * meter") if torque_vec is not None else None,
        flux_density_max=quantity(flux_density_max, "tesla") if flux_density_max is not None else None,
        temperature_max=quantity(temperature_max, "kelvin") if temperature_max is not None else None,
        component_forces={name: quantity(vec, "newton") for name, vec in force_by_component.items()},
        component_torques={name: quantity(vec, "newton * meter") for name, vec in torque_by_component.items()},
        residual=residual,
        wall_time=wall_time,
        elmer_version=elmer_version,
        raw=dict(columns),
    )
