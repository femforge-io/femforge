"""Images femforge builds itself: apt install scripts from typed sections, version tags guarded by a recipe hash, and the build on first use."""

from __future__ import annotations

import argparse
import hashlib
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from femforge.common.cli import CliError
from femforge.common.log import log
from femforge.execution.docker.cli import DockerCli, DockerError


__all__ = ["REGISTRY", "AptPackage", "DockerImage", "ImageRecipe", "Section", "apt_install", "ensure_image", "install_run"]

REGISTRY = "ghcr.io/femforge-io"
"""The registry namespace femforge publishes its released images under."""

RECIPE_LABEL = "femforge.recipe"
"""The image label holding the sha256 of the Dockerfile the image was built from."""

SOURCE_LABEL = "org.opencontainers.image.source"
"""The OCI label naming the repository an image is built from."""

LICENSES_LABEL = "org.opencontainers.image.licenses"
"""The OCI label holding the SPDX expression of what an image contains."""

VERSION_LABEL = "org.opencontainers.image.version"
"""The OCI label holding an image's version, stamped at build time so the recipe text stays version-free."""

BANNER_WIDTH = 80
"""The width of a section banner in an install script."""

COMMAND = "image"
"""The ``femforge`` subcommand these actions sit under."""


@dataclass(frozen=True)
class AptPackage:
    """An apt package an image installs and why it needs it."""

    name: str
    reason: str
    build_only: bool = False
    """Whether the install purges the package once it is done with it."""


@dataclass(frozen=True)
class Section:
    """One titled block of an install script, listing each package it installs with its reason."""

    title: str
    commands: tuple[str, ...]
    packages: tuple[AptPackage, ...] = ()

    def render(self) -> str:
        """The block as shell lines: a dashed banner, the package reasons, then the commands."""
        label: str = f" {self.title.upper()} "
        dashes: int = BANNER_WIDTH - len("#  #") - len(label)
        banner: str = f"# {'-' * (dashes - dashes // 2)}{label}{'-' * (dashes // 2)} #"

        width: int = max((len(package.name) for package in self.packages), default=0)
        reasons: list[str] = [f"#           {package.name:<{width}}   {package.reason}" for package in self.packages]

        return "\n".join([banner, *reasons, *self.commands])


def apt_install(packages: tuple[AptPackage, ...]) -> str:
    """An ``apt-get install`` of ``packages``, one per line."""
    names: str = " \\\n".join(f"    {package.name}" for package in packages)

    return f"apt-get install -y --no-install-recommends \\\n{names}"


def install_run(sections: Sequence[Section]) -> str:
    """A Dockerfile ``RUN`` heredoc that runs ``sections`` as one strict, non-interactive shell script."""
    script: str = "\n\n".join(section.render() for section in sections)

    return f"RUN <<EOF\nset -eux\nexport DEBIAN_FRONTEND=noninteractive\n\n{script}\nEOF"


def recipe_digest(text: str) -> str:
    """The sha256 of a Dockerfile's text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DockerImage:
    """An image a tool runs in: a published or built recipe image, or with no recipe, a tag Docker has or pulls."""

    tag: str
    dockerfile: str | None = None
    probe: tuple[str, ...] = ()
    """A command run in the fresh image whose output the build logs, such as the installed tool's version."""

    published: bool = False
    """Whether a registry serves the tag, so it is pulled before it is built."""

    platform: str | None = None
    """The platform the image is pulled, built and run for, such as ``linux/amd64``; None lets Docker choose."""

    labels: tuple[str, ...] = ()
    """The ``KEY=VALUE`` labels a build stamps beside the recipe label."""

    @property
    def platform_args(self) -> list[str]:
        """The ``--platform`` option of a docker command, empty without a platform."""
        return ["--platform", self.platform] if self.platform is not None else []


