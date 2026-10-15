"""DockerCli: executable lookup, run commands, container names, the host user, kill, captured output, image labels, and builds."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

import femforge.execution.docker.cli as docker_cli
from femforge.common.log import log
from femforge.execution.docker.cli import CASE_DIR, CONTAINER_PREFIX, DockerCli, DockerComponent, DockerError, DockerInfo, DockerVersion, container_name


if TYPE_CHECKING:
    from loguru import Message


def _which_docker(name: str) -> str | None:
    return "docker"


class _FakeBuild:
    """A stand-in for the ``docker build`` process that records its argv and the Dockerfile it was handed."""

    def __init__(self, args: list[str], returncode: int, seen: list[str]) -> None:
        recipe = Path(args[args.index("--file") + 1])
        seen.extend([*args, recipe.read_text(encoding="utf-8")])
        self.stdout: io.StringIO = io.StringIO("#1 [internal] load build definition\n#2 DONE 0.1s\n")
        self._returncode: int = returncode

    def wait(self) -> int:
        return self._returncode


def test_the_executable_is_located_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _which(name: str) -> str | None:
        calls.append(name)

        return "docker"

    monkeypatch.setattr(shutil, "which", _which)
    docker = DockerCli()

    assert docker.exe() == "docker"
    assert docker.exe() == "docker"
    assert calls == ["docker"]


def test_a_missing_executable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _which(name: str) -> str | None:
        return None

    monkeypatch.setattr(shutil, "which", _which)

    with pytest.raises(DockerError, match="needs `docker` on PATH"):
        _ = DockerCli().exe()


def test_run_args_resolve_the_mount_and_set_the_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", _which_docker)
    monkeypatch.chdir(tmp_path)

    args = DockerCli().run_args(
        "femforge-elmer:0.1.0",
        mount=Path("runs"),
        workdir=CASE_DIR,
        container="femforge_t",
        args=["ElmerSolver"],
    )

    assert args == ["docker", "run", "--rm", "--name", "femforge_t", "-v", f"{(tmp_path / 'runs').resolve()}:/case", "-w", "/case", "femforge-elmer:0.1.0", "ElmerSolver"]


def test_run_args_add_the_platform_and_the_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", _which_docker)

    args = DockerCli().run_args(
        "img",
        mount=tmp_path,
        workdir="/",
        container="femforge_t",
        args=["true"],
        user="1000:100",
        platform="linux/amd64",
    )

    assert args[:9] == ["docker", "run", "--rm", "--name", "femforge_t", "--platform", "linux/amd64", "--user", "1000:100"]


def test_container_names_carry_the_prefix_and_are_unique() -> None:
    first, second = container_name(), container_name()

    assert first.startswith(CONTAINER_PREFIX) and second.startswith(CONTAINER_PREFIX)
    assert first != second


def _fake_info(stdout: str, calls: list[list[str]]) -> object:
    def _run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)

        return subprocess.CompletedProcess(args, 0, stdout, "")

    return _run


def _info_json(security_options: list[str] | None) -> str:
    info: dict[str, object] = {
        "ServerVersion": "29.8.2",
        "OperatingSystem": "Ubuntu 24.04.3 LTS",
        "Architecture": "x86_64",
        "DockerRootDir": "/var/lib/docker",
        "SecurityOptions": security_options,
    }

    return json.dumps(info) + "\n"


def _on_linux(monkeypatch: pytest.MonkeyPatch, stdout: str, calls: list[list[str]]) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(os, "getgid", lambda: 100, raising=False)
    monkeypatch.setattr(shutil, "which", _which_docker)
    monkeypatch.setattr(subprocess, "run", _fake_info(stdout, calls))


def test_host_user_maps_a_rootful_linux_daemon_to_the_host_ids_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    _on_linux(monkeypatch, _info_json(["name=apparmor", "name=seccomp,profile=builtin"]), calls)
    docker = DockerCli()

    first, second = docker.host_user, docker.host_user

    assert first == second == "1000:100"
    assert calls == [["docker", "info", "--format", "{{json .}}"]]


def test_host_user_is_none_for_a_rootless_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    _on_linux(monkeypatch, _info_json(["name=seccomp,profile=builtin", "name=rootless", "name=cgroupns"]), [])

    assert DockerCli().host_user is None


def test_host_user_reads_a_daemon_without_security_options_as_rootful(monkeypatch: pytest.MonkeyPatch) -> None:
    _on_linux(monkeypatch, _info_json(None), [])

    assert DockerCli().host_user == "1000:100"


def test_host_user_is_none_off_linux_without_asking_the_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", _fake_info("[]", calls))

    assert DockerCli().host_user is None
    assert calls == []


def test_kill_ignores_an_exited_container(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[list[str]] = []

    def _fake_run(args: list[str], capture_output: bool = False, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        recorded.append(args)

        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr(shutil, "which", _which_docker)

    DockerCli().kill("femforge_z")

    assert recorded == [["docker", "kill", "femforge_z"]]


def _fake_output(returncode: int, stdout: str = "", stderr: str = "") -> object:
    def _run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    return _run


def test_output_strips_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_output(0, stdout="9.0-1\n"))
    monkeypatch.setattr(shutil, "which", _which_docker)

    assert DockerCli().output(["run", "img"]) == "9.0-1"


def test_output_raises_with_stderr_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_output(125, stderr="no such image\n"))
    monkeypatch.setattr(shutil, "which", _which_docker)

    with pytest.raises(DockerError, match="docker run exited with code 125\nno such image") as raised:
        _ = DockerCli().output(["run", "img"])

    assert raised.value.returncode == 125


def test_image_label_reads_the_label_or_none_for_a_missing_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", _which_docker)

    monkeypatch.setattr(subprocess, "run", _fake_output(0, stdout="abc123\n"))
    assert DockerCli().image_label("img:1", "femforge.recipe") == "abc123"

    monkeypatch.setattr(subprocess, "run", _fake_output(1, stderr="No such image: img:1"))
    assert DockerCli().image_label("img:1", "femforge.recipe") is None


def test_build_hands_docker_the_dockerfile_and_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def _popen(args: list[str], **_: object) -> _FakeBuild:
        return _FakeBuild(args, 0, seen)

    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(shutil, "which", _which_docker)

    DockerCli().build("img:1", "FROM scratch\n", labels=["femforge.recipe=abc"])

    assert seen[:6] == ["docker", "build", "--progress=plain", "--tag", "img:1", "--label"]
    assert seen[6] == "femforge.recipe=abc"
    assert seen[-1] == "FROM scratch\n"


def test_a_failed_build_raises_with_its_output_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    def _popen(args: list[str], **_: object) -> _FakeBuild:
        return _FakeBuild(args, 1, [])

    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(shutil, "which", _which_docker)

    with pytest.raises(DockerError, match="docker build of img:1 exited with code 1") as raised:
        DockerCli().build("img:1", "FROM scratch\n", labels=[])

    assert raised.value.output_tail.endswith("#2 DONE 0.1s")


def test_a_build_for_a_platform_passes_it_to_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def _popen(args: list[str], **_: object) -> _FakeBuild:
        return _FakeBuild(args, 0, seen)

    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(shutil, "which", _which_docker)

    DockerCli().build("img:1", "FROM scratch\n", labels=[], platform="linux/amd64")

    assert seen[:5] == ["docker", "build", "--progress=plain", "--platform", "linux/amd64"]


def test_version_reads_the_client_and_the_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    component = {"Version": "29.8.2", "ApiVersion": "1.56", "Os": "linux", "Arch": "amd64", "GitCommit": "abc"}
    monkeypatch.setattr(subprocess, "run", _fake_output(0, stdout=json.dumps({"Client": component, "Server": component})))
    monkeypatch.setattr(shutil, "which", _which_docker)

    version = DockerCli().version()

    assert version == DockerVersion(Client=DockerComponent(Version="29.8.2", ApiVersion="1.56", Os="linux", Arch="amd64"), Server=version.client)
    assert version.server is not None and version.server.version == "29.8.2"


def test_info_reads_the_daemon_and_whether_it_runs_rootless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_output(0, stdout=_info_json(["name=rootless"])))
    monkeypatch.setattr(shutil, "which", _which_docker)

    info = DockerCli().info()

    assert isinstance(info, DockerInfo)
    assert (info.server_version, info.root_dir, info.architecture) == ("29.8.2", "/var/lib/docker", "x86_64")
    assert info.rootless is True


def _hangs(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    raise subprocess.TimeoutExpired(args, cast("float", kwargs["timeout"]))


def test_a_docker_call_past_its_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _hangs)
    monkeypatch.setattr(shutil, "which", _which_docker)

    with pytest.raises(DockerError, match="docker info did not answer within 10.0 s"):
        _ = DockerCli().info(timeout_s=10.0)


def test_a_registry_lookup_past_its_timeout_cannot_tell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _hangs)
    monkeypatch.setattr(shutil, "which", _which_docker)

    assert DockerCli().registry_has("img:1", timeout_s=10.0) is None


def test_has_image_asks_whether_docker_can_inspect_the_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", _which_docker)

    monkeypatch.setattr(subprocess, "run", _fake_output(0))
    assert DockerCli().has_image("img:1") is True

    monkeypatch.setattr(subprocess, "run", _fake_output(1, stderr="No such image: img:1"))
    assert DockerCli().has_image("img:1") is False


@pytest.mark.parametrize(
    ("returncode", "stderr", "expected"),
    [
        (0, "", True),
        (1, "no such manifest: docker.io/library/tool:9", False),
        (1, "manifest unknown: manifest unknown", False),
        (1, 'Get "https://ghcr.io/v2/femforge-io/femforge-elmer/manifests/0.2.0": denied', None),
        (1, "unauthorized: authentication required", None),
        (1, "dial tcp: lookup ghcr.io: no such host", None),
    ],
)
def test_registry_has_tells_a_published_tag_from_a_missing_one_and_an_unknown_one(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stderr: str, expected: bool | None
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_output(returncode, stderr=stderr))
    monkeypatch.setattr(shutil, "which", _which_docker)

    assert DockerCli().registry_has("ghcr.io/femforge-io/femforge-elmer:0.2.0") is expected


class _FakePull:
    """A stand-in ``docker pull``: canned output, an exit code, and how many waits time out before it exits."""

    def __init__(self, args: list[str], output: str, returncode: int, timeouts: int, seen: list[list[str]]) -> None:
        seen.append(args)
        self.stdout: io.StringIO = io.StringIO(output)
        self.returncode: int = returncode
        self.timeouts: int = timeouts
        self.killed: bool = False

    def wait(self, timeout: float | None = None) -> int:
        if self.timeouts > 0 and not self.killed:
            self.timeouts -= 1
            raise subprocess.TimeoutExpired("docker pull", timeout or 0.0)

        return -9 if self.killed else self.returncode

    def kill(self) -> None:
        self.killed = True


@pytest.fixture
def pull_records() -> Iterator[list[str]]:
    """The messages logged while a test pulls."""
    messages: list[str] = []

    def keep(message: Message) -> None:
        messages.append(message.record["message"])

    sink_id: int = log.add(keep, level="INFO")
    yield messages
    log.remove(sink_id)


def _fake_pulls(monkeypatch: pytest.MonkeyPatch, output: str, *, returncode: int = 0, timeouts: int = 0) -> list[list[str]]:
    seen: list[list[str]] = []

    def _popen(args: list[str], **_: object) -> _FakePull:
        return _FakePull(args, output, returncode, timeouts, seen)

    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(shutil, "which", _which_docker)

    return seen


PULL_OUTPUT = "0.2.0: Pulling from femforge-io/femforge-elmer\nabc123: Pulling fs layer\nabc123: Pull complete\ndef456: Pull complete\nStatus: Downloaded newer image\n"
"""What ``docker pull`` prints for a two-layer image."""


def test_pull_logs_each_finished_layer(monkeypatch: pytest.MonkeyPatch, pull_records: list[str]) -> None:
    seen = _fake_pulls(monkeypatch, PULL_OUTPUT)

    DockerCli().pull("ghcr.io/femforge-io/femforge-elmer:0.2.0", platform="linux/amd64")

    assert seen == [["docker", "pull", "--platform", "linux/amd64", "ghcr.io/femforge-io/femforge-elmer:0.2.0"]]
    assert pull_records.count("Pulled an image layer.") == 2


def test_a_slow_pull_logs_a_heartbeat(monkeypatch: pytest.MonkeyPatch, pull_records: list[str]) -> None:
    _ = _fake_pulls(monkeypatch, PULL_OUTPUT, timeouts=2)

    DockerCli().pull("img:1")

    assert pull_records.count("Still pulling the image.") == 2


def test_a_failed_pull_raises_with_its_output_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _fake_pulls(monkeypatch, "Error response from daemon: denied\n", returncode=1)

    with pytest.raises(DockerError, match="docker pull of img:1 exited with code 1") as raised:
        DockerCli().pull("img:1")

    assert raised.value.output_tail == "Error response from daemon: denied"


def test_a_pull_past_its_timeout_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _fake_pulls(monkeypatch, "abc123: Downloading\n", timeouts=99)
    monkeypatch.setattr(docker_cli, "PULL_HEARTBEAT_S", 0.0)

    with pytest.raises(DockerError, match="ran past 0.0 s") as raised:
        DockerCli().pull("img:1", timeout_s=0.0)

    assert raised.value.output_tail == "abc123: Downloading"
