"""The mesh seam: what a meshing job carries and reports, and the backends that mesh it in this process or in a worker."""

from __future__ import annotations

import functools
import os
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from femforge.common.files import BundleFile
from femforge.common.frame import Axisymmetric, Frame, Solid
from femforge.materials.properties import MaterialProperties
from femforge.meshing.config import MeshConfig
from femforge.meshing.groups import MeshGroupsFile
from femforge.meshing.spec import MeshSpec
from femforge.physical.domain import DomainConfig, EntityTag


if TYPE_CHECKING:
    from pydantic import TypeAdapter


__all__ = [
    "GmshMesher",
    "MeshBackend",
    "MeshDone",
    "MeshError",
    "MeshFailed",
    "MeshInputs",
    "MeshJob",
    "MeshReport",
    "MeshStatus",
    "WorkerMesher",
    "WorkerProbe",
    "mesh_inputs",
    "mesh_outcomes",
]

WORKER_MODULE = "femforge.meshing.worker"
"""The module the mesh worker runs as."""

WORKER_ROLE = "mesh"
"""The ``worker`` field every relayed worker record carries."""

WORKER_JOB = "job.json"
"""The job file a [`WorkerMesher`][femforge.meshing.backend.WorkerMesher] hands its worker."""

WORKER_OUTCOME = "outcome.json"
"""The outcome file a worker writes back."""

WORKER_LOG = "worker.log"
"""The worker's merged output, kept until the job ends."""


class MeshError(RuntimeError):
    """Meshing failed: the STEP's bodies could not be grouped or meshed, or the mesh worker crashed or ran out of time."""


class MeshJob(BaseModel):
    """One meshing request as plain data: the mesh spec, the STEP to read, and the ``.msh`` to write."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    spec: MeshSpec
    """The mesh artifact's spec, which decides the mesh."""

    step: str
    """The solve-frame STEP to mesh."""

    msh: str = BundleFile.GEOMETRY_MSH
    """The ``.msh`` file name to write in the output directory."""


class MeshReport(BaseModel):
    """What one meshing job left: the ``.msh``, its group records, and the gmsh release that wrote it."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    msh: str
    """The ``.msh`` file name in the output directory."""

    groups: MeshGroupsFile
    """The body and boundary group records and the element count."""

    gmsh_version: str
    """The gmsh release that meshed the job."""

    duration_s: float
    """`t` `[s]`: Wall time the meshing took."""


class MeshBackend(Protocol):
    """Meshes a job into a gmsh ``.msh`` plus its group records; each backend decides where gmsh runs."""

    def mesh(self, job: MeshJob, out_dir: Path) -> MeshReport:
        """Mesh ``job`` into ``out_dir``, raising [`MeshError`][femforge.meshing.backend.MeshError] when meshing fails."""
        ...


@dataclass
class MeshInputs:
    """The config surface a mesher reads, rebuilt from a [`MeshJob`][femforge.meshing.backend.MeshJob] alone."""

    frame: Frame
    """A frame of the job's body dimension."""

    mesh: MeshConfig
    """The job's STEP and mesh settings."""

    domain: DomainConfig
    """Bare materials named by their tags, and the tag selectors with their element sizes."""


def mesh_inputs(job: MeshJob) -> MeshInputs:
    """The mesher's inputs for ``job``: the frame of its dimension, its mesh settings, and a domain of bare materials and tag selectors."""
    spec: MeshSpec = job.spec
    frame: Frame = Solid() if spec.model_dim == 3 else Axisymmetric()
    mesh = MeshConfig(
        step=job.step,
        global_mesh_size=spec.global_mesh_size,
        gmsh_options=spec.gmsh_options,
        deterministic=spec.deterministic,
    )
    materials: list[MaterialProperties] = [MaterialProperties(name=tag, tag=tag) for tag in spec.materials]
    tags: list[EntityTag] = [EntityTag(selector=dict(tag.selector), mesh_size=tag.mesh_size) for tag in spec.tags]

    return MeshInputs(frame=frame, mesh=mesh, domain=DomainConfig(materials=materials, tags=tags))


