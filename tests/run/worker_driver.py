"""Solves a magnet in air through a WorkerMesher with gmsh blocked and a stand-in Elmer, for the gmsh firewall test to run in a fresh process."""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import build123d as b3d

from femforge.artifacts.cache import ArtifactCache
from femforge.artifacts.local import LocalArtifactStore
from femforge.artifacts.sidecar import ArtifactRecord
from femforge.common.tags import TagKey
from femforge.common.vector import Direction
from femforge.config import SimulationConfig
from femforge.elmer.config import PhysicsSelection
from femforge.elmer.physics import Physics
from femforge.elmer.runtime.backend import ExecutionBackend
from femforge.execution.streaming import CancelToken, CommandResult, LineSink
from femforge.geometry import Build123dGeometry
from femforge.geometry.tags import TaggedCompound, TaggedPart
from femforge.materials import N42, Air
from femforge.meshing import WorkerMesher, block_gmsh
from femforge.meshing.config import MeshConfig
from femforge.physical.conditions import Magnetization
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.run.driver import Driver
from femforge.run.kinds import ELMER_MESH, mesh_spec
from femforge.run.result import Result

PHYSICAL_NAME = re.compile(r'^(\d+) \d+ "([^"]+)"$', re.MULTILINE)
"""One ``dim tag "name"`` line of a gmsh mesh's ``$PhysicalNames`` section."""


class MagnetInAir(Build123dGeometry):
    """A 10 mm magnet at the centre of a 40 mm air box."""

    def model(self) -> TaggedCompound:
        air = TaggedPart(b3d.Box(40, 40, 40), tags={TagKey.MATERIAL: Air().tag})
        magnet = TaggedPart(b3d.Box(10, 10, 10), tags={TagKey.MATERIAL: N42().tag, TagKey.MAGNETIZATION: "N"})

        return TaggedCompound(children=[air, magnet])


class FakeElmer(ExecutionBackend):
    """Names the meshed groups the way ElmerGrid numbers them and solves by replaying a finished run, so no Elmer is needed."""

    def __init__(self, fixtures: Path) -> None:
        self.fixtures: Path = fixtures

    @property
    def partitioned(self) -> bool:
        return False

    def grid_convert(
        self,
        mesh_dir: Path,
        msh_name: str,
        *,
        out_dir: Path,
        scale: float | None = None,
        autoclean: bool = False,
        keep_mesh_dir: bool = False,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        found: list[tuple[str, str]] = PHYSICAL_NAME.findall((mesh_dir / msh_name).read_text(encoding="utf-8"))
        named: list[tuple[int, str]] = [(int(dim), name) for dim, name in found]
        body_dim: int = max(dim for dim, _ in named)
        bodies: list[str] = [name for dim, name in named if dim == body_dim]
        boundaries: list[str] = [name for dim, name in named if dim < body_dim]
        lines: list[str] = [
            "! ----- names for bodies -----",
            *(f"$ {name} = {index}" for index, name in enumerate(bodies, start=1)),
            "! ----- names for boundaries -----",
            *(f"$ {name} = {index}" for index, name in enumerate(boundaries, start=len(bodies) + 1)),
        ]
        _ = (out_dir / "mesh.names").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for name in ("mesh.header", "mesh.nodes", "mesh.elements", "mesh.boundary"):
            _ = (out_dir / name).write_text("stub", encoding="utf-8")

        log_path: Path = out_dir / "elmergrid.log"
        _ = log_path.write_text("converted", encoding="utf-8")

        return CommandResult(returncode=0, duration_s=0.0, log_path=log_path, timed_out=False)

    def grid_partition(
        self,
        mesh_dir: Path,
        n_parts: int,
        *,
        halobc: bool,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        raise AssertionError("a serial solve never partitions")

    def solve(
        self,
        sim_dir: Path,
        *,
        n_procs: int = 1,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        for name in ("case.dat", "case.dat.names", "elmersolver.log"):
            shutil.copyfile(self.fixtures / name, sim_dir / name)

        log_path: Path = sim_dir / "elmersolver.log"
        if on_line is not None:
            for line in log_path.read_text(encoding="utf-8").splitlines(keepends=True):
                on_line(line)

        return CommandResult(returncode=0, duration_s=0.0, log_path=log_path, timed_out=False)


def _config(direction: Direction) -> SimulationConfig:
    magnet = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=direction)])

    return SimulationConfig(
        geometry=MagnetInAir(),
        mesh=MeshConfig(global_mesh_size=0.004),
        domain=DomainConfig(materials=[Air(), N42()], tags=[magnet]),
        physics=PhysicsSelection(selected=Physics.MAGNETOSTATICS),
    )


def main() -> None:
    """Solve uncached, then produce and reuse a cached mesh, all with gmsh blocked, and print what happened as JSON."""
    runs, store, fixtures = (Path(arg) for arg in sys.argv[1:4])
    block_gmsh()
    up, down = _config(Direction(0, 0, 1)), _config(Direction(0, 0, -1))

    uncached: Result = Driver(runs_dir=runs / "uncached", code_sha="firewall", backend=FakeElmer(fixtures), mesher=WorkerMesher()).evaluate(up)
    cache = ArtifactCache(LocalArtifactStore(store))
    driver = Driver(
        runs_dir=runs / "cached",
        code_sha="firewall",
        backend=FakeElmer(fixtures),
        mesher=WorkerMesher(),
        artifacts=cache,
    )
    produced: Result = driver.evaluate(up)
    reused: Result = driver.evaluate(down)
    record: ArtifactRecord | None = cache.lookup(ELMER_MESH, mesh_spec(up))

    outcome: dict[str, object] = {
        "statuses": [uncached.status, produced.status, reused.status],
        "msh": (runs / "uncached" / str(uncached.run_id) / "geometry.msh").is_file(),
        "gmsh_version": record.tools.gmsh if record is not None else None,
        "gmsh_loaded": "gmsh" in sys.modules,
    }
    print(json.dumps(outcome))


if __name__ == "__main__":
    main()