@dataclass(frozen=True)
class ImageRecipe:
    """An image femforge builds itself, with the version and recipe hash it was last released at."""

    name: str
    version: str
    digest: str
    """The sha256 of the Dockerfile released as `version`."""
    dockerfile: str
    source: Path
    """The module whose ``IMAGE_VERSION`` and ``IMAGE_DIGEST`` lines a bump rewrites."""
    probe: tuple[str, ...] = ()
    registry: str = REGISTRY
    """The registry namespace a released version is published under."""
    platform: str | None = None
    """The platform the image is built for and runs on; None lets Docker choose."""
    probe_label: str = ""
    """What `probe` reports the version of, such as ``elmerfem-csc``."""

    def image(self) -> DockerImage:
        """The released image under the registry, or while the recipe differs from the released one, a local dev image that is never published."""
        digest: str = recipe_digest(self.dockerfile)
        if digest == self.digest:
            return DockerImage(
                f"{self.registry}/{self.name}:{self.version}",
                self.dockerfile,
                self.probe,
                published=True,
                platform=self.platform,
                labels=(f"{VERSION_LABEL}={self.version}",),
            )

        version: str = f"{self.version}-dev.{digest[:8]}"

        return DockerImage(
            f"{self.name}:{version}",
            self.dockerfile,
            self.probe,
            published=False,
            platform=self.platform,
            labels=(f"{VERSION_LABEL}={version}",),
        )


_BUILD_LOCK = threading.Lock()
"""Serializes pulls and builds, so concurrent solves in one process fetch an image once."""

_READY: set[str] = set()
"""The tags this process has confirmed it holds, built from their recipe when they have one."""


def ensure_image(image: DockerImage, docker: DockerCli, *, pull: bool = True) -> None:
    """Make Docker hold ``image``: reuse a matching local tag, else pull a published one, else build its recipe; checked once per tag per process.

    A tag without a recipe is pulled when Docker lacks it and never built, so a failed pull raises `DockerError`.

    Args:
        image: The image to make available.
        docker: The client to run Docker through.
        pull: Try the registry before building a published recipe.
    """

    def pulled_from_recipe(digest: str) -> bool:
        try:
            docker.pull(image.tag, platform=image.platform)
        except DockerError as error:
            log.bind(tag=image.tag, error=str(error)).warning("Could not pull the image; building it instead.")
            return False

        if docker.image_label(image.tag, RECIPE_LABEL) == digest:
            return True

        log.bind(tag=image.tag).warning("The pulled image was built from another recipe; building it instead.")
        return False

    with _BUILD_LOCK:
        if image.tag in _READY:
            return

        if image.dockerfile is None:
            if not docker.has_image(image.tag):
                docker.pull(image.tag, platform=image.platform)

            _READY.add(image.tag)
            return

        digest: str = recipe_digest(image.dockerfile)
        if docker.image_label(image.tag, RECIPE_LABEL) == digest or (pull and image.published and pulled_from_recipe(digest)):
            _READY.add(image.tag)
            return

        log.bind(tag=image.tag).info("Building a Docker image; this takes a few minutes, once.")
        docker.build(
            image.tag,
            image.dockerfile,
            labels=[f"{RECIPE_LABEL}={digest}", *image.labels],
            platform=image.platform,
        )

        probed: str = docker.output(["run", "--rm", *image.platform_args, image.tag, *image.probe]) if image.probe else ""
        log.bind(tag=image.tag, probe=probed).success("Built the Docker image.")
        _READY.add(image.tag)


class VersionPart(StrEnum):
    """Which part of a version a bump raises."""

    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


def bumped(version: str, part: VersionPart) -> str:
    """``version`` with ``part`` raised by one and every later part reset to zero."""
    major, minor, patch = (int(number) for number in version.split("."))

    if part is VersionPart.MAJOR:
        return f"{major + 1}.0.0"

    if part is VersionPart.MINOR:
        return f"{major}.{minor + 1}.0"

    return f"{major}.{minor}.{patch + 1}"


