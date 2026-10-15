"""Native Elmer: the host's ElmerGrid and ElmerSolver, located on first use."""

from __future__ import annotations

from pathlib import Path

from femforge.common.files import BundleFile
from femforge.common.log import log
from femforge.elmer.runtime.backend import ExecutionBackend
from femforge.elmer.runtime.commands import check_partitions, finish_grid_convert, grid_convert_args, grid_partition_args
from femforge.elmer.runtime.tools import ElmerToolsLocations, locate_elmer_tools
from femforge.execution.streaming import CancelToken, CommandResult, LineSink, run_streaming


__all__ = ["NativeElmer"]


class NativeElmer(ExecutionBackend):
    """Runs a native Elmer install; it is not partitioned, because native builds lack the MUMPS a partitioned solve needs."""

    def __init__(self, tools: ElmerToolsLocations | None = None) -> None:
        """Run ``tools``, or the install [`locate_elmer_tools`][] finds on first use."""
        self._tools: ElmerToolsLocations | None = tools

    @property
    def tools(self) -> ElmerToolsLocations:
        """The native Elmer executables."""
        if self._tools is None:
            self._tools = locate_elmer_tools()

        return self._tools

    @property
    def partitioned(self) -> bool:
        """False: a native build lacks the MUMPS a partitioned solve needs."""
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
        """Convert the gmsh mesh ``mesh_dir/msh_name`` with the native ElmerGrid into Elmer mesh files plus ``elmergrid.log`` in ``out_dir``.

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
        out_dir.mkdir(parents=True, exist_ok=True)
        result: CommandResult = run_streaming(
            [self.tools.ElmerGrid, *grid_convert_args(msh_name, scale=scale, autoclean=autoclean)],
            cwd=mesh_dir,
            log_path=out_dir / BundleFile.ELMERGRID_LOG,
            on_line=on_line,
            timeout_s=timeout_s,
            cancel=cancel,
        )

        return finish_grid_convert(
            result,
            mesh_dir,
            msh_name,
            out_dir=out_dir,
            keep_mesh_dir=keep_mesh_dir,
        )

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
        """Split the Elmer mesh dir ``mesh_dir`` into ``partitioning.<n_parts>`` inside it with the native ElmerGrid.

        Args:
            mesh_dir: The Elmer mesh dir itself.
            n_parts: The partition count.
            halobc: Add the halo elements that boundary-terminated coil electrodes need.
            on_line: Receives each output line.
            timeout_s: Seconds before the partitioning is stopped; None waits for it.
            cancel: Stops the partitioning once it is set.
        """
        result: CommandResult = run_streaming(
            [self.tools.ElmerGrid, *grid_partition_args(mesh_dir.name, n_parts, halobc=halobc)],
            cwd=mesh_dir.parent,
            log_path=mesh_dir / BundleFile.PARTITION_LOG,
            on_line=on_line,
            timeout_s=timeout_s,
            cancel=cancel,
        )

        return check_partitions(result, mesh_dir, n_parts)

    def solve(
        self,
        sim_dir: Path,
        *,
        n_procs: int = 1,
        on_line: LineSink | None = None,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
    ) -> CommandResult:
        """Run the native ElmerSolver on the case in ``sim_dir``, serially when the MPI build is missing.

        Args:
            sim_dir: The case directory, which receives ``elmersolver.log`` and the solver's outputs.
            n_procs: The MPI rank count.
            on_line: Receives each output line.
            timeout_s: Seconds before the solve is stopped; None waits for it.
            cancel: Stops the solve once it is set.
        """
        tools: ElmerToolsLocations = self.tools
        command: list[str] = [tools.ElmerSolver, BundleFile.SIF]
        if n_procs > 1 and tools.mpiexec is not None and tools.ElmerSolver_mpi is not None:
            command = [tools.mpiexec, "-n", str(n_procs), tools.ElmerSolver_mpi, BundleFile.SIF]
        elif n_procs > 1:
            log.bind(n_procs=n_procs).warning("ElmerSolver_mpi or mpiexec is not installed; running the solve serially.")

        return run_streaming(
            command,
            cwd=sim_dir,
            log_path=sim_dir / BundleFile.SOLVER_LOG,
            on_line=on_line,
            timeout_s=timeout_s,
            cancel=cancel,
        )
