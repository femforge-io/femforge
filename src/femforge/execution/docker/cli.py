"""The ``docker`` command-line client: every Docker call femforge makes, through one executable located on first use."""

from __future__ import annotations

import contextvars
import functools
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import IO

from pydantic import BaseModel, ConfigDict, Field

from femforge.common.log import log


__all__ = ["CASE_DIR", "CONTAINER_PREFIX", "DockerCli", "DockerComponent", "DockerError", "DockerInfo", "DockerVersion", "container_name"]

ERROR_TAIL_LINES = 40
"""How many trailing output lines a failed build or pull carries in its error."""

CASE_DIR = "/case"
"""Where a run binds its host directory inside the container."""

CONTAINER_PREFIX = "femforge_"
"""The name prefix of every container femforge runs, which monitors filter on."""

ROOTLESS_OPTION = "name=rootless"
"""The ``docker info`` security option a rootless daemon reports."""

PULL_TIMEOUT_S = 1800.0
"""`t` `[s]`: How long a pull may run before it is stopped."""

PULL_HEARTBEAT_S = 15.0
"""`t` `[s]`: How often a pull logs that it is still running."""

LAYER_PULLED = "Pull complete"
"""How ``docker pull`` ends the line of a finished layer."""

MANIFEST_MISSING = ("no such manifest", "manifest unknown", "not found")
"""What ``docker manifest inspect`` says about a tag the registry does not hold."""


def container_name() -> str:
    """A unique container name, so a timeout can ``docker kill`` it: killing the client alone leaves the container running."""
    return f"{CONTAINER_PREFIX}{uuid.uuid4().hex}"


class DockerError(RuntimeError):
    """A docker command failed; carries its exit code and the tail of its output."""

    def __init__(self, message: str, *, returncode: int, output_tail: str = "") -> None:
        """Build the error from ``message`` with the output tail appended.

        Args:
            message: What failed.
            returncode: The docker command's exit code; -1 when docker is not on PATH.
            output_tail: The end of the command's output, appended to the message when not empty.
        """
        super().__init__(f"{message}\n{output_tail}" if output_tail else message)
        self.returncode: int = returncode
        self.output_tail: str = output_tail


class DockerComponent(BaseModel):
    """One side of ``docker version``: the client or the daemon."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    version: str = Field(alias="Version")
    """The release, such as ``29.8.2``."""

    api_version: str = Field(alias="ApiVersion")
    """The Docker API version it speaks."""

    os: str = Field(alias="Os")
    """The operating system it runs on."""

    arch: str = Field(alias="Arch")
    """The CPU architecture it runs on."""


class DockerVersion(BaseModel):
    """What ``docker version`` reports: the client, and the daemon when it answers."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    client: DockerComponent = Field(alias="Client")
    """The ``docker`` command-line client."""

    server: DockerComponent | None = Field(default=None, alias="Server")
    """The daemon, or None when it did not answer."""


class DockerInfo(BaseModel):
    """What ``docker info`` reports about the daemon."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    server_version: str = Field(alias="ServerVersion")
    """The daemon release."""

    operating_system: str = Field(alias="OperatingSystem")
    """The daemon's host system, such as ``Docker Desktop``."""

    architecture: str = Field(alias="Architecture")
    """The daemon's CPU architecture, such as ``x86_64``."""

    root_dir: str = Field(alias="DockerRootDir")
    """Where the daemon keeps images and containers, a path on the daemon's host."""

    security_options: list[str] | None = Field(default=None, alias="SecurityOptions")
    """The daemon's security options, such as ``name=rootless``."""

    @property
    def rootless(self) -> bool:
        """Whether the daemon runs without root, so files it writes into a mount belong to the user."""
        return any(ROOTLESS_OPTION in option for option in self.security_options or [])


