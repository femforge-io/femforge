"""Coverage for the native tool lookup: ``locate_elmer_tools`` / ``ElmerToolsLocations`` (PATH + Program Files lookup), monkeypatched so the lookup branches run regardless of the host."""

from __future__ import annotations

import pytest

from femforge.elmer.runtime import tools as elmer_tools
from femforge.elmer.runtime.tools import ElmerToolsLocations, locate_elmer_tools


def test_locate_uses_path_when_found(monkeypatch: pytest.MonkeyPatch) -> None:
    # shutil.which resolves everything -> all four tools (incl. the MPI pair) flow straight from PATH.
    def fake_which(name: str) -> str | None:
        return rf"C:\tools\{name}.exe"

    monkeypatch.setattr(elmer_tools.shutil, "which", fake_which)
    tools: ElmerToolsLocations = locate_elmer_tools()
    assert tools.ElmerGrid.endswith("ElmerGrid.exe")
    assert tools.ElmerSolver.endswith("ElmerSolver.exe")
    assert tools.ElmerSolver_mpi is not None and tools.ElmerSolver_mpi.endswith("ElmerSolver_mpi.exe")
    assert tools.mpiexec is not None and tools.mpiexec.endswith("mpiexec.exe")
    assert tools.can_run_parallel


def test_locate_falls_back_to_program_files(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not on PATH, but present under "C:\Program Files\Elmer *\bin" -> the glob fallback finds the Elmer tools;
    # mpiexec is MS-MPI's, found at its default install path.
    def fake_which(_name: str) -> str | None:
        return None

    def fake_glob(pattern: str) -> list[str]:
        return [pattern.replace("*", "9.0")]

    def fake_isfile(path: str) -> bool:
        return path == elmer_tools._MSMPI_DEFAULT

    monkeypatch.setattr(elmer_tools.shutil, "which", fake_which)
    monkeypatch.setattr(elmer_tools.glob, "glob", fake_glob)
    monkeypatch.setattr(elmer_tools.os.path, "isfile", fake_isfile)
    tools: ElmerToolsLocations = locate_elmer_tools()
    assert "Elmer 9.0" in tools.ElmerGrid
    assert tools.ElmerSolver.endswith("ElmerSolver.exe")
    assert tools.ElmerSolver_mpi is not None and "Elmer 9.0" in tools.ElmerSolver_mpi
    assert tools.mpiexec == elmer_tools._MSMPI_DEFAULT
    assert tools.can_run_parallel


def test_locate_without_mpi_yields_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # The required tools are on PATH, but neither ElmerSolver_mpi nor mpiexec is installed -> the MPI pair is
    # None and can_run_parallel is False (a serial solve still works).
    def fake_which(name: str) -> str | None:
        return None if name in ("mpiexec", "ElmerSolver_mpi") else rf"C:\tools\{name}.exe"

    def no_glob(_pattern: str) -> list[str]:
        return []

    def not_a_file(_path: str) -> bool:
        return False

    monkeypatch.setattr(elmer_tools.shutil, "which", fake_which)
    monkeypatch.setattr(elmer_tools.glob, "glob", no_glob)
    monkeypatch.setattr(elmer_tools.os.path, "isfile", not_a_file)
    tools: ElmerToolsLocations = locate_elmer_tools()
    assert tools.ElmerSolver_mpi is None
    assert tools.mpiexec is None
    assert not tools.can_run_parallel


def test_locate_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Neither on PATH nor in Program Files -> a clear FileNotFoundError naming the tool.
    def fake_which(_name: str) -> str | None:
        return None

    def fake_glob(_pattern: str) -> list[str]:
        return []

    monkeypatch.setattr(elmer_tools.shutil, "which", fake_which)
    monkeypatch.setattr(elmer_tools.glob, "glob", fake_glob)
    with pytest.raises(FileNotFoundError, match="ElmerGrid"):
        locate_elmer_tools()


def test_elmer_tools_locations_holds_both_paths() -> None:
    tools: ElmerToolsLocations = ElmerToolsLocations(ElmerGrid="grid.exe", ElmerSolver="solver.exe")
    assert (tools.ElmerGrid, tools.ElmerSolver) == ("grid.exe", "solver.exe")
    # The MPI pair is optional and absent by default -> no parallel run.
    assert tools.ElmerSolver_mpi is None and tools.mpiexec is None
    assert not tools.can_run_parallel


def test_native_elmer_installed_follows_the_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    found = ElmerToolsLocations(ElmerGrid="ElmerGrid", ElmerSolver="ElmerSolver", ElmerSolver_mpi=None, mpiexec=None)
    monkeypatch.setattr(elmer_tools, "locate_elmer_tools", lambda: found)

    assert elmer_tools.native_elmer_installed()

    def missing() -> ElmerToolsLocations:
        raise FileNotFoundError("ElmerGrid")

    monkeypatch.setattr(elmer_tools, "locate_elmer_tools", missing)

    assert not elmer_tools.native_elmer_installed()
