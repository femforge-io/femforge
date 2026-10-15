"""The JSON schemas of femforge's persisted models, printed or written out by ``python -m femforge.schemas``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from femforge.artifacts.sidecar import ArtifactRecord, ArtifactRef
from femforge.config import RunMeta, SimulationConfig
from femforge.doctor import DoctorReport
from femforge.elmer.config import (
    CoilCurrentSolver,
    Constants,
    ElectrostaticsSolver,
    GenericSolver,
    HeatSolver,
    MagnetoDynamics2DSolver,
    MagnetoDynamicsCalcFieldsSolver,
    MagnetoDynamicsSolver,
    OperatingPoint,
    PhysicsConfig,
    PhysicsSelection,
    ResultOutputSolver,
    SaveScalarsSolver,
    SimulationSettings,
    StatCurrentSolver,
    StressSolver,
)
from femforge.materials.properties import MaterialProperties
from femforge.meshing.config import MeshConfig
from femforge.meshing.gmsh_options import GmshOptions
from femforge.meshing.spec import MeshSpec
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.run.manifest import RunManifest
from femforge.run.result import MeshStats, Result
from femforge.run.retention import CleanupRecord, RetentionPolicy
from femforge.telemetry.convergence import ConvergenceTargetsFile, LoopTarget
from femforge.viz.field_display import DisplaySpec


SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    # The composition root plus its per-concern area models. Each area constructs and validates on its
    # own, so each is a standalone deliverable here, not only a nested $def inside SimulationConfig.
    "SimulationConfig": SimulationConfig,
    "RunMeta": RunMeta,
    "MeshConfig": MeshConfig,
    "GmshOptions": GmshOptions,
    "DomainConfig": DomainConfig,
    "PhysicsSelection": PhysicsSelection,
    # The Elmer solver config tree: the typed stack `physics_config()` builds, which populates
    # `Sim.config`. PhysicsConfig is the container; the concrete solvers and the simulation/constants/
    # operating-point blocks it holds are each independently editable before `Sim.generate()`.
    "PhysicsConfig": PhysicsConfig,
    "SimulationSettings": SimulationSettings,
    "Constants": Constants,
    "OperatingPoint": OperatingPoint,
    "MagnetoDynamicsSolver": MagnetoDynamicsSolver,
    "MagnetoDynamics2DSolver": MagnetoDynamics2DSolver,
    "MagnetoDynamicsCalcFieldsSolver": MagnetoDynamicsCalcFieldsSolver,
    "ResultOutputSolver": ResultOutputSolver,
    "HeatSolver": HeatSolver,
    "ElectrostaticsSolver": ElectrostaticsSolver,
    "StatCurrentSolver": StatCurrentSolver,
    "CoilCurrentSolver": CoilCurrentSolver,
    "SaveScalarsSolver": SaveScalarsSolver,
    "StressSolver": StressSolver,
    "GenericSolver": GenericSolver,
    # Domain leaf models (composed by DomainConfig).
    "MaterialProperties": MaterialProperties,
    "EntityTag": EntityTag,
    # Run-lifecycle deliverables: the provenance manifest and the typed return path.
    "RunManifest": RunManifest,
    "Result": Result,
    "MeshStats": MeshStats,
    # Retention: the policy a driver or purge applies and the record it leaves on the manifest.
    "RetentionPolicy": RetentionPolicy,
    "CleanupRecord": CleanupRecord,
    # Artifacts: the store sidecar, the reference a manifest keeps, and the mesh artifact's spec.
    "ArtifactRecord": ArtifactRecord,
    "ArtifactRef": ArtifactRef,
    "MeshSpec": MeshSpec,
    # Telemetry: the sif-authored convergence targets a run writes beside its convergence log.
    "ConvergenceTargetsFile": ConvergenceTargetsFile,
    "LoopTarget": LoopTarget,
    # Viz: the persisted state of one field display (the workbench saves and restores it per run).
    "DisplaySpec": DisplaySpec,
    # Doctor: the report `femforge doctor --json` prints.
    "DoctorReport": DoctorReport,
}
"""Every model with a published JSON schema, keyed by its schema file name."""


def model_schema(name: str) -> dict[str, object]:
    """JSON schema for one registered model, by name."""
    try:
        return SCHEMA_MODELS[name].model_json_schema()
    except KeyError:
        raise KeyError(f"unknown schema {name!r}; available: {list(SCHEMA_MODELS)}") from None


def all_schemas() -> dict[str, dict[str, object]]:
    """JSON schema for every registered model, keyed by name."""
    return {name: model.model_json_schema() for name, model in SCHEMA_MODELS.items()}


def dump_schemas(out_dir: str | Path) -> list[Path]:
    """Write one ``<Name>.json`` per registered model into ``out_dir``."""
    out: Path = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in all_schemas().items():
        path: Path = out / f"{name}.json"
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            _ = fh.write(json.dumps(schema, indent=2) + "\n")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    """Print every schema or one model's, or write them all into ``--out``; returns the exit code."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Export JSON schemas for the config/material models.")
    parser.add_argument("model", nargs="?", help="A single model name to print (default: all).")
    parser.add_argument(
        "--out",
        metavar="DIR",
        help="Write <Name>.json for every model into DIR instead of printing.",
    )
    args: argparse.Namespace = parser.parse_args(argv)
    # argparse.Namespace attributes are typed Any; pin them to the known shapes.
    out_dir = cast("str | None", args.out)
    model_name = cast("str | None", args.model)

    if out_dir:
        paths: list[Path] = dump_schemas(out_dir)
        for p in paths:
            print(f"wrote {p}")
        return 0

    if model_name:
        print(json.dumps(model_schema(model_name), indent=2))
    else:
        print(json.dumps(all_schemas(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
