"""``femforge doctor``: check that this machine can mesh and solve, and name the fix for whatever it cannot."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, computed_field
from rich.console import Console
from rich.table import Table
from rich.text import Text

from femforge.elmer.runtime.docker_image import ELMER_IMAGE
from femforge.elmer.runtime.tools import ElmerToolsLocations, locate_elmer_tools
from femforge.execution.docker.cli import DockerCli, DockerError, DockerInfo
from femforge.execution.docker.image import RECIPE_LABEL, DockerImage, ImageRecipe, recipe_digest
from femforge.meshing.backend import WORKER_MODULE, WorkerProbe


__all__ = ["COMMAND", "Check", "CheckName", "CheckStatus", "DoctorReport", "add_arguments", "diagnose", "render", "run"]

COMMAND = "doctor"
"""The ``femforge`` subcommand."""

PROBE_TIMEOUT_S = 60.0
"""`t` `[s]`: How long a gmsh, CAD kernel or image probe may take."""

DOCKER_TIMEOUT_S = 10.0
"""`t` `[s]`: How long ``docker version``, ``docker info`` and a registry lookup may take."""

DISK_WARN_BYTES = 5 * 10**9
"""`b` `[B]`: Free space below which a disk check warns."""

DISK_FAIL_BYTES = 2 * 10**9
"""`b` `[B]`: Free space below which a disk check fails."""

GMSH_APT_LIBRARIES = ("libgl1", "libglu1-mesa", "libxrender1", "libxcursor1", "libxft2", "libxinerama1")
"""The system libraries gmsh's wheel loads on Linux, which the CI runner installs too."""

MISSING_LIBRARY = "cannot open shared object file"
"""How the Linux loader reports a missing system library."""

ARM_ARCHITECTURES = ("aarch64", "arm64")
"""How ``docker info`` names an ARM daemon, on which the amd64 Elmer image runs under emulation."""

DOCKER_INSTALL = "https://docs.docker.com/get-started/get-docker/"
"""Where to get Docker."""

ELMER_INSTALL = "https://www.elmerfem.org/blog/binaries/"
"""Where to get a native Elmer."""

CAD_PROBE = """\
import importlib.metadata as metadata

import build123d
import OCP

kernel = metadata.packages_distributions().get("OCP", ["OCP"])[0]
print(f"build123d {metadata.version('build123d')} on {kernel} {metadata.version(kernel)}")
"""
"""Imports the CAD kernel in a child and prints its versions, so a broken install cannot take the doctor down."""


class CheckName(StrEnum):
    """The checks, in the order the doctor runs them."""

    PYTHON = "Python"
    GMSH = "gmsh"
    CAD_KERNEL = "CAD kernel"
    DOCKER = "Docker"
    ELMER_IMAGE = "Elmer image"
    NATIVE_ELMER = "Native Elmer"
    RUNS_DISK = "Runs disk"
    ARTIFACTS_DISK = "Artifacts disk"
    DOCKER_DISK = "Docker disk"
    SOLVE_PATH = "Solve path"


class CheckStatus(StrEnum):
    """How a check went."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"

    @property
    def style(self) -> str:
        """The Rich style the status shows in."""
        match self:
            case CheckStatus.OK:
                return "green"
            case CheckStatus.WARN:
                return "yellow"
            case CheckStatus.FAIL:
                return "bold red"
            case CheckStatus.SKIP:
                return "dim"


class Check(BaseModel):
    """One doctor check: what it looked at, how it went, and how to fix it."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    name: CheckName
    """What was checked."""

    status: CheckStatus
    """How the check went."""

    detail: str
    """What the check found."""

    fix: str | None = None
    """What to do about it, when there is something to do."""