def bump_source(source: str, version: str, digest: str) -> str:
    """A recipe module's source with its released version and digest replaced.

    Args:
        source: The recipe module's source text.
        version: The new ``IMAGE_VERSION``.
        digest: The new ``IMAGE_DIGEST``.
    """
    for name, value in (("IMAGE_VERSION", version), ("IMAGE_DIGEST", digest)):
        source, count = re.subn(
            rf'^{name} = ".*"$',
            f'{name} = "{value}"',
            source,
            count=1,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise ValueError(f"{name} is not assigned on a line of its own.")

    return source


class ImageAction(StrEnum):
    """The ``femforge image`` subcommands."""

    DOCKERFILE = "dockerfile"
    TAG = "tag"
    PULL = "pull"
    BUILD = "build"
    BUMP = "bump"


def add_arguments(parser: argparse.ArgumentParser, recipes: Sequence[ImageRecipe]) -> None:
    """The ``femforge image`` subcommands, each over one of ``recipes`` (the first by default)."""
    actions = parser.add_subparsers(dest="action", required=True)
    names: list[str] = [recipe.name for recipe in recipes]

    def with_name(action: argparse.ArgumentParser) -> argparse.ArgumentParser:
        _ = action.add_argument("name", nargs="?", default=names[0], choices=names, help="The image.")

        return action

    _ = with_name(actions.add_parser(ImageAction.DOCKERFILE, help="Print the generated Dockerfile."))
    _ = with_name(actions.add_parser(ImageAction.TAG, help="Print the tag this checkout's recipe resolves to."))
    _ = with_name(actions.add_parser(ImageAction.PULL, help="Pull the released image from the registry."))

    build = with_name(actions.add_parser(ImageAction.BUILD, help="Pull or build the image unless Docker already has it."))
    _ = build.add_argument("--no-pull", action="store_true", help="Build locally without trying the registry first.")

    bump = with_name(actions.add_parser(ImageAction.BUMP, help="Raise the image version if the recipe changed since the last bump."))
    _ = bump.add_argument(
        "--part",
        default=VersionPart.PATCH.value,
        choices=[part.value for part in VersionPart],
        help="The version part to raise.",
    )


def run(args: argparse.Namespace, recipes: Sequence[ImageRecipe]) -> int:
    """Run one ``femforge image`` subcommand."""
    action = ImageAction(cast("str", args.action))
    recipe: ImageRecipe = next(recipe for recipe in recipes if recipe.name == cast("str", args.name))

    if action is ImageAction.DOCKERFILE:
        print(recipe.dockerfile, end="")

        return 0

    image: DockerImage = recipe.image()
    if action is ImageAction.TAG:
        print(image.tag)

        return 0

    if action is ImageAction.PULL:
        if not image.published:
            raise CliError(f"The {recipe.name} recipe differs from its released {recipe.version}, so no registry has it; build it with `femforge image build`.")

        docker = DockerCli()
        docker.pull(image.tag, platform=image.platform)
        if docker.image_label(image.tag, RECIPE_LABEL) != recipe.digest:
            log.bind(tag=image.tag).warning("The pulled image was built from another recipe; `femforge image build` rebuilds it.")

        print(image.tag)

        return 0

    if action is ImageAction.BUILD:
        ensure_image(image, DockerCli(), pull=not cast("bool", args.no_pull))
        print(image.tag)

        return 0

    digest: str = recipe_digest(recipe.dockerfile)
    if digest == recipe.digest:
        print(f"The {recipe.name} recipe is unchanged since {recipe.version}; nothing to bump.")

        return 0

    if "site-packages" in recipe.source.parts:
        raise CliError("femforge image bump rewrites femforge's source; run it from a checkout.")

    version: str = bumped(recipe.version, VersionPart(cast("str", args.part)))
    source: str = recipe.source.read_bytes().decode("utf-8")
    _ = recipe.source.write_bytes(bump_source(source, version, digest).encode("utf-8"))
    print(f"{recipe.name} {recipe.version} -> {version} (recipe {digest[:8]}).")

    return 0
