"""Docker end to end (FEMFORGE_DOCKER_TESTS=1): without native Elmer, a partitioned solve converts, partitions and solves in containers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import build123d as b3d
import pytest

from femforge.common.tags import TagKey
from femforge.common.vector import Direction
from femforge.config import SimulationConfig
from femforge.elmer.config import PhysicsSelection
from femforge.elmer.physics import Physics
from femforge.elmer.runtime import native
from femforge.elmer.runtime.tools import ElmerToolsLocations
from femforge.geometry import Build123dGeometry
from femforge.geometry.tags import TaggedCompound, TaggedPart
from femforge.materials import N42, Air
from femforge.meshing.config import MeshConfig
from femforge.physical.conditions import Magnetization
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.run.driver import Driver
from femforge.physical.units import U
from femforge.run.retention import RetentionPolicy

pytestmark = pytest.mark.skipif(os.environ.get("FEMFORGE_DOCKER_TESTS") != "1", reason="set FEMFORGE_DOCKER_TESTS=1 to run the Docker end-to-end tests")


class _MagnetInAir(Build123dGeometry):
    """A 10 mm magnet at the centre of a 40 mm air box."""

    def model(self) -> TaggedCompound:
        air = TaggedPart(b3d.Box(40, 40, 40), tags={TagKey.MATERIAL: Air().tag})
        magnet = TaggedPart(b3d.Box(10, 10, 10), tags={TagKey.MATERIAL: N42().tag, TagKey.MAGNETIZATION: "N"})

        return TaggedCompound(children=[air, magnet])


def test_a_docker_driver_converts_partitions_and_solves_without_native_elmer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_native_elmer() -> ElmerToolsLocations:
        raise AssertionError("the Docker backend looked for a native Elmer install")

    monkeypatch.setattr(native, "locate_elmer_tools", no_native_elmer)
    monkeypatch.chdir(tmp_path)
    magnet = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 0, 1))])
    config = SimulationConfig(
        geometry=_MagnetInAir(),
        mesh=MeshConfig(global_mesh_size=4 * U.mm),
        domain=DomainConfig(materials=[Air(), N42()], tags=[magnet]),
        physics=PhysicsSelection(selected=Physics.MAGNETOSTATICS),
    )
    driver = Driver(
        runs_dir="runs",
        code_sha="docker-e2e",
        docker=True,
        mpi_processes=2,
        retention=RetentionPolicy.keep_all(),
    )

    result = driver.evaluate(config)

    bundle: Path = tmp_path / "runs" / str(result.run_id)
    assert result.status == "ok", (bundle / "elmersolver.log").read_text(encoding="utf-8")[-3000:]
    assert (bundle / "mesh.header").is_file()
    assert (bundle / "partitioning.2").is_dir()
    assert (bundle / "elmergrid.log").is_file() and (bundle / "elmergrid_partition.log").is_file()

    if sys.platform == "linux":
        foreign: list[Path] = [path for path in bundle.rglob("*") if path.stat().st_uid != os.getuid()]
        assert foreign == []
