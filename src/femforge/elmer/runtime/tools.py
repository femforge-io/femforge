"""Locating a native Elmer install: ElmerGrid and ElmerSolver, plus the optional MPI solver and launcher."""

from __future__ import annotations

import glob
import os
import shutil


__all__ = ["ElmerToolsLocations", "locate_elmer_tools", "native_elmer_installed"]


# The default Microsoft MPI launcher location on Windows (MS-MPI is what Elmer's `ElmerSolver_mpi` links against).
_MSMPI_DEFAULT = r"C:\Program Files\Microsoft MPI\Bin\mpiexec.exe"


class ElmerToolsLocations:
    """The located Elmer executables: ``ElmerGrid`` and ``ElmerSolver`` (required), plus the optional parallel pair ``ElmerSolver_mpi`` and ``mpiexec`` (``None`` when MPI isn't installed)."""

    def __init__(self, ElmerGrid: str, ElmerSolver: str, ElmerSolver_mpi: str | None = None, mpiexec: str | None = None) -> None:
        """Record the located executables.

        Args:
            ElmerGrid: The ElmerGrid executable.
            ElmerSolver: The serial ElmerSolver executable.
            ElmerSolver_mpi: The MPI build of ElmerSolver; None when MPI is not installed.
            mpiexec: The MPI launcher; None when MPI is not installed.
        """
        self.ElmerGrid: str = ElmerGrid
        self.ElmerSolver: str = ElmerSolver
        self.ElmerSolver_mpi: str | None = ElmerSolver_mpi
        """The MPI build of ElmerSolver, for parallel solves; ``None`` if not installed."""
        self.mpiexec: str | None = mpiexec
        """The MPI launcher (MS-MPI ``mpiexec``); ``None`` if not installed."""

    @property
    def can_run_parallel(self) -> bool:
        """True when both the MPI solver and an MPI launcher are available, so a partitioned solve can run."""
        return self.ElmerSolver_mpi is not None and self.mpiexec is not None


def _locate_in_elmer_bin(name: str) -> str | None:
    """A tool by ``name`` via PATH, else under a standard ``C:\\Program Files\\Elmer *\\bin`` install; ``None`` if absent."""
    found: str | None = shutil.which(name)
    if found is None:
        candidates: list[str] = glob.glob(rf"C:\Program Files\Elmer *\bin\{name}.exe")
        found = candidates[0] if candidates else None
    return found


def locate_elmer_tools() -> ElmerToolsLocations:
    """
    Locate the Elmer executables. ``ElmerGrid`` / ``ElmerSolver`` are required (a ``FileNotFoundError`` names the first
    missing); ``ElmerSolver_mpi`` and ``mpiexec`` are optional and resolve to ``None`` when MPI isn't installed.
    """
    tools: dict[str, str] = {}
    for name in ("ElmerGrid", "ElmerSolver"):
        found: str | None = _locate_in_elmer_bin(name)
        if found is None:
            raise FileNotFoundError(f"Could not locate {name} executable, please ensure it is installed and in PATH.")
        tools[name] = found

    # Optional MPI pair: the parallel ElmerSolver ships in the Elmer bin; mpiexec is MS-MPI's launcher (PATH or its default install dir).
    elmer_solver_mpi: str | None = _locate_in_elmer_bin("ElmerSolver_mpi")
    mpiexec: str | None = shutil.which("mpiexec") or (_MSMPI_DEFAULT if os.path.isfile(_MSMPI_DEFAULT) else None)

    return ElmerToolsLocations(ElmerGrid=tools["ElmerGrid"], ElmerSolver=tools["ElmerSolver"], ElmerSolver_mpi=elmer_solver_mpi, mpiexec=mpiexec)


def native_elmer_installed() -> bool:
    """Whether ``ElmerGrid`` and ``ElmerSolver`` are installed where [`locate_elmer_tools`][] looks."""
    try:
        _ = locate_elmer_tools()
    except FileNotFoundError:
        return False

    return True
