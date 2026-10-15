"""The femforge-elmer Docker image: headless Elmer from CSC's PPA on Ubuntu 24.04, with Open MPI and MUMPS."""

from __future__ import annotations

from pathlib import Path

from femforge.execution.docker.image import LICENSES_LABEL, SOURCE_LABEL, AptPackage, ImageRecipe, Section, apt_install, install_run


__all__ = ["ELMER_IMAGE", "dockerfile"]

BASE_IMAGE = "ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55"
"""Ubuntu 24.04 pinned by its OCI index digest, so the recipe hash names the base the image is built on."""

PLATFORM = "linux/amd64"
"""The only platform the CSC PPA builds Elmer for."""

SOURCE = "https://github.com/femforge-io/femforge"
"""The repository the recipe comes from."""

LICENSES = "GPL-2.0-or-later AND LGPL-2.1-or-later"
"""The licenses of the Elmer the image installs: ElmerSolver and ElmerGrid, and the ElmerSolver library."""

IMAGE_VERSION = "0.2.0"
"""The released version of the image; ``femforge image bump`` raises it whenever the recipe changes."""

IMAGE_DIGEST = "d1c93c582ba3cff6c3f7168a3d0079b0a085ea6c0f9dcd2ef2e0e6338978fc48"
"""The sha256 of the Dockerfile released as [`IMAGE_VERSION`][]."""

PPA_TOOLS: tuple[AptPackage, ...] = (
    AptPackage("software-properties-common", "Manages apt package archives", build_only=True),
    AptPackage("gpg-agent", "Manages security keys", build_only=True),
    AptPackage("ca-certificates", "Provides root CA certificates"),
)
"""The packages needed to add the Elmer PPA."""

ELMER_PACKAGE = AptPackage("elmerfem-csc", "Provides ElmerGrid and ElmerSolver, with Open MPI and MUMPS")
"""The Elmer package from the CSC PPA."""

ELMER: tuple[AptPackage, ...] = (ELMER_PACKAGE,)
"""The packages the Elmer section installs."""

INSTALL: tuple[Section, ...] = (
    Section("Install tools needed to add the PPA", ("apt-get update", apt_install(PPA_TOOLS)), PPA_TOOLS),
    Section("Register the Elmer PPA", ("add-apt-repository -y ppa:elmer-csc-ubuntu/elmer-csc-ppa",)),
    Section("Install Elmer", ("apt-get update", apt_install(ELMER)), ELMER),
    Section("Cleanup PPA tooling", (f"apt-get purge -y --auto-remove {' '.join(p.name for p in PPA_TOOLS if p.build_only)}",)),
    Section("Cleanup apt indexes", ("rm -rf /var/lib/apt/lists/*",)),
)
"""The install script, in order."""


def dockerfile() -> str:
    """The image's Dockerfile, generated from `INSTALL`."""
    return f"""\
# syntax=docker/dockerfile:1
# ^ Enables RUN heredocs (BuildKit).

# Elmer FEM (headless) on Ubuntu 24.04, installed from CSC's official PPA.
FROM {BASE_IMAGE}

# The repository this recipe comes from, and the licenses of the Elmer it installs.
LABEL {SOURCE_LABEL}="{SOURCE}" \\
      {LICENSES_LABEL}="{LICENSES}"

# Installs headless Elmer FEM from CSC's official package archive.
# One RUN, so the cleanup at the end actually shrinks the image.
{install_run(INSTALL)}

# Open MPI refuses to launch as root by default, and containers run as root.
# These allow parallel runs, e.g.:  mpirun -np 4 ElmerSolver_mpi
ENV OMPI_ALLOW_RUN_AS_ROOT=1 \\
    OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1

# Working directory for the simulation case (case.sif + mesh directory).
# Typically bind-mounted:  docker run -v "$PWD":/case <image> ElmerSolver case.sif
WORKDIR /case
"""


ELMER_IMAGE = ImageRecipe(
    name="femforge-elmer",
    version=IMAGE_VERSION,
    digest=IMAGE_DIGEST,
    dockerfile=dockerfile(),
    source=Path(__file__),
    probe=("dpkg-query", "--show", "--showformat=${Version}", ELMER_PACKAGE.name),
    platform=PLATFORM,
    probe_label=ELMER_PACKAGE.name,
)
"""The image Elmer runs in under the Docker backend."""
