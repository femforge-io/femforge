"""Importing femforge stays cheap: time budgets and the modules each first step loads, measured in fresh interpreters."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, cast

import pytest

import femforge


SRC = Path(femforge.__file__).parents[1]
"""The source tree under test, put first on each child's path so a child never resolves another checkout."""

RUNS = 5
"""Fresh interpreters per measurement; a budget compares against the fastest."""

SCALE = float(os.environ.get("FEMFORGE_IMPORT_BUDGET_SCALE", "1"))
"""`k` `[-]`: Multiplier on every time budget, for slow machines."""

PROBE = """\
import json, sys, time
exec(sys.argv[1])
start = time.perf_counter()
exec(sys.argv[2])
seconds = time.perf_counter() - start
print(json.dumps({"seconds": seconds, "modules": sorted(sys.modules)}))
"""
"""Runs a setup statement untimed, then times one step and reports the modules loaded after it."""


class Measurement(NamedTuple):
    """The fastest timing of one import step and the modules loaded once it ran."""

    seconds: float
    """`t` `[s]`: Fastest wall time of the step over the runs."""

    modules: frozenset[str]
    """Every module in ``sys.modules`` after the step."""


def _measure(setup: str, step: str) -> Measurement:
    env = {name: value for name, value in os.environ.items() if not name.startswith(("COV_CORE_", "COVERAGE_")) and name != "EAGER_IMPORT"}
    inherited = [entry for entry in env.get("PYTHONPATH", "").split(os.pathsep) if entry]
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), *inherited])

    timings: list[float] = []
    modules: frozenset[str] = frozenset()
    for _ in range(RUNS):
        done = subprocess.run(
            [sys.executable, "-c", PROBE, setup, step],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        report = cast("dict[str, object]", json.loads(done.stdout.splitlines()[-1]))
        timings.append(cast("float", report["seconds"]))
        modules = frozenset(cast("list[str]", report["modules"]))

    return Measurement(min(timings), modules)


@pytest.fixture(scope="module")
def root_import() -> Measurement:
    return _measure("", "import femforge")


@pytest.fixture(scope="module")
def config_access() -> Measurement:
    return _measure("import femforge as ff", "ff.SimulationConfig")


def test_import_femforge_stays_within_its_budget(root_import: Measurement) -> None:
    assert root_import.seconds < 0.060 * SCALE


def test_the_first_simulation_config_stays_within_its_budget(config_access: Measurement) -> None:
    assert config_access.seconds < 0.750 * SCALE


@pytest.mark.parametrize("module", ["pydantic", "pint", "numpy", "loguru", "rich"])
def test_import_femforge_loads_no_heavy_dependency(root_import: Measurement, module: str) -> None:
    assert module not in root_import.modules


def test_import_femforge_loads_none_of_its_subpackages(root_import: Measurement) -> None:
    loaded = sorted(name for name in root_import.modules if name.startswith("femforge."))

    assert loaded == []


@pytest.mark.parametrize(
    "module",
    [
        "gmsh",
        "build123d",
        "polars",
        "pyvista",
        "vtk",
        "vtkmodules",
        "trame",
        "loguru",
        "femforge.elmer.runtime",
        "femforge.execution",
        "femforge.run",
        "femforge.telemetry",
        "femforge_workbench",
    ],
)
def test_the_first_simulation_config_loads_no_cad_viz_or_run_stack(config_access: Measurement, module: str) -> None:
    assert module not in config_access.modules
