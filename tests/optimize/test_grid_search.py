"""GridSearch walks the grid through a (fake or real-with-fake-solver) driver and records one StudyRecord per point."""

import shutil
from pathlib import Path

import pytest

from femforge.frames import Solid
from femforge.common.vector import Vec3
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.optimize.objective import NetForceMagnitude
from femforge.optimize.parameterization import GlobalMeshSize, Parameterization
from femforge.optimize.search import GridSearch
from femforge.physical.units import U, quantity
from femforge.run.bundle import load_manifest
from femforge.run.driver import Driver
from femforge.run.manifest import cache_key
from femforge.common.names import bundle_name
from femforge.run.result import MeshStats, Result, RunStatus

RUN_FIXTURES: Path = Path(__file__).parents[1] / "run" / "fixtures"


class _FakeDriver:
    """Stands in for run.Driver: canned force that rises as the mesh gets finer, so best() has a known winner."""

    def __init__(self) -> None:
        self.code_sha: str | None = "sha-test"
        self.seen: list[float] = []

    def evaluate(self, config: SimulationConfig) -> Result:
        size: float = config.mesh.global_mesh_size.m_as("m")
        self.seen.append(size)
        force_x: float = 1.0 / size  # finer mesh (smaller size) -> larger "force"
        return Result(frame=Solid(), status=RunStatus.OK, force=quantity(Vec3(force_x, 0.0, 0.0), U.N))


def _parameterization() -> Parameterization:
    base = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step", global_mesh_size=1.0 * U.m))
    return Parameterization(base=base, knobs=[GlobalMeshSize(name="global", low=0.2 * U.m, high=1.0 * U.m)])


def test_grid_search_reads_points_in_the_knob_unit_and_records_it() -> None:
    base = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"))
    param = Parameterization(base=base, knobs=[GlobalMeshSize(name="global", low=200 * U.mm, high=1000 * U.mm)])
    driver = _FakeDriver()

    study = GridSearch(parameterization=param, grid={"global": [1000.0, 500.0]}, objective=NetForceMagnitude()).run(driver)

    assert driver.seen == pytest.approx([1.0, 0.5])
    assert study.space["global"].unit == "mm"
    assert study.to_dataframe().attrs["units"] == {"global": "mm"}


def test_grid_search_records_one_point_each_and_finds_best() -> None:
    param = _parameterization()
    grid: dict[str, list[float]] = {"global": [1.0, 0.5, 0.2]}
    driver = _FakeDriver()

    study = GridSearch(parameterization=param, grid=grid, objective=NetForceMagnitude()).run(driver)

    # One record per grid point, evaluated in grid order.
    assert len(study.records) == 3
    assert driver.seen == [1.0, 0.5, 0.2]
    assert [record.point["global"] for record in study.records] == [1.0, 0.5, 0.2]

    # Finest mesh (global=0.2) gives the largest force -> the maximizing best.
    best = study.best()
    assert best.point == {"global": 0.2}
    assert best.objective == pytest.approx(5.0)

    # Each record's run_id is the named cache_key of the config it actually evaluated; distinct points -> distinct ids.
    for record in study.records:
        expected = bundle_name(cache_key(param.to_config(record.point)))
        assert record.run_id == expected
    assert len({record.run_id for record in study.records}) == 3


def _fake_solver(config: SimulationConfig, sim_dir: Path) -> MeshStats:
    """Copy a finished run's outputs into sim_dir so the driver writes a real bundle without gmsh/Elmer."""
    for name in ("case.dat", "case.dat.names", "elmersolver.log"):
        shutil.copyfile(RUN_FIXTURES / name, sim_dir / name)
    return MeshStats(element_count=1000, min_element_size=0.05, max_element_size=1.0)


def test_grid_search_run_ids_resolve_to_written_bundles(tmp_path: Path) -> None:
    param = _parameterization()
    grid: dict[str, list[float]] = {"global": [1.0, 0.5]}
    driver = Driver(runs_dir=tmp_path, code_sha="sha-test", solver=_fake_solver)

    study = GridSearch(parameterization=param, grid=grid, objective=NetForceMagnitude()).run(driver)

    # Every recorded run_id points at a real bundle directory whose manifest agrees.
    assert len(study.records) == 2
    for record in study.records:
        bundle = tmp_path / record.run_id
        assert (bundle / "manifest.json").is_file()
        assert load_manifest(bundle).run_id == record.run_id
