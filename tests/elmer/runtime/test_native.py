"""NativeElmer: its tool lookup, and convert, partition and solve against python children standing in for the Elmer tools."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

import femforge.elmer.runtime.native as native
from femforge.common.log import log
from femforge.elmer.runtime.native import NativeElmer
from femforge.elmer.runtime.tools import ElmerToolsLocations
from femforge.execution.streaming import CommandError, CommandResult


def _tools() -> ElmerToolsLocations:
    return ElmerToolsLocations(ElmerGrid="EG", ElmerSolver="ES")


def _py_tools() -> ElmerToolsLocations:
    """Every tool is the python interpreter, so a script named like the tool's first argument plays the tool."""
    return ElmerToolsLocations(ElmerGrid=sys.executable, ElmerSolver=sys.executable)


def _install_fake_convert(mesh_dir: Path, *, create: bool = True) -> None:
    """A script named ``14``: ``python 14 2 <msh> ...`` plays ElmerGrid's conversion, creating the ``<stem>/`` mesh dir."""
    script: str = (
        "import sys\n"
        "from pathlib import Path\n"
        "print('fake elmergrid:', ' '.join(sys.argv[1:]))\n"
        f"if {create}:\n"
        "    out = Path(Path(sys.argv[2]).stem)\n"
        "    out.mkdir(exist_ok=True)\n"
        "    for name in ('mesh.header', 'mesh.nodes'):\n"
        "        (out / name).write_text(name, encoding='utf-8')\n"
    )
    _ = (mesh_dir / "14").write_text(script, encoding="utf-8")


def _install_fake_partition(working_dir: Path) -> None:
    """A script named ``2``: ``python 2 2 <mesh> -metiskway <n> ...`` plays ElmerGrid's partitioner."""
    script: str = "import sys\nfrom pathlib import Path\nprint('fake elmergrid:', ' '.join(sys.argv[1:]))\nPath(sys.argv[2], 'partitioning.' + sys.argv[4]).mkdir(parents=True)\n"
    _ = (working_dir / "2").write_text(script, encoding="utf-8")


class _CommandRecorder:
    """Stands in for ``run_streaming``: records each command and reports a clean exit."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, args: list[str], *, log_path: Path, **_: object) -> CommandResult:
        self.commands.append(args)

        return CommandResult(returncode=0, duration_s=0.0, log_path=log_path, timed_out=False)


def test_native_elmer_is_not_partitioned() -> None:
    assert NativeElmer(tools=_tools()).partitioned is False


def test_native_tools_are_located_once(monkeypatch: pytest.MonkeyPatch) -> None:
    located: list[int] = []
    tools = _tools()

    def _locate() -> ElmerToolsLocations:
        located.append(1)

        return tools

    monkeypatch.setattr(native, "locate_elmer_tools", _locate)
    backend = NativeElmer()

    assert backend.tools is tools
    assert backend.tools is tools
    assert located == [1]


def test_grid_convert_runs_elmergrid_in_the_mesh_dir_and_moves_the_mesh(tmp_path: Path) -> None:
    mesh_dir = tmp_path / "meshsrc"
    mesh_dir.mkdir()
    out_dir = tmp_path / "bundle"
    _install_fake_convert(mesh_dir)

    result = NativeElmer(tools=_py_tools()).grid_convert(mesh_dir, "model.msh", out_dir=out_dir, scale=0.001)

    assert result.returncode == 0
    assert (out_dir / "mesh.header").is_file() and (out_dir / "mesh.nodes").is_file()
    assert not (mesh_dir / "model").exists()
    assert "fake elmergrid: 2 model.msh -scale 0.001 0.001 0.001" in (out_dir / "elmergrid.log").read_text(encoding="utf-8")


def test_grid_convert_keeps_the_mesh_dir_on_request(tmp_path: Path) -> None:
    _install_fake_convert(tmp_path)

    _ = NativeElmer(tools=_py_tools()).grid_convert(tmp_path, "model.msh", out_dir=tmp_path, keep_mesh_dir=True)

    assert (tmp_path / "model" / "mesh.header").is_file()


def test_grid_convert_raises_when_elmergrid_leaves_no_mesh(tmp_path: Path) -> None:
    _install_fake_convert(tmp_path, create=False)

    with pytest.raises(CommandError, match="no mesh directory"):
        _ = NativeElmer(tools=_py_tools()).grid_convert(tmp_path, "model.msh", out_dir=tmp_path)


def test_grid_partition_runs_beside_the_mesh_dir_and_checks_its_partitions(tmp_path: Path) -> None:
    mesh_dir = tmp_path / "model"
    mesh_dir.mkdir()
    _install_fake_partition(tmp_path)

    result = NativeElmer(tools=_py_tools()).grid_partition(mesh_dir, 4, halobc=True)

    assert result.returncode == 0
    assert (mesh_dir / "partitioning.4").is_dir()
    assert "2 model -metiskway 4 -halobc" in (mesh_dir / "elmergrid_partition.log").read_text(encoding="utf-8")


def test_grid_partition_refuses_a_dotted_mesh_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="first dot"):
        _ = NativeElmer(tools=_py_tools()).grid_partition(tmp_path / "run.v2", 4, halobc=False)


def test_solve_runs_the_serial_solver_on_the_sif_in_the_case_dir(tmp_path: Path) -> None:
    _ = (tmp_path / "case.sif").write_text("import sys\nprint('solver output')\nsys.exit(9)\n", encoding="utf-8")

    result = NativeElmer(tools=_py_tools()).solve(tmp_path)

    assert result.returncode == 9
    assert (tmp_path / "elmersolver.log").read_text(encoding="utf-8") == "solver output\n"


def test_a_parallel_solve_launches_the_mpi_solver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _CommandRecorder()
    monkeypatch.setattr(native, "run_streaming", recorder)
    tools = ElmerToolsLocations(ElmerGrid="EG", ElmerSolver="ES", ElmerSolver_mpi="ESMPI", mpiexec="MPX")

    _ = NativeElmer(tools=tools).solve(tmp_path, n_procs=4)

    assert recorder.commands == [["MPX", "-n", "4", "ESMPI", "case.sif"]]


def test_a_parallel_solve_without_mpi_runs_serially_and_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _CommandRecorder()
    monkeypatch.setattr(native, "run_streaming", recorder)
    buffer = io.StringIO()
    sink_id: int = log.add(buffer, level="WARNING", format="{message}")

    try:
        _ = NativeElmer(tools=_tools()).solve(tmp_path, n_procs=4)
    finally:
        log.remove(sink_id)

    assert recorder.commands == [["ES", "case.sif"]]
    assert "running the solve serially" in buffer.getvalue()