class DockerCli:
    """The ``docker`` executable, located on first use and then reused for every call."""

    def __init__(self) -> None:
        """Start without an executable; the first call finds ``docker`` on PATH."""
        self._exe: str | None = None

    def exe(self) -> str:
        """The docker client executable."""
        if self._exe is None:
            found: str | None = shutil.which("docker")
            if found is None:
                raise DockerError("The Docker backend needs `docker` on PATH.", returncode=-1)

            self._exe = found

        return self._exe

    @functools.cached_property
    def host_user(self) -> str | None:
        """The ``uid:gid`` a container runs as on a Linux host with a rootful daemon, so mount output stays the user's; None elsewhere."""
        if sys.platform != "linux":
            return None

        if self.info().rootless:
            return None

        return f"{os.getuid()}:{os.getgid()}"

    def version(self, *, timeout_s: float | None = None) -> DockerVersion:
        """The client and daemon versions, raising [`DockerError`][] when the daemon does not answer, within ``timeout_s`` seconds when given."""
        return DockerVersion.model_validate_json(self.output(["version", "--format", "{{json .}}"], timeout_s=timeout_s))

    def info(self, *, timeout_s: float | None = None) -> DockerInfo:
        """What the daemon reports about itself, raising [`DockerError`][] when it does not answer, within ``timeout_s`` seconds when given."""
        return DockerInfo.model_validate_json(self.output(["info", "--format", "{{json .}}"], timeout_s=timeout_s))

    def has_image(self, tag: str) -> bool:
        """Whether Docker holds the image ``tag`` locally."""
        done: subprocess.CompletedProcess[bytes] = subprocess.run(
            [self.exe(), "image", "inspect", tag],
            capture_output=True,
            check=False,
        )

        return done.returncode == 0

    def registry_has(self, tag: str, *, timeout_s: float | None = None) -> bool | None:
        """Whether a registry holds ``tag``: False when it says so, None when it cannot tell, as for a private package that denies anonymous reads."""
        try:
            done: subprocess.CompletedProcess[str] = subprocess.run(
                [self.exe(), "manifest", "inspect", tag],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return None

        if done.returncode == 0:
            return True

        message: str = done.stderr.lower()
        if any(missing in message for missing in MANIFEST_MISSING):
            return False

        return None

    def pull(self, tag: str, *, platform: str | None = None, timeout_s: float = PULL_TIMEOUT_S) -> None:
        """Pull ``tag``, logging each finished layer and a heartbeat, and raise [`DockerError`][] with the output tail on failure or timeout.

        Args:
            tag: The image to pull.
            platform: The platform to pull, such as ``linux/amd64``; None lets Docker choose.
            timeout_s: Seconds before the pull is stopped.
        """
        options: list[str] = ["--platform", platform] if platform is not None else []
        tail: deque[str] = deque(maxlen=ERROR_TAIL_LINES)
        proc: subprocess.Popen[str] = subprocess.Popen(
            [self.exe(), "pull", *options, tag],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        def read() -> None:
            stdout: IO[str] | None = proc.stdout
            assert stdout is not None  # stdout=PIPE always yields a stream
            for line in stdout:
                text: str = line.rstrip()
                tail.append(text)
                if text.endswith(LAYER_PULLED):
                    log.bind(tag=tag, layer=text.split(":", 1)[0]).info("Pulled an image layer.")

        reader = threading.Thread(target=contextvars.copy_context().run, args=(read,), daemon=True)
        reader.start()

        started: float = time.monotonic()
        returncode: int | None = None
        while returncode is None:
            remaining: float = timeout_s - (time.monotonic() - started)
            try:
                returncode = proc.wait(timeout=max(0.0, min(PULL_HEARTBEAT_S, remaining)))
            except subprocess.TimeoutExpired:
                if time.monotonic() - started < timeout_s:
                    log.bind(tag=tag, elapsed=round(time.monotonic() - started)).info("Still pulling the image.")
                    continue

                proc.kill()
                _ = proc.wait()
                reader.join()
                raise DockerError(f"docker pull of {tag} ran past {timeout_s} s", returncode=-1, output_tail="\n".join(tail)) from None

        reader.join()
        if returncode != 0:
            raise DockerError(
                f"docker pull of {tag} exited with code {returncode}",
                returncode=returncode,
                output_tail="\n".join(tail),
            )

    def run_args(
        self,
        image: str,
        *,
        mount: Path,
        workdir: str,
        container: str,
        args: Sequence[str],
        user: str | None = None,
        platform: str | None = None,
    ) -> list[str]:
        """A ``docker run`` command that runs ``args`` in ``image`` with ``mount`` bound at [`CASE_DIR`][].

        Args:
            image: The image tag.
            mount: The host directory to bind; a relative path resolves against the working directory.
            workdir: The working directory inside the container.
            container: The container name a kill addresses.
            args: The command to run inside the container.
            user: The ``uid:gid`` to run as; None keeps the image's user.
            platform: The image platform, such as ``linux/amd64``; None lets Docker choose.
        """
        options: list[str] = []
        if platform is not None:
            options += ["--platform", platform]

        if user is not None:
            options += ["--user", user]

        return [
            self.exe(),
            "run",
            "--rm",
            "--name",
            container,
            *options,
            "-v",
            f"{mount.resolve()}:{CASE_DIR}",
            "-w",
            workdir,
            image,
            *args,
        ]

    def kill(self, container: str) -> None:
        """Stop a running container; a container that already exited is not an error."""
        _ = subprocess.run(
            [self.exe(), "kill", container],
            capture_output=True,
            check=False,
        )

    def output(self, args: Sequence[str], *, timeout_s: float | None = None) -> str:
        """The stripped stdout of ``docker <args>``, raising [`DockerError`][] on a nonzero exit or past ``timeout_s`` seconds."""
        try:
            done: subprocess.CompletedProcess[str] = subprocess.run(
                [self.exe(), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            raise DockerError(f"docker {args[0]} did not answer within {timeout_s} s", returncode=-1) from None

        if done.returncode != 0:
            raise DockerError(
                f"docker {args[0]} exited with code {done.returncode}",
                returncode=done.returncode,
                output_tail=done.stderr.strip(),
            )

        return done.stdout.strip()

    def image_label(self, tag: str, label: str) -> str | None:
        """The value of ``label`` on the local image ``tag``, or None when Docker has no such image."""
        try:
            return self.output(["image", "inspect", "--format", f'{{{{ index .Config.Labels "{label}" }}}}', tag])
        except DockerError:
            return None

    def build(self, tag: str, dockerfile: str, *, labels: Sequence[str], platform: str | None = None) -> None:
        """Build ``dockerfile`` as ``tag``, logging its output at debug.

        Args:
            tag: The tag to build.
            dockerfile: The Dockerfile text, which must copy nothing in: the build context is empty.
            labels: ``KEY=VALUE`` labels to stamp on the image.
            platform: The platform to build for, such as ``linux/amd64``; None builds for the daemon's own.
        """
        tail: deque[str] = deque(maxlen=ERROR_TAIL_LINES)

        with tempfile.TemporaryDirectory(prefix="femforge-image-") as context:
            recipe = Path(context) / "Dockerfile"
            _ = recipe.write_bytes(dockerfile.encode("utf-8"))

            label_args: list[str] = [arg for label in labels for arg in ("--label", label)]
            platform_args: list[str] = ["--platform", platform] if platform is not None else []
            proc: subprocess.Popen[str] = subprocess.Popen(
                [self.exe(), "build", "--progress=plain", *platform_args, "--tag", tag, *label_args, "--file", str(recipe), context],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            stdout: IO[str] | None = proc.stdout
            assert stdout is not None  # stdout=PIPE always yields a stream
            for line in stdout:
                tail.append(line.rstrip())
                log.bind(tag=tag, line=line.rstrip()).debug("Docker build output.")

            returncode: int = proc.wait()

        if returncode != 0:
            raise DockerError(
                f"docker build of {tag} exited with code {returncode}",
                returncode=returncode,
                output_tail="\n".join(tail),
            )