class GmshMesher(MeshBackend):
    """Meshes in this process with gmsh, which loads on the first job and is finalized after every one."""

    def mesh(self, job: MeshJob, out_dir: Path) -> MeshReport:
        """Mesh ``job`` into ``out_dir`` with gmsh in this process."""
        from femforge.meshing.gmsh_backend import mesh_with_gmsh

        return mesh_with_gmsh(job, out_dir)


class MeshStatus(StrEnum):
    """How a worker job ended."""

    DONE = "done"
    """The job meshed; the outcome carries its report."""

    FAILED = "failed"
    """Meshing raised; the outcome carries the error."""


class MeshDone(BaseModel):
    """A worker job that meshed."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    status: Literal[MeshStatus.DONE] = MeshStatus.DONE
    """Tells the outcome kinds apart."""

    report: MeshReport
    """What the job left."""


class MeshFailed(BaseModel):
    """A worker job whose meshing raised."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    status: Literal[MeshStatus.FAILED] = MeshStatus.FAILED
    """Tells the outcome kinds apart."""

    error: str
    """The exception's type name."""

    message: str
    """The exception's message."""


MeshOutcome = Annotated[MeshDone | MeshFailed, Field(discriminator="status")]
"""The outcome file of a worker job, told apart by its ``status``."""


@functools.cache
def mesh_outcomes() -> TypeAdapter[MeshDone | MeshFailed]:
    """The validator of a worker's outcome file, built on first use."""
    from pydantic import TypeAdapter

    return TypeAdapter(MeshOutcome)


class WorkerProbe(BaseModel):
    """What ``python -m femforge.meshing.worker --probe`` prints: the gmsh release the worker meshes with."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    gmsh: str
    """The gmsh version."""


class WorkerMesher(MeshBackend):
    """Meshes in a child Python process, so gmsh, its license and its crashes stay out of this one."""

    def __init__(self, *, python: str = sys.executable, module: str = WORKER_MODULE, timeout_s: float | None = None) -> None:
        """Mesh each job with ``python -m module``, stopped after ``timeout_s`` seconds when given.

        Args:
            python: The interpreter that runs the worker.
            module: The worker module.
            timeout_s: Seconds before a job is stopped; None waits for it.
        """
        self.python: str = python
        self.module: str = module
        self.timeout_s: float | None = timeout_s

    def mesh(self, job: MeshJob, out_dir: Path) -> MeshReport:
        """Mesh ``job`` into ``out_dir`` in a worker process, raising [`MeshError`][femforge.meshing.backend.MeshError] when the worker fails or leaves no outcome."""
        from femforge.common.log import TELEMETRY_ENV, relay_record
        from femforge.execution.streaming import CommandResult, log_tail, run_streaming

        out_dir.mkdir(parents=True, exist_ok=True)
        # The worker meshes into the directory of an absolute msh, so the job file carries everything it needs.
        placed: MeshJob = job.model_copy(update={"step": str(Path(job.step).resolve()), "msh": str((out_dir / job.msh).resolve())})
        env: dict[str, str] = {name: value for name, value in os.environ.items() if name != TELEMETRY_ENV}
        env["PYTHONUTF8"] = "1"

        with tempfile.TemporaryDirectory(prefix="femforge-mesh-") as scratch:
            folder = Path(scratch)
            job_file: Path = folder / WORKER_JOB
            outcome_file: Path = folder / WORKER_OUTCOME
            _ = job_file.write_text(placed.model_dump_json(), encoding="utf-8")

            result: CommandResult = run_streaming(
                [self.python, "-m", self.module, str(job_file), str(outcome_file)],
                cwd=folder,
                log_path=folder / WORKER_LOG,
                on_line=functools.partial(relay_record, worker=WORKER_ROLE),
                timeout_s=self.timeout_s,
                env=env,
            )
            if not outcome_file.is_file():
                stopped: str = f"ran past its {self.timeout_s} s" if result.timed_out else f"exited with code {result.returncode}"
                raise MeshError(f"The mesh worker {stopped} without an outcome.\n{log_tail(result.log_path)}")

            outcome: MeshDone | MeshFailed = mesh_outcomes().validate_json(outcome_file.read_text(encoding="utf-8"))

        if isinstance(outcome, MeshFailed):
            raise MeshError(f"{outcome.error}: {outcome.message}")

        return outcome.report
