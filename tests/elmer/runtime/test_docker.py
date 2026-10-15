"""DockerElmer: the container command each Elmer tool runs, the image ensured before the timer, and the kill a timeout sends."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

import femforge.elmer.runtime.docker as docker_backend
from femforge.elmer.runtime.docker import DockerElmer
from femforge.elmer.runtime.docker_image import ELMER_IMAGE
from femforge.execution.docker.cli import CONTAINER_PREFIX, DockerCli
from femforge.execution.docker.image import DockerImage
from femforge.execution.streaming import CommandResult


class _FakeDocker(DockerCli):
    """A docker client with a fixed host user and every image at hand, which records kills instead of sending them."""

    def __init__(self, user: str | None = None) -> None:
        super().__init__()
        # Fills the cached property, so no test asks a daemon for its security options.
        self.__dict__["host_user"] = user
        self.killed: list[str] = []

    def has_image(self, tag: str) -> bool:
        return True

    def kill(self, container: str) -> None:
        self.killed.append(container)


class _Run:
    """One recorded ``run_streaming`` call."""

    def __init__(self, args: list[str], kwargs: dict[str, object]) -> None:
        self.args: list[str] = args
        self.kwargs: dict[str, object] = kwargs

    @property
    def container(self) -> str:
        return self.args[self.args.index("--name") + 1]


class _Recorder:
    """Stands in for ``run_streaming``: records each command, then plays the tool's side effect."""

    def __init__(self, effect: Callable[[], None] | None = None, events: list[str] | None = None) -> None:
        self.runs: list[_Run] = []
        self.effect: Callable[[], None] | None = effect
        self.events: list[str] = events if events is not None else []

    def __call__(self, args: list[str], *, log_path: Path, **kwargs: object) -> CommandResult:
        self.events.append("run")
        self.runs.append(_Run(args, {"log_path": log_path, **kwargs}))
        _ = log_path.write_text("elmergrid output\n", encoding="utf-8")
        if self.effect is not None:
            self.effect()

        return CommandResult(returncode=0, duration_s=0.0, log_path=log_path, timed_out=False)


@pytest.fixture(autouse=True)
def _docker_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def _which(name: str) -> str | None:
        return "docker"

    monkeypatch.setattr(shutil, "which", _which)


def test_docker_elmer_runs_femforge_image_by_default_and_is_partitioned() -> None:
    backend = DockerElmer()

    assert backend.image == ELMER_IMAGE.image()
    assert backend.partitioned is True
    assert DockerElmer(DockerImage("mine:1")).image == DockerImage("mine:1")


def test_grid_convert_runs_elmergrid_in_a_container_over_the_mesh_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def converted() -> None:
        (tmp_path / "geometry").mkdir()
        _ = (tmp_path / "geometry" / "mesh.header").write_text("header", encoding="utf-8")

    recorder = _Recorder(converted)
    monkeypatch.setattr(docker_backend, "run_streaming", recorder)
    out_dir = tmp_path / "bundle"

    _ = DockerElmer(DockerImage("img"), docker=_FakeDocker("1000:100")).grid_convert(tmp_path, "geometry.msh", out_dir=out_dir, autoclean=True)

    run = recorder.runs[0]
    assert run.args == [
        "docker",
        "run",
        "--rm",
        "--name",
        run.container,
        "--user",
        "1000:100",
        "-v",
        f"{tmp_path.resolve()}:/case",
        "-w",
        "/case",
        "img",
        "ElmerGrid",
        "14",
        "2",
        "geometry.msh",
        "-autoclean",
    ]
    assert run.container.startswith(CONTAINER_PREFIX)
    assert run.kwargs["log_path"] == out_dir / "elmergrid.log"
    assert (out_dir / "mesh.header").read_text(encoding="utf-8") == "header"


def test_grid_partition_addresses_the_mount_from_the_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mesh_dir = tmp_path / "run.v2"
    mesh_dir.mkdir()

    def partitioned() -> None:
        (mesh_dir / "partitioning.4").mkdir()

    recorder = _Recorder(partitioned)
    monkeypatch.setattr(docker_backend, "run_streaming", recorder)

    result = DockerElmer(DockerImage("img"), docker=_FakeDocker()).grid_partition(mesh_dir, 4, halobc=True)

    run = recorder.runs[0]
    assert result.returncode == 0
    assert "--user" not in run.args
    assert run.args[-10:] == ["-w", "/", "img", "ElmerGrid", "2", "2", "case", "-metiskway", "4", "-halobc"]
    assert run.kwargs["log_path"] == mesh_dir / "elmergrid_partition.log"


def test_solve_runs_the_serial_or_the_mpi_solver_and_forwards_the_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(docker_backend, "run_streaming", recorder)
    backend = DockerElmer(DockerImage("img"), docker=_FakeDocker())

    _ = backend.solve(tmp_path)
    _ = backend.solve(tmp_path, n_procs=4, timeout_s=60.0)

    serial, parallel = recorder.runs
    assert serial.args[-4:] == ["-w", "/case", "img", "ElmerSolver"]
    assert parallel.args[-8:] == ["-w", "/case", "img", "mpirun", "--oversubscribe", "-np", "4", "ElmerSolver_mpi"]
    assert parallel.kwargs["timeout_s"] == 60.0
    assert parallel.kwargs["log_path"] == tmp_path / "elmersolver.log"


def test_a_container_runs_on_the_image_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(docker_backend, "run_streaming", recorder)

    _ = DockerElmer(DockerImage("img", platform="linux/amd64"), docker=_FakeDocker()).solve(tmp_path)

    args = recorder.runs[0].args
    assert args[args.index("--platform") + 1] == "linux/amd64"


def test_a_timeout_kill_stops_the_named_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(docker_backend, "run_streaming", recorder)
    docker = _FakeDocker()

    _ = DockerElmer(DockerImage("img"), docker=docker).solve(tmp_path)

    run = recorder.runs[0]
    kill = run.kwargs["kill"]
    assert callable(kill)

    _ = kill()

    assert docker.killed == [run.container]


def test_the_image_is_ensured_before_the_command_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    def ensure(image: DockerImage, docker: DockerCli) -> None:
        events.append(f"ensure {image.tag}")

    monkeypatch.setattr(docker_backend, "ensure_image", ensure)
    monkeypatch.setattr(docker_backend, "run_streaming", _Recorder(events=events))

    _ = DockerElmer(DockerImage("img", "FROM scratch\n"), docker=_FakeDocker()).solve(tmp_path)

    assert events == ["ensure img", "run"]
