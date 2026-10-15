"""The execution seam: where ElmerGrid and ElmerSolver run, whether natively, in Docker, or on a remote host."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from femforge.execution.streaming import CancelToken, CommandResult, LineSink


__all__ = ["ExecutionBackend"]


class ExecutionBackend(Protocol):
    """Runs the Elmer tools over local directories; a remote backend uploads from and downloads into the same paths."""

    @property
    def partitioned(self) -> bool:
        """Whether a solve with more than one MPI rank is trustworthy here."""
        ...

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
        """Convert the gmsh mesh ``mesh_dir/msh_name`` into Elmer mesh files plus ``elmergrid.log`` in ``out_dir``; raises ``CommandError`` on failure.

        Args:
            mesh_dir: The directory holding the ``.msh``.
            msh_name: The ``.msh`` file name.
            out_dir: Where the mesh files and the log land.
            scale: A factor applied to every coordinate; None keeps them.
            autoclean: Run ElmerGrid's ``-autoclean`` pass.
            keep_mesh_dir: Keep the mesh files in ElmerGrid's ``<stem>/`` dir instead of moving them into ``out_dir``.
            on_line: Receives each output line.
            timeout_s: Seconds before the conversion is stopped; None waits for it.
            cancel: Stops the conversion once it is set.
        """
        ...

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
        """Split the Elmer mesh dir ``mesh_dir`` into ``partitioning.<n_parts>`` inside it; raises ``CommandError`` on failure.

        Args:
            mesh_dir: The Elmer mesh dir itself; a backend that addresses it by name needs that name dot-free.
            n_parts: The partition count.
            halobc: Add the halo elements that boundary-terminated coil electrodes need.
            on_line: Receives each output line.
            timeout_s: Seconds before the partitioning is stopped; None waits for it.
            cancel: Stops the partitioning once it is set.
        """
        ...

    def solve(
        self,
        sim_dir: Path,
        *,
        n_procs: int = 1,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        """Run ElmerSolver on the case in ``sim_dir``, reading only inside it; a failed solve returns its nonzero exit instead of raising.

        Args:
            sim_dir: The case directory, which receives ``elmersolver.log`` and the solver's outputs.
            n_procs: The MPI rank count.
            on_line: Receives each output line.
            timeout_s: Seconds before the solve is stopped; None waits for it.
            cancel: Stops the solve once it is set.
        """
        ...
