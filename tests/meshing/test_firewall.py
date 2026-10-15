"""block_gmsh: once called, no later import in the process can load gmsh, and it refuses to start after gmsh has loaded."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

import femforge
from femforge.meshing.firewall import _GmshBlocker, block_gmsh


def test_block_gmsh_refuses_every_later_gmsh_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "gmsh", raising=False)
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))

    block_gmsh()
    block_gmsh()

    with pytest.raises(ImportError, match="blocked"):
        _ = importlib.import_module("gmsh")

    assert sum(isinstance(finder, _GmshBlocker) for finder in sys.meta_path) == 1


def test_the_blocker_leaves_every_other_module_alone() -> None:
    blocker = _GmshBlocker()

    assert blocker.find_spec("numpy", None) is None
    assert blocker.find_spec("gmshfoo", None) is None

    with pytest.raises(ImportError, match="blocked"):
        _ = blocker.find_spec("gmsh.model", None)


def test_block_gmsh_refuses_once_gmsh_is_loaded() -> None:
    import gmsh

    assert "gmsh" in sys.modules and gmsh.__version__

    with pytest.raises(RuntimeError, match="already loaded"):
        block_gmsh()


def test_a_blocked_process_fails_on_import_gmsh() -> None:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(Path(femforge.__file__).resolve().parents[1]), os.environ.get("PYTHONPATH", "")])}

    done = subprocess.run(
        [sys.executable, "-c", "from femforge.meshing import block_gmsh\nblock_gmsh()\nimport gmsh"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )

    assert done.returncode != 0
    assert "ImportError: gmsh is blocked in this process" in done.stderr
