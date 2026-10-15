"""The femforge-elmer recipe: its released hash, the package reasons, the purge list, and the one install heredoc."""

from __future__ import annotations

import re

from femforge.elmer.runtime.docker_image import ELMER, ELMER_IMAGE, ELMER_PACKAGE, IMAGE_DIGEST, IMAGE_VERSION, LICENSES, PPA_TOOLS, SOURCE, dockerfile
from femforge.execution.docker.image import BANNER_WIDTH, LICENSES_LABEL, REGISTRY, SOURCE_LABEL, VERSION_LABEL, recipe_digest


def test_the_recipe_matches_the_released_digest() -> None:
    assert recipe_digest(dockerfile()) == IMAGE_DIGEST, "The femforge-elmer recipe changed: run `femforge image bump`."


def test_the_released_recipe_is_published_under_the_registry_for_amd64() -> None:
    released = ELMER_IMAGE.image()

    assert released.tag == f"{REGISTRY}/femforge-elmer:{IMAGE_VERSION}"
    assert released.published is True and released.platform == "linux/amd64"
    assert released.labels == (f"{VERSION_LABEL}={IMAGE_VERSION}",)
    assert ELMER_IMAGE.probe[-1] == ELMER_IMAGE.probe_label == ELMER_PACKAGE.name


def test_the_base_is_pinned_by_digest_and_the_version_stays_out_of_the_recipe() -> None:
    text = dockerfile()

    assert re.search(r"^FROM ubuntu:24\.04@sha256:[0-9a-f]{64}$", text, re.MULTILINE)
    assert f'{SOURCE_LABEL}="{SOURCE}"' in text and f'{LICENSES_LABEL}="{LICENSES}"' in text
    assert IMAGE_VERSION not in text and VERSION_LABEL not in text


def test_every_installed_package_is_listed_with_its_reason() -> None:
    text = dockerfile()

    for package in (*PPA_TOOLS, *ELMER):
        assert f"    {package.name}" in text
        assert any(line.startswith("#") and package.name in line and line.endswith(package.reason) for line in text.splitlines())


def test_only_build_only_packages_are_purged() -> None:
    purge = next(line for line in dockerfile().splitlines() if line.startswith("apt-get purge"))

    assert purge.split()[4:] == [package.name for package in PPA_TOOLS if package.build_only]


def test_the_install_runs_as_one_heredoc_with_full_width_banners() -> None:
    lines = dockerfile().splitlines()
    banners = [line for line in lines if line.startswith("# -")]

    assert lines.count("RUN <<EOF") == 1 and lines.count("EOF") == 1
    assert lines[lines.index("RUN <<EOF") + 1] == "set -eux"
    assert banners and all(len(banner) == BANNER_WIDTH and banner.endswith(" #") for banner in banners)
