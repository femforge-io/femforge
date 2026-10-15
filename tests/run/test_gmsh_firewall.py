"""A driver that meshes through a WorkerMesher never loads gmsh, whether it meshes uncached, produces a cached mesh, or rebinds one."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

pytest.importorskip("gmsh")
pytest.importorskip("build123d")

import gmsh  # imported after the gate so the file still collects where gmsh is absent

import femforge


ROOT = Path(__file__).resolve().parents[2]
"""The checkout root, which holds the ``tests`` package the child runs."""

SRC = Path(femforge.__file__).resolve().parents[1]
"""The source tree under test, which the child and its mesh workers must import."""

FIXTURES = Path(__file__).parent / "fixtures"
"""A finished run's solver outputs, which the stand-in Elmer replays."""


def test_a_worker_driver_never_loads_gmsh(tmp_path: Path) -> None:
    paths = [str(SRC), str(ROOT), os.environ.get("PYTHONPATH", "")]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(path for path in paths if path), "PYTHONUTF8": "1"}

    done = subprocess.run(
        [sys.executable, "-m", "tests.run.worker_driver", str(tmp_path / "runs"), str(tmp_path / "store"), str(FIXTURES)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=tmp_path,
        timeout=600,
        check=False,
    )

    assert done.returncode == 0, done.stderr[-4000:]
    outcome = cast("dict[str, object]", json.loads(done.stdout.splitlines()[-1]))
    assert outcome == {"statuses": ["ok", "ok", "ok"], "msh": True, "gmsh_version": gmsh.__version__, "gmsh_loaded": False}
