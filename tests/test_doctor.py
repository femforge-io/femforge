"""femforge doctor: each check's verdict and fix against faked probes, the table and JSON reports, and the exit code."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple, cast

import pytest

from femforge import cli, doctor, schemas
from femforge.doctor import GMSH_APT_LIBRARIES, Check, CheckName, CheckStatus, DoctorReport, diagnose
from femforge.elmer.runtime.tools import ElmerToolsLocations
from femforge.execution.docker.cli import DockerCli, DockerComponent, DockerError, DockerInfo, DockerVersion
from femforge.execution.docker.image import ImageRecipe, recipe_digest
from femforge.meshing.backend import WorkerProbe


DOCKERFILE = "FROM scratch\n"
COMPONENT = DockerComponent(Version="29.8.2", ApiVersion="1.56", Os="linux", Arch="amd64")
QUALITY = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "quality.yml"
"""The CI workflow whose runner installs the libraries gmsh loads."""


class _Usage(NamedTuple):
    total: int
    used: int
    free: int


class _FakeDocker(DockerCli):
    """A Docker client whose daemon, local image label, registry answer and image probe a test chooses."""

    def __init__(
        self,
        *,
        root: Path,
        architecture: str = "x86_64",
        error: str | None = None,
        label: str | None = None,
        registry: bool | None = None,
        probe_fails: bool = False,
    ) -> None:
        super().__init__()
        self.daemon = DockerInfo(
            ServerVersion="29.8.2",
            OperatingSystem="Ubuntu 24.04.3 LTS",
            Architecture=architecture,
            DockerRootDir=str(root),
            SecurityOptions=["name=rootless"],
        )
        self.error: str | None = error
        self.label: str | None = label
        self.registry: bool | None = registry
        self.probe_fails: bool = probe_fails
        self.commands: list[list[str]] = []

    def version(self, *, timeout_s: float | None = None) -> DockerVersion:
        if self.error is not None:
            raise DockerError("docker version exited with code 1", returncode=1, output_tail=self.error)

        return DockerVersion(Client=COMPONENT, Server=COMPONENT)

    def info(self, *, timeout_s: float | None = None) -> DockerInfo:
        return self.daemon

    def image_label(self, tag: str, label: str) -> str | None:
        return self.label

    def registry_has(self, tag: str, *, timeout_s: float | None = None) -> bool | None:
        return self.registry

    def output(self, args: Sequence[str], *, timeout_s: float | None = None) -> str:
        self.commands.append(list(args))
        if self.probe_fails:
            raise DockerError("docker run exited with code 125", returncode=125)

        return "9.0-1"


def _done(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _on_path(name: str) -> str | None:
    return name


def _not_on_path(name: str) -> str | None:
    return None


def _roomy(path: Path) -> _Usage:
    return _Usage(10**13, 0, 10**12)


def _cramped(path: Path) -> _Usage:
    return _Usage(10**13, 0, 10**9)


GMSH_OK = _done(stdout=WorkerProbe(gmsh="4.15.2").model_dump_json())
CAD_OK = _done(stdout="build123d 0.13.0 on cadquery-ocp-novtk 8.0.1\n")
TOOLS = ElmerToolsLocations(ElmerGrid="ElmerGrid", ElmerSolver="ElmerSolver", ElmerSolver_mpi="ElmerSolver_mpi", mpiexec="mpiexec")


def _recipe(source: Path, *, released: bool = True) -> ImageRecipe:
    return ImageRecipe(
        name="tool",
        version="0.1.0",
        digest=recipe_digest(DOCKERFILE) if released else "0" * 64,
        dockerfile=DOCKERFILE,
        source=source,
        probe=("tool", "--version"),
        probe_label="tool",
    )


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A healthy machine: gmsh and the CAD kernel import, docker is on PATH, a native Elmer exists, and every disk has room."""
    _probes(monkeypatch, gmsh=GMSH_OK, cad=CAD_OK)
    monkeypatch.setattr(doctor.shutil, "which", _on_path)
    monkeypatch.setattr(doctor, "locate_elmer_tools", lambda: TOOLS)
    monkeypatch.setattr(doctor.shutil, "disk_usage", _roomy)

    return monkeypatch


def _probes(monkeypatch: pytest.MonkeyPatch, *, gmsh: subprocess.CompletedProcess[str] | None, cad: subprocess.CompletedProcess[str] | None) -> None:
    """Answers the gmsh and CAD kernel probes; None runs a probe past its timeout."""

    def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        answer = gmsh if "--probe" in args else cad
        if answer is None:
            raise subprocess.TimeoutExpired(args, doctor.PROBE_TIMEOUT_S)

        return answer

    monkeypatch.setattr(doctor.subprocess, "run", run)