class DoctorReport(BaseModel):
    """Every doctor check in order, with the count of blockers and warnings."""

    # The serialization schema is what `--json` prints, computed counts included.
    model_config = ConfigDict(use_attribute_docstrings=True, json_schema_mode_override="serialization")

    checks: list[Check]
    """The checks, in the order they ran."""

    @computed_field
    @property
    def blockers(self) -> int:
        """How many checks failed."""
        return sum(check.status is CheckStatus.FAIL for check in self.checks)

    @computed_field
    @property
    def warnings(self) -> int:
        """How many checks warned."""
        return sum(check.status is CheckStatus.WARN for check in self.checks)


def _probe(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """The finished child ``args``, or None when it ran past [`PROBE_TIMEOUT_S`][]."""
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None


def _last_line(text: str) -> str:
    """The last non-blank line of ``text``, where a traceback names its error."""
    lines: list[str] = [line.strip() for line in text.splitlines() if line.strip()]

    return lines[-1] if lines else "no output"


def _check_python() -> Check:
    return Check(name=CheckName.PYTHON, status=CheckStatus.OK, detail=f"{platform.python_implementation()} {platform.python_version()} at {sys.executable}")


def _check_gmsh() -> Check:
    done = _probe([sys.executable, "-m", WORKER_MODULE, "--probe"])
    if done is None:
        return Check(name=CheckName.GMSH, status=CheckStatus.FAIL, detail=f"The gmsh probe did not answer within {PROBE_TIMEOUT_S} s.")

    if done.returncode == 0:
        try:
            probe = WorkerProbe.model_validate_json(done.stdout)
        except ValueError:
            return Check(name=CheckName.GMSH, status=CheckStatus.FAIL, detail=f"The gmsh probe printed {_last_line(done.stdout)!r}.")

        return Check(name=CheckName.GMSH, status=CheckStatus.OK, detail=f"gmsh {probe.gmsh}")

    detail: str = _last_line(done.stderr or done.stdout)
    fix: str = "Reinstall gmsh with `pip install --force-reinstall gmsh`."
    if sys.platform == "linux" and MISSING_LIBRARY in detail:
        fix = f"Install the libraries gmsh loads: `sudo apt-get install -y --no-install-recommends {' '.join(GMSH_APT_LIBRARIES)}`."

    return Check(name=CheckName.GMSH, status=CheckStatus.FAIL, detail=detail, fix=fix)


def _check_cad_kernel() -> Check:
    done = _probe([sys.executable, "-c", CAD_PROBE])
    if done is not None and done.returncode == 0:
        return Check(name=CheckName.CAD_KERNEL, status=CheckStatus.OK, detail=done.stdout.strip())

    detail: str = f"The CAD kernel probe did not answer within {PROBE_TIMEOUT_S} s." if done is None else _last_line(done.stderr or done.stdout)

    return Check(
        name=CheckName.CAD_KERNEL,
        status=CheckStatus.FAIL,
        detail=detail,
        fix="Reinstall build123d and its OCP kernel with `pip install --force-reinstall build123d`.",
    )


def _check_docker(docker: DockerCli) -> tuple[Check, DockerInfo | None]:
    if shutil.which("docker") is None:
        return Check(name=CheckName.DOCKER, status=CheckStatus.WARN, detail="docker is not on PATH.", fix=f"Install Docker from {DOCKER_INSTALL}."), None

    try:
        _ = docker.version(timeout_s=DOCKER_TIMEOUT_S)
        info: DockerInfo = docker.info(timeout_s=DOCKER_TIMEOUT_S)
    except DockerError as error:
        message: str = str(error)
        fix: str = "Start Docker: open Docker Desktop, or run `sudo systemctl start docker` on Linux."
        if "permission denied" in message.lower():
            fix = "Let your user reach the Docker socket: `sudo usermod -aG docker $USER`, then log out and back in."

        return Check(name=CheckName.DOCKER, status=CheckStatus.WARN, detail=_last_line(message), fix=fix), None

    detail: str = f"Docker {info.server_version} on {info.operating_system}, {info.architecture}{', rootless' if info.rootless else ''}"
    if info.architecture in ARM_ARCHITECTURES:
        emulated = Check(
            name=CheckName.DOCKER,
            status=CheckStatus.WARN,
            detail=f"{detail}; the amd64 Elmer image runs under emulation here.",
            fix="Expect slower solves; Docker Desktop's Rosetta emulation setting speeds them up.",
        )

        return emulated, info

    return Check(name=CheckName.DOCKER, status=CheckStatus.OK, detail=detail), info


def _check_elmer_image(docker: DockerCli, recipe: ImageRecipe) -> Check:
    image: DockerImage = recipe.image()

    def installed() -> str:
        if not recipe.probe:
            return image.tag

        try:
            version: str = docker.output(["run", "--rm", *image.platform_args, image.tag, *recipe.probe], timeout_s=PROBE_TIMEOUT_S)
        except DockerError:
            return f"{image.tag}; its {recipe.probe_label} version did not answer"

        return f"{image.tag} with {recipe.probe_label} {version}"

    label: str | None = docker.image_label(image.tag, RECIPE_LABEL)
    if label == recipe_digest(recipe.dockerfile):
        return Check(name=CheckName.ELMER_IMAGE, status=CheckStatus.OK, detail=installed())

    state: str = f"{image.tag} was built from another recipe" if label is not None else f"{image.tag} is not here"
    build_fix = "Build it now with `femforge image build`."
    if not image.published:
        return Check(name=CheckName.ELMER_IMAGE, status=CheckStatus.WARN, detail=f"{state}; this recipe is unreleased, so it is built on first use.", fix=build_fix)

    held: bool | None = docker.registry_has(image.tag, timeout_s=DOCKER_TIMEOUT_S)
    if held:
        return Check(name=CheckName.ELMER_IMAGE, status=CheckStatus.WARN, detail=f"{state}; the registry has it.", fix="Pull it now with `femforge image pull`.")

    reason: str = "the registry does not have it" if held is False else "the registry denied anonymous reads, so the package is private or not yet published"

    return Check(name=CheckName.ELMER_IMAGE, status=CheckStatus.WARN, detail=f"{state}; {reason}, so it is built on first use.", fix=build_fix)


def _check_native_elmer(docker_usable: bool) -> tuple[Check, ElmerToolsLocations | None]:
    try:
        tools: ElmerToolsLocations = locate_elmer_tools()
    except FileNotFoundError as error:
        if docker_usable:
            return Check(name=CheckName.NATIVE_ELMER, status=CheckStatus.WARN, detail="No native Elmer; Docker runs Elmer instead."), None

        return Check(name=CheckName.NATIVE_ELMER, status=CheckStatus.FAIL, detail=str(error), fix=f"Install Elmer from {ELMER_INSTALL}, or Docker."), None

    parallel: str = "with MPI" if tools.can_run_parallel else "without MPI"

    return Check(name=CheckName.NATIVE_ELMER, status=CheckStatus.OK, detail=f"ElmerSolver at {tools.ElmerSolver}, {parallel}"), tools


def _check_disk(name: CheckName, path: Path | None, fix: str) -> Check:
    if path is None:
        return Check(name=name, status=CheckStatus.SKIP, detail="Not set.")

    existing: Path = next(folder for folder in (path, *path.parents) if folder.exists())
    free: int = shutil.disk_usage(existing).free
    detail: str = f"{free / 10**9:.1f} GB free at {path}"
    if free < DISK_FAIL_BYTES:
        return Check(name=name, status=CheckStatus.FAIL, detail=detail, fix=fix)

    if free < DISK_WARN_BYTES:
        return Check(name=name, status=CheckStatus.WARN, detail=detail, fix=fix)

    return Check(name=name, status=CheckStatus.OK, detail=detail)


def _check_docker_disk(info: DockerInfo | None) -> Check:
    if info is None:
        return Check(name=CheckName.DOCKER_DISK, status=CheckStatus.SKIP, detail="Needs Docker.")

    root = Path(info.root_dir)
    if not root.exists():
        return Check(name=CheckName.DOCKER_DISK, status=CheckStatus.SKIP, detail=f"{info.root_dir} is on the daemon's own disk, out of reach from here.")

    return _check_disk(CheckName.DOCKER_DISK, root, "Free Docker's space with `docker system prune`.")


def _check_solve_path(native: bool, docker_usable: bool) -> Check:
    if native or docker_usable:
        where: str = " and ".join(place for place, works in (("natively", native), ("in Docker", docker_usable)) if works)

        return Check(name=CheckName.SOLVE_PATH, status=CheckStatus.OK, detail=f"Elmer runs {where}.")

    return Check(
        name=CheckName.SOLVE_PATH,
        status=CheckStatus.FAIL,
        detail="Neither a native Elmer nor Docker works here.",
        fix=f"Install Docker from {DOCKER_INSTALL}, or Elmer from {ELMER_INSTALL}.",
    )


def diagnose(runs_dir: Path, artifacts: Path | None = None, *, docker: DockerCli | None = None, recipe: ImageRecipe = ELMER_IMAGE) -> DoctorReport:
    """Run every check against this machine.

    Args:
        runs_dir: The runs directory whose disk is checked.
        artifacts: The artifact store whose disk is checked; None skips that check.
        docker: The Docker client to probe through; None makes one.
        recipe: The image recipe whose image is checked.
    """
    client: DockerCli = docker if docker is not None else DockerCli()
    docker_check, info = _check_docker(client)
    docker_usable: bool = info is not None
    image_check: Check = _check_elmer_image(client, recipe) if docker_usable else Check(name=CheckName.ELMER_IMAGE, status=CheckStatus.SKIP, detail="Needs Docker.")
    native_check, tools = _check_native_elmer(docker_usable)

    return DoctorReport(
        checks=[
            _check_python(),
            _check_gmsh(),
            _check_cad_kernel(),
            docker_check,
            image_check,
            native_check,
            _check_disk(CheckName.RUNS_DISK, runs_dir.resolve(), "Free space, for example with `femforge purge`."),
            _check_disk(CheckName.ARTIFACTS_DISK, artifacts.resolve() if artifacts is not None else None, "Free space, for example with `femforge purge`."),
            _check_docker_disk(info),
            _check_solve_path(tools is not None, docker_usable),
        ]
    )


def render(report: DoctorReport, console: Console) -> None:
    """Print the checks as a table, then the numbered fixes and the count of blockers and warnings."""
    table = Table("Check", "Status", "Detail")
    for check in report.checks:
        table.add_row(check.name.value, Text(check.status.value.upper(), style=check.status.style), Text(check.detail))

    console.print(table)

    fixes: list[Check] = [check for check in report.checks if check.fix is not None]
    for number, check in enumerate(fixes, start=1):
        console.print(Text(f"{number}. {check.name.value}: {check.fix}"))

    blockers: str = f"{report.blockers} blocker{'' if report.blockers == 1 else 's'}"
    warnings: str = f"{report.warnings} warning{'' if report.warnings == 1 else 's'}"
    console.print(f"{blockers}, {warnings}")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The ``femforge doctor`` options."""
    _ = parser.add_argument("--json", action="store_true", help="Print the report as JSON instead of a table.")
    _ = parser.add_argument("--runs-dir", default="runs", help="The runs directory whose disk is checked (default: runs).")


def run(args: argparse.Namespace, console: Console, artifacts: str | None) -> int:
    """Run the checks and report them; the exit code is 1 when any check failed.

    Args:
        args: The parsed ``femforge doctor`` options.
        console: Where the table goes.
        artifacts: The artifact store root from the environment, or None.
    """
    report: DoctorReport = diagnose(Path(cast("str", args.runs_dir)), Path(artifacts) if artifacts else None)
    if cast("bool", args.json):
        print(report.model_dump_json(indent=2))
    else:
        render(report, console)

    return 1 if report.blockers else 0
