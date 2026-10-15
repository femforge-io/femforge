"""The mesh worker: ``python -m femforge.meshing.worker JOB OUTCOME`` meshes one job with gmsh and writes how it ended."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from enum import IntEnum
from pathlib import Path
from typing import cast

from femforge.common.log import log_to_stdout_json
from femforge.meshing.backend import WORKER_MODULE, MeshDone, MeshFailed, MeshJob, MeshReport, WorkerProbe
from femforge.meshing.gmsh_backend import mesh_with_gmsh


__all__ = ["WorkerExit", "main"]


class WorkerExit(IntEnum):
    """The worker's exit codes."""

    OK = 0
    """The job meshed, or the probe answered."""

    FAILED = 1
    """Meshing raised; the outcome file says how."""

    USAGE = 2
    """The command line was wrong, as for argparse's own errors."""


def main(argv: Sequence[str] | None = None) -> int:
    """Mesh the job file ``JOB`` into the directory of its absolute ``msh`` and write the outcome file ``OUTCOME``, or print the gmsh version."""
    parser = argparse.ArgumentParser(prog=f"python -m {WORKER_MODULE}", description="Mesh one femforge job with gmsh.")
    _ = parser.add_argument("job", nargs="?", type=Path, help="The MeshJob JSON file; its msh is the absolute .msh path to write.")
    _ = parser.add_argument("outcome", nargs="?", type=Path, help="Where the outcome JSON goes.")
    _ = parser.add_argument("--probe", action="store_true", help="Print the gmsh version as JSON and exit.")
    args = parser.parse_args(argv)

    if cast("bool", args.probe):
        import gmsh

        print(WorkerProbe(gmsh=gmsh.__version__).model_dump_json())
        return WorkerExit.OK

    job_file = cast("Path | None", args.job)
    outcome_file = cast("Path | None", args.outcome)
    if job_file is None or outcome_file is None:
        parser.print_usage(sys.stderr)
        print(f"{parser.prog}: error: JOB and OUTCOME are required without --probe", file=sys.stderr)
        return WorkerExit.USAGE

    log_to_stdout_json()
    job: MeshJob = MeshJob.model_validate_json(job_file.read_text(encoding="utf-8"))
    target = Path(job.msh)

    try:
        report: MeshReport = mesh_with_gmsh(job.model_copy(update={"msh": target.name}), target.parent)
    except Exception as error:  # noqa: BLE001 - every failure becomes an outcome the parent reads
        _ = outcome_file.write_text(MeshFailed(error=type(error).__name__, message=str(error)).model_dump_json(), encoding="utf-8")
        return WorkerExit.FAILED

    _ = outcome_file.write_text(MeshDone(report=report).model_dump_json(), encoding="utf-8")
    return WorkerExit.OK


if __name__ == "__main__":
    sys.exit(main())