def _check(report: DoctorReport, name: CheckName) -> Check:
    return next(check for check in report.checks if check.name is name)


def _no_native_elmer() -> ElmerToolsLocations:
    raise FileNotFoundError("Could not locate ElmerGrid executable, please ensure it is installed and in PATH.")


def test_a_healthy_machine_passes_every_check(machine: pytest.MonkeyPatch, tmp_path: Path) -> None:
    docker = _FakeDocker(root=tmp_path, label=recipe_digest(DOCKERFILE))

    report = diagnose(tmp_path / "runs", tmp_path / "store", docker=docker, recipe=_recipe(tmp_path))

    assert [check.name for check in report.checks] == list(CheckName)
    assert {check.status for check in report.checks} == {CheckStatus.OK}
    assert (report.blockers, report.warnings) == (0, 0)
    assert _check(report, CheckName.GMSH).detail == "gmsh 4.15.2"
    assert _check(report, CheckName.ELMER_IMAGE).detail == "ghcr.io/femforge-io/tool:0.1.0 with tool 9.0-1"
    assert _check(report, CheckName.DOCKER).detail == "Docker 29.8.2 on Ubuntu 24.04.3 LTS, x86_64, rootless"
    assert _check(report, CheckName.NATIVE_ELMER).detail == "ElmerSolver at ElmerSolver, with MPI"
    assert _check(report, CheckName.SOLVE_PATH).detail == "Elmer runs natively and in Docker."


@pytest.mark.parametrize(
    ("platform", "stderr", "fix"),
    [
        ("linux", "OSError: libGLU.so.1: cannot open shared object file: No such file or directory", f"apt-get install -y --no-install-recommends {' '.join(GMSH_APT_LIBRARIES)}"),
        ("linux", "ModuleNotFoundError: No module named 'gmsh'", "pip install --force-reinstall gmsh"),
        ("win32", "OSError: [WinError 126] The specified module could not be found", "pip install --force-reinstall gmsh"),
    ],
)
def test_a_broken_gmsh_fails_with_the_fix_for_its_error(machine: pytest.MonkeyPatch, tmp_path: Path, platform: str, stderr: str, fix: str) -> None:
    _probes(machine, gmsh=_done(1, stderr=f"Traceback (most recent call last):\n{stderr}\n"), cad=CAD_OK)
    machine.setattr(sys, "platform", platform)

    gmsh = _check(diagnose(tmp_path, docker=_FakeDocker(root=tmp_path)), CheckName.GMSH)

    assert gmsh.status is CheckStatus.FAIL
    assert gmsh.detail == stderr
    assert gmsh.fix is not None and fix in gmsh.fix


