"""Image machinery: install sections, version and dev tags, the build on first use, bumps, and the ``femforge image`` commands."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

import femforge.execution.docker.image as image
from femforge import cli
from femforge.execution.docker.cli import DockerCli, DockerError
from femforge.execution.docker.image import (
    RECIPE_LABEL,
    REGISTRY,
    VERSION_LABEL,
    AptPackage,
    DockerImage,
    ImageRecipe,
    Section,
    VersionPart,
    bump_source,
    bumped,
    ensure_image,
    install_run,
    recipe_digest,
)


RECIPE_SOURCE = '''IMAGE_VERSION = "0.1.0"
"""Doc."""

IMAGE_DIGEST = "aaa"
'''

DOCKERFILE = "FROM scratch\n"


class _FakeDocker(DockerCli):
    """A DockerCli that holds images in memory and a registry of pullable ones, recording every pull and build."""

    def __init__(self, labels: dict[str, str] | None = None, registry: dict[str, str] | None = None, *, denied: bool = False) -> None:
        super().__init__()
        self.labels: dict[str, str] = labels if labels is not None else {}
        self.registry: dict[str, str] = registry if registry is not None else {}
        self.denied: bool = denied
        self.pulls: list[tuple[str, str | None]] = []
        self.builds: list[tuple[str, list[str], str | None]] = []
        self.probes: list[list[str]] = []

    def image_label(self, tag: str, label: str) -> str | None:
        return self.labels.get(tag)

    def has_image(self, tag: str) -> bool:
        return tag in self.labels

    def pull(self, tag: str, *, platform: str | None = None, timeout_s: float = 1800.0) -> None:
        self.pulls.append((tag, platform))
        if self.denied:
            raise DockerError(f"docker pull of {tag} exited with code 1", returncode=1, output_tail="Error response from daemon: denied")

        if tag not in self.registry:
            raise DockerError(f"docker pull of {tag} exited with code 1", returncode=1, output_tail="manifest unknown")

        self.labels[tag] = self.registry[tag]

    def build(self, tag: str, dockerfile: str, *, labels: Sequence[str], platform: str | None = None) -> None:
        self.builds.append((tag, list(labels), platform))
        self.labels[tag] = labels[0].split("=", 1)[1]

    def output(self, args: Sequence[str], *, timeout_s: float | None = None) -> str:
        self.probes.append(list(args))

        return "9.0-1"


def _published(platform: str | None = None) -> DockerImage:
    return DockerImage(f"{REGISTRY}/tool:0.1.0", DOCKERFILE, published=True, platform=platform, labels=(f"{VERSION_LABEL}=0.1.0",))


def _recipe(source: Path, *, released: bool = True) -> ImageRecipe:
    """A one-line recipe whose released digest matches it unless ``released`` is False."""
    dockerfile = "FROM scratch\n"

    return ImageRecipe(
        name="tool",
        version="0.1.0",
        digest=recipe_digest(dockerfile) if released else "0" * 64,
        dockerfile=dockerfile,
        source=source,
    )


@pytest.fixture(autouse=True)
def _fresh_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image, "_READY", set[str]())


def test_a_section_aligns_its_package_reasons_under_a_centered_banner() -> None:
    packages = (AptPackage("a", "Does A"), AptPackage("longer", "Does B"))

    lines = Section("Install", ("apt-get update",), packages).render().splitlines()

    assert lines[0] == f"# {'-' * 34} INSTALL {'-' * 33} #"
    assert lines[1:] == ["#           a        Does A", "#           longer   Does B", "apt-get update"]


def test_install_run_wraps_the_sections_in_one_strict_heredoc() -> None:
    run = install_run([Section("One", ("true",)), Section("Two", ("false",))])

    assert run.startswith("RUN <<EOF\nset -eux\nexport DEBIAN_FRONTEND=noninteractive\n\n# ")
    assert run.endswith("\nfalse\nEOF")


def test_a_released_recipe_is_published_under_the_registry(tmp_path: Path) -> None:
    recipe = ImageRecipe(
        name="tool",
        version="0.1.0",
        digest=recipe_digest(DOCKERFILE),
        dockerfile=DOCKERFILE,
        source=tmp_path,
        platform="linux/amd64",
    )

    assert recipe.image() == DockerImage(
        f"{REGISTRY}/tool:0.1.0",
        DOCKERFILE,
        published=True,
        platform="linux/amd64",
        labels=(f"{VERSION_LABEL}=0.1.0",),
    )


def test_an_unreleased_recipe_gets_a_local_dev_tag_that_is_never_published(tmp_path: Path) -> None:
    digest = recipe_digest(DOCKERFILE)

    dev = _recipe(tmp_path, released=False).image()

    assert dev.tag == f"tool:0.1.0-dev.{digest[:8]}"
    assert dev.published is False
    assert dev.labels == (f"{VERSION_LABEL}=0.1.0-dev.{digest[:8]}",)


def test_an_image_without_a_recipe_is_reused_when_present_and_never_pulled_again() -> None:
    docker = _FakeDocker({"mine:1": ""})

    ensure_image(DockerImage("mine:1"), docker)

    assert docker.pulls == [] and docker.builds == []


def test_an_image_without_a_recipe_is_pulled_once_and_never_built() -> None:
    docker = _FakeDocker(registry={"mine:1": ""})

    ensure_image(DockerImage("mine:1", platform="linux/amd64"), docker)
    ensure_image(DockerImage("mine:1", platform="linux/amd64"), docker)

    assert docker.pulls == [("mine:1", "linux/amd64")]
    assert docker.builds == []


def test_an_image_without_a_recipe_that_cannot_be_pulled_raises() -> None:
    with pytest.raises(DockerError, match="manifest unknown"):
        ensure_image(DockerImage("mine:1"), _FakeDocker())


def test_a_dev_image_is_built_with_its_labels_once_and_probed_without_a_pull() -> None:
    docker = _FakeDocker()
    wanted = DockerImage("tool:0.1.0-dev.abc", DOCKERFILE, ("tool", "--version"), platform="linux/amd64", labels=(f"{VERSION_LABEL}=0.1.0-dev.abc",))

    ensure_image(wanted, docker)
    ensure_image(wanted, docker)

    assert docker.pulls == []
    assert docker.builds == [("tool:0.1.0-dev.abc", [f"{RECIPE_LABEL}={recipe_digest(DOCKERFILE)}", f"{VERSION_LABEL}=0.1.0-dev.abc"], "linux/amd64")]
    assert docker.probes == [["run", "--rm", "--platform", "linux/amd64", "tool:0.1.0-dev.abc", "tool", "--version"]]


def test_a_build_without_a_probe_runs_nothing_after() -> None:
    docker = _FakeDocker()

    ensure_image(DockerImage("tool:0.1.0", DOCKERFILE), docker)

    assert docker.probes == []


def test_an_image_built_from_the_same_recipe_is_reused() -> None:
    docker = _FakeDocker({f"{REGISTRY}/tool:0.1.0": recipe_digest(DOCKERFILE)})

    ensure_image(_published(), docker)

    assert docker.pulls == [] and docker.builds == []


def test_a_published_image_is_pulled_instead_of_built() -> None:
    docker = _FakeDocker(registry={f"{REGISTRY}/tool:0.1.0": recipe_digest(DOCKERFILE)})

    ensure_image(_published("linux/amd64"), docker)

    assert docker.pulls == [(f"{REGISTRY}/tool:0.1.0", "linux/amd64")]
    assert docker.builds == []


def test_a_published_image_another_recipe_built_is_rebuilt_after_the_pull() -> None:
    docker = _FakeDocker(registry={f"{REGISTRY}/tool:0.1.0": "stale"})

    ensure_image(_published(), docker)

    assert [tag for tag, _, _ in docker.builds] == [f"{REGISTRY}/tool:0.1.0"]


def test_a_private_or_unpublished_package_falls_back_to_a_build() -> None:
    docker = _FakeDocker(denied=True)

    ensure_image(_published(), docker)

    assert len(docker.pulls) == 1
    assert [tag for tag, _, _ in docker.builds] == [f"{REGISTRY}/tool:0.1.0"]


def test_a_build_without_pulling_skips_the_registry() -> None:
    docker = _FakeDocker(registry={f"{REGISTRY}/tool:0.1.0": recipe_digest(DOCKERFILE)})

    ensure_image(_published(), docker, pull=False)

    assert docker.pulls == []
    assert [tag for tag, _, _ in docker.builds] == [f"{REGISTRY}/tool:0.1.0"]


@pytest.mark.parametrize(
    ("part", "expected"),
    [(VersionPart.MAJOR, "2.0.0"), (VersionPart.MINOR, "1.3.0"), (VersionPart.PATCH, "1.2.4")],
)
def test_a_bump_raises_one_part_and_resets_the_later_ones(part: VersionPart, expected: str) -> None:
    assert bumped("1.2.3", part) == expected


def test_bump_source_replaces_the_version_and_digest_lines() -> None:
    assert bump_source(RECIPE_SOURCE, "0.1.1", "bbb") == RECIPE_SOURCE.replace("0.1.0", "0.1.1").replace("aaa", "bbb")


def test_bump_source_refuses_a_source_without_the_constants() -> None:
    with pytest.raises(ValueError, match="IMAGE_DIGEST"):
        _ = bump_source('IMAGE_VERSION = "0.1.0"\n', "0.1.1", "bbb")


def test_the_dockerfile_command_prints_the_default_recipe(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["image", "dockerfile"]) == 0
    assert capsys.readouterr().out == cli.IMAGES[0].dockerfile


def test_the_build_command_ensures_and_prints_the_tag(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ensured: list[tuple[str, bool]] = []

    def _ensure(wanted: DockerImage, docker: DockerCli, *, pull: bool = True) -> None:
        ensured.append((wanted.tag, pull))

    monkeypatch.setattr(image, "ensure_image", _ensure)

    assert cli.main(["image", "build", cli.IMAGES[0].name]) == 0
    assert cli.main(["image", "build", "--no-pull"]) == 0
    assert ensured == [(cli.IMAGES[0].image().tag, True), (cli.IMAGES[0].image().tag, False)]
    assert capsys.readouterr().out == f"{cli.IMAGES[0].image().tag}\n" * 2


def test_the_tag_command_prints_the_tag_the_recipe_resolves_to(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["image", "tag"]) == 0
    assert capsys.readouterr().out == f"{cli.IMAGES[0].image().tag}\n"


def test_the_pull_command_pulls_the_released_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    pulled: list[tuple[str, str | None]] = []
    label: list[str | None] = [recipe_digest(DOCKERFILE)]

    def pull(self: DockerCli, tag: str, *, platform: str | None = None, timeout_s: float = 1800.0) -> None:
        pulled.append((tag, platform))

    def image_label(self: DockerCli, tag: str, name: str) -> str | None:
        return label[0]

    monkeypatch.setattr(DockerCli, "pull", pull)
    monkeypatch.setattr(DockerCli, "image_label", image_label)
    released = ImageRecipe(name="tool", version="0.1.0", digest=recipe_digest(DOCKERFILE), dockerfile=DOCKERFILE, source=tmp_path, platform="linux/amd64")
    monkeypatch.setattr(cli, "IMAGES", (released,))

    assert cli.main(["image", "pull"]) == 0
    label[0] = "stale"
    assert cli.main(["image", "pull"]) == 0

    assert pulled == [(f"{REGISTRY}/tool:0.1.0", "linux/amd64")] * 2
    assert capsys.readouterr().out == f"{REGISTRY}/tool:0.1.0\n" * 2


def test_the_pull_command_refuses_a_dev_recipe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "IMAGES", (_recipe(tmp_path, released=False),))

    assert cli.main(["image", "pull"]) == 1
    assert "no registry has it" in capsys.readouterr().err


def test_bump_leaves_an_unchanged_recipe_alone(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["image", "bump"]) == 0
    assert "nothing to bump" in capsys.readouterr().out


def test_bump_rewrites_the_version_and_digest_of_a_changed_recipe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "recipe.py"
    _ = source.write_bytes(RECIPE_SOURCE.encode("utf-8"))
    monkeypatch.setattr(cli, "IMAGES", (_recipe(source, released=False),))

    assert cli.main(["image", "bump", "--part", "minor"]) == 0

    assert source.read_text(encoding="utf-8") == RECIPE_SOURCE.replace("0.1.0", "0.2.0").replace("aaa", recipe_digest("FROM scratch\n"))


def test_bump_refuses_an_installed_copy(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    installed = Path("venv/Lib/site-packages/femforge/elmer/runtime/docker_image.py")
    monkeypatch.setattr(cli, "IMAGES", (_recipe(installed, released=False),))

    assert cli.main(["image", "bump"]) == 1
    assert "run it from a checkout" in capsys.readouterr().err
