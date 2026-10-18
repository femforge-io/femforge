"""The mesh seam's plain data, the mesher inputs a job rebuilds, the in-process backend's hand-off, and the worker's outcome files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest

import femforge.meshing
from femforge.frames import Axisymmetric, Solid
from femforge.common.tags import TagKey
from femforge.common.vector import Direction
from femforge.config import SimulationConfig
from femforge.materials import N42, Air
from femforge.meshing.backend import (
    GmshMesher,
    MeshDone,
    MeshFailed,
    MeshJob,
    MeshReport,
    MeshStatus,
    mesh_inputs,
    mesh_outcomes,
)
from femforge.meshing.config import MeshConfig
from femforge.meshing.gmsh_options import GmshOptions
from femforge.meshing.groups import MeshGroupsFile
from femforge.meshing.naming import compose_group_name
from femforge.meshing.spec import MeshSpec, TagSelectorSpec
from femforge.physical.conditions import Magnetization
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import U
from femforge.run.kinds import mesh_spec


def _spec(model_dim: Literal[2, 3] = 3) -> MeshSpec:
    return MeshSpec(
        step_key="0" * 64,
        model_dim=model_dim,
        global_mesh_size=0.002,
        gmsh_options=GmshOptions(num_threads=2),
        deterministic=True,
        tags=[TagSelectorSpec(selector={"Magnetization": "N"}, mesh_size=0.0005)],
        materials=["Air", "N42"],
    )


def _report() -> MeshReport:
    return MeshReport(msh="geometry.msh", groups=MeshGroupsFile(element_count=7), gmsh_version="4.15.0", duration_s=0.5)


def test_a_job_writes_geometry_msh_unless_told_otherwise_and_round_trips() -> None:
    job = MeshJob(spec=_spec(), frame=Solid(), step="runs/a/geometry.step")

    restored = MeshJob.model_validate_json(job.model_dump_json())

    assert job.msh == "geometry.msh"
    assert restored == job


def test_mesh_inputs_rebuild_the_frame_the_mesh_settings_and_a_bare_domain() -> None:
    inputs = mesh_inputs(MeshJob(spec=_spec(), frame=Solid(), step="model.step"))

    assert isinstance(inputs.frame, Solid)
    assert inputs.mesh.step == "model.step"
    assert inputs.mesh.global_mesh_size == 2 * U.mm
    assert inputs.mesh.resolved_gmsh_options().num_threads == 1
    assert [(material.name, material.tag) for material in inputs.domain.materials] == [("Air", "Air"), ("N42", "N42")]
    assert [(dict(tag.selector), tag.mesh_size, tag.conditions) for tag in inputs.domain.tags] == [({"Magnetization": "N"}, 0.5 * U.mm, [])]
    assert isinstance(mesh_inputs(MeshJob(spec=_spec(model_dim=2), frame=Axisymmetric(), step="model.step")).frame, Axisymmetric)


def test_mesh_inputs_name_each_group_as_the_full_config_would() -> None:
    magnet = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 0, 1))])
    config = SimulationConfig(mesh=MeshConfig(step="model.step"), domain=DomainConfig(materials=[N42(), Air()], tags=[magnet]))
    inputs = mesh_inputs(MeshJob(spec=mesh_spec(config, "0" * 64), frame=config.frame, step="model.step"))
    warnings: list[str] = []

    rebuilt = {material.tag: compose_group_name(material, inputs.domain.tags, warnings.append) for material in inputs.domain.materials}
    authored = {material.tag: compose_group_name(material, config.domain.tags, warnings.append) for material in config.domain.materials}

    assert rebuilt == authored == {"Air": "AIR_MAGNETIZATIONN", "N42": "N42_MAGNETIZATIONN"}


def test_gmsh_mesher_hands_the_job_to_the_in_process_mesher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from femforge.meshing import gmsh_backend

    calls: list[tuple[MeshJob, Path]] = []

    def mesh_with_gmsh(job: MeshJob, out_dir: Path) -> MeshReport:
        calls.append((job, out_dir))

        return _report()

    monkeypatch.setattr(gmsh_backend, "mesh_with_gmsh", mesh_with_gmsh)
    job = MeshJob(spec=_spec(), frame=Solid(), step="model.step")

    report = GmshMesher().mesh(job, tmp_path)

    assert report == _report()
    assert calls == [(job, tmp_path)]


def test_worker_outcomes_parse_by_their_status() -> None:
    done = MeshDone(report=_report())
    failed = MeshFailed(error="MeshError", message="no material")

    assert mesh_outcomes().validate_json(done.model_dump_json()) == done
    assert mesh_outcomes().validate_json(failed.model_dump_json()) == failed
    assert json.loads(failed.model_dump_json())["status"] == MeshStatus.FAILED


def test_every_name_the_meshing_package_exports_resolves() -> None:
    missing = [name for name in femforge.meshing.__all__ if not hasattr(femforge.meshing, name)]

    assert missing == []
