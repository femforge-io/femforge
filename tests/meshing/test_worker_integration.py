"""The real mesh worker (gmsh and build123d gated): it meshes a STEP exactly as in process, probes gmsh, and reports a job it cannot mesh."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("gmsh")
pytest.importorskip("build123d")

import build123d as b3d  # imported after the gate so the file still collects where the CAD stack is absent
import gmsh

import femforge
from femforge.common.tags import TagKey
from femforge.config import SimulationConfig
from femforge.geometry import Build123dGeometry
from femforge.geometry.tags import TaggedCompound, TaggedPart
from femforge.materials import N42, Air
from femforge.materials.properties import MaterialProperties
from femforge.meshing.backend import GmshMesher, MeshError, MeshJob, WorkerMesher, WorkerProbe
from femforge.meshing.config import MeshConfig
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.run.kinds import mesh_spec


SRC = Path(femforge.__file__).resolve().parents[1]
"""The source tree under test, which the worker child must import too."""


class _MagnetInAir(Build123dGeometry):
    """A 10 mm magnet at the centre of a 40 mm air box."""

    def model(self) -> TaggedCompound:
        air = TaggedPart(b3d.Box(40, 40, 40), tags={TagKey.MATERIAL: Air().tag})
        magnet = TaggedPart(b3d.Box(10, 10, 10), tags={TagKey.MATERIAL: N42().tag, TagKey.MAGNETIZATION: "N"})

        return TaggedCompound(children=[air, magnet])


@pytest.fixture
def worker_imports_this_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = [str(SRC), os.environ.get("PYTHONPATH", "")]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(path for path in paths if path))


def _job(step: Path, materials: list[MaterialProperties]) -> MeshJob:
    config = SimulationConfig(
        mesh=MeshConfig(step=str(step), global_mesh_size=0.004, deterministic=True),
        domain=DomainConfig(materials=materials, tags=[EntityTag(selector={TagKey.MAGNETIZATION: "N"})]),
    )

    return MeshJob(spec=mesh_spec(config, "0" * 64), step=str(step))


@pytest.mark.usefixtures("worker_imports_this_tree")
def test_worker_and_in_process_meshes_agree(tmp_path: Path) -> None:
    step = _MagnetInAir().build_step(tmp_path / "geometry.step")
    job = _job(step, [Air(), N42()])

    in_process = GmshMesher().mesh(job, tmp_path / "in-process")
    worker = WorkerMesher(timeout_s=300.0).mesh(job, tmp_path / "worker")

    assert worker.groups == in_process.groups
    assert worker.groups.element_count > 0
    assert [body.name for body in worker.groups.bodies] == ["AIR", "N42_MAGNETIZATIONN"]
    assert worker.gmsh_version == in_process.gmsh_version == gmsh.__version__
    assert (tmp_path / "worker" / worker.msh).is_file() and (tmp_path / "in-process" / in_process.msh).is_file()
    assert not gmsh.isInitialized()


@pytest.mark.usefixtures("worker_imports_this_tree")
def test_a_job_the_mesher_rejects_fails_with_its_message(tmp_path: Path) -> None:
    step = _MagnetInAir().build_step(tmp_path / "geometry.step")

    with pytest.raises(MeshError, match="MeshError: All entities must have an associated material"):
        _ = WorkerMesher(timeout_s=300.0).mesh(_job(step, [Air()]), tmp_path / "worker")


@pytest.mark.usefixtures("worker_imports_this_tree")
def test_the_worker_probe_reports_the_gmsh_version() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "femforge.meshing.worker", "--probe"],
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )

    assert WorkerProbe.model_validate_json(done.stdout) == WorkerProbe(gmsh=gmsh.__version__)
