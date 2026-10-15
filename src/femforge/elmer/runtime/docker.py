"""Elmer in Docker: each tool run in a fresh container over the host directory it reads and writes."""

from __future__ import annotations

import functools
from pathlib import Path, PurePosixPath

from femforge.common.files import BundleFile
from femforge.elmer.runtime.backend import ExecutionBackend
from femforge.elmer.runtime.commands import ELMERGRID, ELMERSOLVER, ELMERSOLVER_MPI, check_partitions, finish_grid_convert, grid_convert_args, grid_partition_args
from femforge.elmer.runtime.docker_image import ELMER_IMAGE
from femforge.execution.docker.cli import CASE_DIR, DockerCli, container_name
from femforge.execution.docker.image import DockerImage, ensure_image
from femforge.execution.streaming import CancelToken, CommandResult, LineSink, run_streaming


__all__ = ["DockerElmer"]

MPIRUN = "mpirun"
"""The image's MPI launcher."""

OVERSUBSCRIBE = "--oversubscribe"
"""Open MPI counts physical cores as slots, and the rank count must match the partition count even on a smaller host."""


class DockerElmer(ExecutionBackend):
    """Runs Elmer in ``image``, femforge's Elmer image by default, built on first use; partitioned solves use the image's MUMPS."""

    def __init__(self, image: DockerImage | None = None, *, docker: DockerCli | None = None) -> None:
        """Run in ``image`` (femforge's Elmer image when None) through ``docker`` (a fresh client when None)."""
        self.image: DockerImage = image if image is not None else ELMER_IMAGE.image()
        self.docker: DockerCli = docker if docker is not None else DockerCli()

    @property
    def partitioned(self) -> bool:
        """True: the image's MUMPS makes a multi-rank solve trustworthy."""
        return True

    def _run(
        self,
        args: list[str],
        *,
        mount: Path,
        workdir: str,
        log_path: Path,
        on_line: LineSink | None,
        timeout_s: float | None,
        cancel: CancelToken | None,
    ) -> CommandResult:
        """Run ``args`` in a fresh container over ``mount``; the image is ensured first, so a build never counts against ``timeout_s``."""
        ensure_image(self.image, self.docker)

        container: str = container_name()
        command: list[str] = self.docker.run_args(
            self.image.tag,
            mount=mount,
            workdir=workdir,
            container=container,
            args=args,
            user=self.docker.host_user,
            platform=self.image.platform,
        )

        return run_streaming(
            command,
            cwd=mount,
            log_path=log_path,
            on_line=on_line,
            timeout_s=timeout_s,
            kill=functools.partial(self.docker.kill, container),
            cancel=cancel,
        )

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
        """Convert the gmsh mesh ``mesh_dir/msh_name`` in a container into Elmer mesh files plus ``elmergrid.log`` in ``out_dir``.

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
        result: CommandResult = self._run(
            [ELMERGRID, *grid_convert_args(msh_name, scale=scale, autoclean=autoclean)],
            mount=mesh_dir,
            workdir=CASE_DIR,
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
        """Split the Elmer mesh dir ``mesh_dir`` into ``partitioning.<n_parts>`` inside it, in a container.

        Args:
            mesh_dir: The Elmer mesh dir itself.
            n_parts: The partition count.
            halobc: Add the halo elements that boundary-terminated coil electrodes need.
            on_line: Receives each output line.
            timeout_s: Seconds before the partitioning is stopped; None waits for it.
            cancel: Stops the partitioning once it is set.
        """
        # The container addresses the mesh dir by its mount point, whose name is dot-free whatever the host directory is called.
        case: PurePosixPath = PurePosixPath(CASE_DIR)
        result: CommandResult = self._run(
            [ELMERGRID, *grid_partition_args(case.name, n_parts, halobc=halobc)],
            mount=mesh_dir,
            workdir=str(case.parent),
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
        """Run ElmerSolver on the case in ``sim_dir`` in a container, under ``mpirun`` when ``n_procs`` exceeds one.

        Args:
            sim_dir: The case directory, which receives ``elmersolver.log`` and the solver's outputs.
            n_procs: The MPI rank count.
            on_line: Receives each output line.
            timeout_s: Seconds before the solve is stopped; None waits for it.
            cancel: Stops the solve once it is set.
        """
        args: list[str] = [MPIRUN, OVERSUBSCRIBE, "-np", str(n_procs), ELMERSOLVER_MPI] if n_procs > 1 else [ELMERSOLVER]

        return self._run(
            args,
            mount=sim_dir,
            workdir=CASE_DIR,
            log_path=sim_dir / BundleFile.SOLVER_LOG,
            on_line=on_line,
            timeout_s=timeout_s,
            cancel=cancel,
        )
