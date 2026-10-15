"""Every folder under ``examples/`` and the ``femforge new`` template import and build each config they can build from defaults, in a process of their own, without meshing or solving."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import femforge
import femforge_workbench
from femforge import scaffold


REPO_ROOT = Path(__file__).resolve().parents[1]
"""The repository root."""

EXAMPLES = sorted(path for path in (REPO_ROOT / "examples").iterdir() if path.is_dir() and not path.name.startswith((".", "_")))
"""Every example folder; a new folder joins the test by existing."""

FOLDERS = [*(pytest.param(path, id=path.name) for path in EXAMPLES), pytest.param(scaffold.TEMPLATE, id="project-template")]
"""The examples and the template ``femforge new`` copies, each named by its folder."""

PROBE = """
import importlib
import inspect
import sys
import typing
from pathlib import Path

import femforge as ff

folder = Path(sys.argv[1])
sys.path.insert(0, str(folder))
modules = [importlib.import_module(path.stem) for path in sorted(folder.glob("*.py"))]
builders = [
    value
    for module in modules
    for value in vars(module).values()
    if inspect.isfunction(value) and value.__module__ == module.__name__ and typing.get_type_hints(value).get("return") is ff.SimulationConfig
]
assert builders, f"{folder.name} defines no function that returns a SimulationConfig"

for builder in builders:
    config = builder()
    assert isinstance(config, ff.SimulationConfig), f"{builder.__qualname__} returned a {type(config).__name__}"
    print(f"{builder.__module__}.{builder.__qualname__}: {config.frame.label}")
"""
"""The program each example's process runs: import every module of the folder, then call each function that returns a SimulationConfig with its defaults."""


def test_the_examples_folder_holds_examples() -> None:
    assert EXAMPLES, "examples/ holds no example folder"


@pytest.mark.parametrize("example", FOLDERS)
def test_an_example_builds_its_configs(example: Path, tmp_path: Path) -> None:
    sources = [Path(femforge.__file__).parents[1], Path(femforge_workbench.__file__).parents[1]]
    env = {name: value for name, value in os.environ.items() if not name.startswith(("COV_CORE_", "COVERAGE_"))}
    env["PYTHONPATH"] = os.pathsep.join(str(source) for source in sources)

    done = subprocess.run(
        [sys.executable, "-c", PROBE, str(example)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip(), f"{example.name} built no config"
