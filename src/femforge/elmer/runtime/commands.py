"""The ElmerGrid commands every backend runs: their arguments and the checks on what they leave behind."""

from __future__ import annotations

import shutil
from pathlib import Path

from femforge.common.files import partition_dir_name
from femforge.execution.streaming import CommandError, CommandResult, log_tail, require_success


__all__ = ["ELMERGRID", "ELMERSOLVER", "ELMERSOLVER_MPI", "check_partitions", "finish_grid_convert", "grid_convert_args", "grid_partition_args"]

ELMERGRID = "ElmerGrid"
"""The mesh converter and partitioner."""

ELMERSOLVER = "ElmerSolver"
"""The serial solver."""

ELMERSOLVER_MPI = "ElmerSolver_mpi"
"""The MPI build of the solver."""


def grid_convert_args(msh_name: str, *, scale: float | None = None, autoclean: bool = False) -> list[str]:
    """ElmerGrid's arguments that convert the gmsh mesh ``msh_name`` into an Elmer mesh dir named after its stem.

    Args:
        msh_name: The ``.msh`` file name in the working directory.
        scale: A factor applied to every coordinate; None keeps them.
        autoclean: Run ElmerGrid's ``-autoclean`` pass.
    """
    args: list[str] = ["14", "2", msh_name]
    if scale is not None:
        args += ["-scale", str(scale), str(scale), str(scale)]

    if autoclean:
        args.append("-autoclean")

    return args


def finish_grid_convert(result: CommandResult, mesh_dir: Path, msh_name: str, *, out_dir: Path, keep_mesh_dir: bool) -> CommandResult:
    """Raise unless the conversion left ``mesh_dir/<stem>/``, then move its files into ``out_dir`` unless ``keep_mesh_dir``.

    Args:
        result: The conversion's outcome.
        mesh_dir: The directory ElmerGrid ran in.
        msh_name: The converted ``.msh`` file name.
        out_dir: Where the mesh files belong.
        keep_mesh_dir: Keep the ``<stem>/`` layout that a ``Mesh DB "." "<stem>"`` solve reads.
    """
    _ = require_success(result, ELMERGRID)

    produced: Path = mesh_dir / Path(msh_name).stem
    if not produced.is_dir():
        raise CommandError(
            f"ElmerGrid succeeded but produced no mesh directory at {produced}",
            returncode=result.returncode,
            log_tail=log_tail(result.log_path),
        )

    if keep_mesh_dir:
        return result

    for item in sorted(produced.iterdir()):
        target: Path = out_dir / item.name
        if target.exists():
            target.unlink()

        _ = shutil.move(str(item), str(out_dir))

    shutil.rmtree(produced)
    return result


def grid_partition_args(mesh_name: str, n_parts: int, *, halobc: bool) -> list[str]:
    """ElmerGrid's arguments that split the Elmer mesh dir ``mesh_name`` into ``n_parts`` METIS partitions inside it.

    Args:
        mesh_name: The mesh dir's name in the working directory; ElmerGrid truncates its output path at the first dot.
        n_parts: The partition count.
        halobc: Add the halo elements that boundary-terminated coil electrodes need.
    """
    if "." in mesh_name:
        raise ValueError(f"ElmerGrid truncates the mesh name {mesh_name!r} at its first dot; partition a mesh directory whose name has none.")

    args: list[str] = ["2", "2", mesh_name, "-metiskway", str(n_parts)]
    if halobc:
        args.append("-halobc")

    return args


def check_partitions(result: CommandResult, mesh_dir: Path, n_parts: int) -> CommandResult:
    """Raise unless the partition run succeeded and left ``mesh_dir/partitioning.<n_parts>``.

    Args:
        result: The partition run's outcome.
        mesh_dir: The partitioned Elmer mesh dir.
        n_parts: The partition count.
    """
    _ = require_success(result, ELMERGRID)

    part_dir: Path = mesh_dir / partition_dir_name(n_parts)
    if not part_dir.is_dir():
        raise CommandError(
            f"ElmerGrid reported success but {part_dir} does not exist",
            returncode=result.returncode,
            log_tail=log_tail(result.log_path),
        )

    return result
