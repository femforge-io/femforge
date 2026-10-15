"""The in-process gmsh meshing behind [`GmshMesher`][femforge.meshing.backend.GmshMesher] and the mesh worker."""

from __future__ import annotations

import time
from pathlib import Path

import gmsh

from femforge.meshing.backend import MeshJob, MeshReport, mesh_inputs
from femforge.meshing.groups import MeshGroupsFile
from femforge.meshing.mesher import Mesher


__all__ = ["mesh_with_gmsh"]


def mesh_with_gmsh(job: MeshJob, out_dir: Path) -> MeshReport:  # pragma: no cover - drives a live gmsh model
    """Mesh ``job`` into ``out_dir`` in a fresh gmsh session of this process, finalized afterwards so no model outlives the job."""
    started: float = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    output: Path = out_dir / job.msh

    # gmsh.clear() keeps options, so a session other code left open would leak its settings into this mesh.
    if gmsh.isInitialized():
        gmsh.finalize()

    mesher = Mesher(mesh_inputs(job), output=output)
    try:
        mesher.generate()
    finally:
        if gmsh.isInitialized():
            gmsh.finalize()

    return MeshReport(
        msh=output.name,
        groups=MeshGroupsFile.capture(mesher.physical_groups, mesher.boundary_groups, mesher.element_count or 0),
        gmsh_version=gmsh.__version__,
        duration_s=time.monotonic() - started,
    )