def test_a_gmsh_probe_that_hangs_or_prints_nonsense_fails(machine: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _probes(machine, gmsh=None, cad=CAD_OK)
    hung = _check(diagnose(tmp_path, docker=_FakeDocker(root=tmp_path)), CheckName.GMSH)

    _probes(machine, gmsh=_done(stdout="not json\n"), cad=CAD_OK)
    garbled = _check(diagnose(tmp_path, docker=_FakeDocker(root=tmp_path)), CheckName.GMSH)

    assert hung.status is CheckStatus.FAIL and "did not answer within 60.0 s" in hung.detail
    assert garbled.status is CheckStatus.FAIL and "'not json'" in garbled.detail


def test_a_broken_cad_kernel_fails(machine: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _probes(machine, gmsh=GMSH_OK, cad=_done(1, stderr="ImportError: DLL load failed while importing TKernel\n"))
    broken = _check(diagnose(tmp_path, docker=_FakeDocker(root=tmp_path)), CheckName.CAD_KERNEL)

    _probes(machine, gmsh=GMSH_OK, cad=None)
    hung = _check(diagnose(tmp_path, docker=_FakeDocker(root=tmp_path)), CheckName.CAD_KERNEL)

    assert broken.status is CheckStatus.FAIL and broken.detail == "ImportError: DLL load failed while importing TKernel"
    assert broken.fix is not None and "build123d" in broken.fix
    assert hung.status is CheckStatus.FAIL and "did not answer" in hung.detail


def test_without_docker_the_image_and_docker_disk_are_skipped(machine: pytest.MonkeyPatch, tmp_path: Path) -> None:
    machine.setattr(doctor.shutil, "which", _not_on_path)

    report = diagnose(tmp_path, docker=_FakeDocker(root=tmp_path))

    docker = _check(report, CheckName.DOCKER)
    assert docker.status is CheckStatus.WARN and docker.fix is not None and doctor.DOCKER_INSTALL in docker.fix
    assert _check(report, CheckName.ELMER_IMAGE).status is CheckStatus.SKIP
    assert _check(report, CheckName.DOCKER_DISK).status is CheckStatus.SKIP
    assert _check(report, CheckName.SOLVE_PATH).detail == "Elmer runs natively."


@pytest.mark.parametrize(
    ("error", "fix"),
    [
        ("permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock", "sudo usermod -aG docker $USER"),
        ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?", "sudo systemctl start docker"),
    ],
)
def test_an_unreachable_daemon_warns_with_its_fix(machine: pytest.MonkeyPatch, tmp_path: Path, error: str, fix: str) -> None:
    docker = _check(diagnose(tmp_path, docker=_FakeDocker(root=tmp_path, error=error)), CheckName.DOCKER)

    assert docker.status is CheckStatus.WARN
    assert docker.detail == error
    assert docker.fix is not None and fix in docker.fix


def test_an_arm_daemon_warns_that_the_image_is_emulated(machine: pytest.MonkeyPatch, tmp_path: Path) -> None:
    docker = _check(diagnose(tmp_path, docker=_FakeDocker(root=tmp_path, architecture="aarch64")), CheckName.DOCKER)

    assert docker.status is CheckStatus.WARN
    assert "runs under emulation" in docker.detail


@pytest.mark.parametrize(
    ("label", "registry", "detail", "fix"),
    [
        (None, True, "is not here; the registry has it.", "femforge image pull"),
        ("stale", True, "was built from another recipe; the registry has it.", "femforge image pull"),
        (None, False, "the registry does not have it, so it is built on first use.", "femforge image build"),
        (None, None, "the registry denied anonymous reads, so the package is private or not yet published", "femforge image build"),
    ],
)
def test_a_released_image_that_is_not_here_names_how_to_get_it(machine: pytest.MonkeyPatch, tmp_path: Path, label: str | None, registry: bool | None, detail: str, fix: str) -> None:
    docker = _FakeDocker(root=tmp_path, label=label, registry=registry)

    image = _check(diagnose(tmp_path, docker=docker, recipe=_recipe(tmp_path)), CheckName.ELMER_IMAGE)

    assert image.status is CheckStatus.WARN
    assert detail in image.detail
    assert image.fix is not None and fix in image.fix


def test_an_unreleased_recipe_is_built_without_asking_the_registry(machine: pytest.MonkeyPatch, tmp_path: Path) -> None:
    image = _check(diagnose(tmp_path, docker=_FakeDocker(root=tmp_path, registry=True), recipe=_recipe(tmp_path, released=False)), CheckName.ELMER_IMAGE)

    assert image.status is CheckStatus.WARN
    assert "this recipe is unreleased" in image.detail
    assert image.fix is not None and "femforge image build" in image.fix


def test_an_image_version_that_does_not_answer_still_passes(machine: pytest.MonkeyPatch, tmp_path: Path) -> None:
    docker = _FakeDocker(root=tmp_path, label=recipe_digest(DOCKERFILE), probe_fails=True)

    image = _check(diagnose(tmp_path, docker=docker, recipe=_recipe(tmp_path)), CheckName.ELMER_IMAGE)

    assert image.status is CheckStatus.OK
    assert image.detail.endswith("its tool version did not answer")


def test_an_image_without_a_probe_names_only_its_tag(machine: pytest.MonkeyPatch, tmp_path: Path) -> None:
    docker = _FakeDocker(root=tmp_path, label=recipe_digest(DOCKERFILE))
    recipe = ImageRecipe(name="tool", version="0.1.0", digest=recipe_digest(DOCKERFILE), dockerfile=DOCKERFILE, source=tmp_path)

    image = _check(diagnose(tmp_path, docker=docker, recipe=recipe), CheckName.ELMER_IMAGE)

    assert image.detail == "ghcr.io/femforge-io/tool:0.1.0"
    assert docker.commands == []


def test_missing_native_elmer_warns_beside_docker_and_fails_without_it(machine: pytest.MonkeyPatch, tmp_path: Path) -> None:
    machine.setattr(doctor, "locate_elmer_tools", _no_native_elmer)
    beside_docker = diagnose(tmp_path, docker=_FakeDocker(root=tmp_path))

    machine.setattr(doctor.shutil, "which", _not_on_path)
    alone = diagnose(tmp_path, docker=_FakeDocker(root=tmp_path))

    native = _check(beside_docker, CheckName.NATIVE_ELMER)
    assert native.status is CheckStatus.WARN and native.fix is None
    assert _check(beside_docker, CheckName.SOLVE_PATH).detail == "Elmer runs in Docker."
    assert _check(alone, CheckName.NATIVE_ELMER).status is CheckStatus.FAIL
    assert _check(alone, CheckName.SOLVE_PATH).status is CheckStatus.FAIL
    assert alone.blockers == 2


def test_native_elmer_without_mpi_says_so(machine: pytest.MonkeyPatch, tmp_path: Path) -> None:
    machine.setattr(doctor, "locate_elmer_tools", lambda: ElmerToolsLocations(ElmerGrid="ElmerGrid", ElmerSolver="ElmerSolver"))

    native = _check(diagnose(tmp_path, docker=_FakeDocker(root=tmp_path)), CheckName.NATIVE_ELMER)

    assert native.detail.endswith("without MPI")


@pytest.mark.parametrize(("free", "status"), [(10**12, CheckStatus.OK), (4 * 10**9, CheckStatus.WARN), (10**9, CheckStatus.FAIL)])
def test_disk_checks_warn_below_5_gb_and_fail_below_2_gb(machine: pytest.MonkeyPatch, tmp_path: Path, free: int, status: CheckStatus) -> None:
    seen: list[Path] = []

    def disk_usage(path: Path) -> _Usage:
        seen.append(path)

        return _Usage(10**13, 0, free)

    machine.setattr(doctor.shutil, "disk_usage", disk_usage)

    report = diagnose(tmp_path / "not" / "yet", tmp_path / "store", docker=_FakeDocker(root=tmp_path))

    disks = [_check(report, name) for name in (CheckName.RUNS_DISK, CheckName.ARTIFACTS_DISK, CheckName.DOCKER_DISK)]
    assert {disk.status for disk in disks} == {status}
    assert seen[0] == tmp_path.resolve()
    assert all((disk.fix is None) == (status is CheckStatus.OK) for disk in disks)


def test_an_unset_store_and_an_unreachable_docker_root_are_skipped(machine: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report = diagnose(tmp_path, docker=_FakeDocker(root=tmp_path / "inside-the-vm"))

    assert _check(report, CheckName.ARTIFACTS_DISK).status is CheckStatus.SKIP
    docker_disk = _check(report, CheckName.DOCKER_DISK)
    assert docker_disk.status is CheckStatus.SKIP and "out of reach" in docker_disk.detail


def test_the_table_shows_every_status_numbers_the_fixes_and_counts(machine: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    machine.setattr(doctor, "DockerCli", lambda: _FakeDocker(root=tmp_path, label=None, registry=False))
    machine.setattr(doctor, "locate_elmer_tools", _no_native_elmer)
    machine.setattr(doctor.shutil, "disk_usage", _cramped)

    code = cli.main(["doctor", "--runs-dir", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 1
    assert all(status.value.upper() in out for status in CheckStatus)
    assert "1. Elmer image: Build it now with `femforge image build`." in out
    assert "2. Runs disk:" in out
    assert out.rstrip().endswith("2 blockers, 2 warnings")


def test_json_dumps_the_report_with_its_counts(machine: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    machine.setattr(doctor, "DockerCli", lambda: _FakeDocker(root=tmp_path, label=None, registry=True))
    machine.setenv(cli.ROOT_ENV, str(tmp_path / "store"))

    code = cli.main(["doctor", "--json", "--runs-dir", str(tmp_path)])

    dumped = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    report = DoctorReport.model_validate(dumped)
    assert code == 0
    assert (dumped["blockers"], dumped["warnings"]) == (0, 1)
    assert _check(report, CheckName.ARTIFACTS_DISK).detail.endswith(str((tmp_path / "store").resolve()))


def test_the_exported_schema_names_every_key_json_prints() -> None:
    report = DoctorReport(checks=[Check(name=CheckName.PYTHON, status=CheckStatus.OK, detail="3.12.7")])

    printed = cast("dict[str, object]", json.loads(report.model_dump_json()))
    properties = cast("dict[str, object]", schemas.model_schema("DoctorReport")["properties"])

    assert set(printed) == set(properties)


def test_a_single_blocker_and_warning_are_counted_in_the_singular(capsys: pytest.CaptureFixture[str]) -> None:
    report = DoctorReport(
        checks=[
            Check(name=CheckName.PYTHON, status=CheckStatus.FAIL, detail="x"),
            Check(name=CheckName.GMSH, status=CheckStatus.WARN, detail="y"),
        ]
    )

    doctor.render(report, cli.console())

    assert capsys.readouterr().out.rstrip().endswith("1 blocker, 1 warning")


def test_the_ci_runner_installs_the_libraries_the_gmsh_fix_names() -> None:
    install = next(line for line in QUALITY.read_text(encoding="utf-8").splitlines() if "apt-get install" in line)

    assert all(library in install.split() for library in GMSH_APT_LIBRARIES)
