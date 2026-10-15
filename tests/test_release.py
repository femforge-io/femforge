"""Both packages, the citation, the changelog and the project template carry one version, each package ships the same LICENSE and NOTICE, and NOTICE names only paths that exist and every bundled license."""

import re
import tomllib
from pathlib import Path

import femforge
from femforge import scaffold


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_ROOT = REPO_ROOT / "packages" / "femforge-workbench"
THIRD_PARTY_LICENSES = WORKBENCH_ROOT / "src" / "femforge_workbench" / "widgets" / "serve" / "THIRD_PARTY_LICENSES.txt"
VERSION_LINE = re.compile(r'^version = "(?P<version>[^"]+)"$', re.MULTILINE)
WORKBENCH_EXTRA = re.compile(r"^workbench = \[(?P<requirements>.*?)\]$", re.MULTILINE | re.DOTALL)
DEPENDENCIES = re.compile(r"^dependencies = \[(?P<requirements>.*?)\]$", re.MULTILINE | re.DOTALL)
SECTION_RULE = re.compile(r"^-{80}$", re.MULTILINE)
SCRIPT_BLOCK = re.compile(r"(?m)^# /// script$\s(?P<content>(^#(| .*)$\s)+)^# ///$")
LICENSE_HEADER = re.compile(r"(?P<name>\S+) (?P<version>\S+) \((?P<identifier>.+)\)")
NOTICE_PATHS = (
    ("LICENSE", "LICENSE"),
    ("tests/elmer/fixtures/sif_reference", "tests/elmer/fixtures/sif_reference"),
    ("femforge_workbench/widgets/serve/THIRD_PARTY_LICENSES.txt", "packages/femforge-workbench/src/femforge_workbench/widgets/serve/THIRD_PARTY_LICENSES.txt"),
    ("femforge_workbench/widgets/serve/fonts/", "packages/femforge-workbench/src/femforge_workbench/widgets/serve/fonts"),
)


def test_package_versions_match() -> None:
    def project_version(pyproject: Path) -> str:
        text = pyproject.read_text(encoding="utf-8")
        match = VERSION_LINE.search(text)
        assert match is not None, f"{pyproject} has no version line"

        return match.group("version")

    femforge_version = project_version(REPO_ROOT / "pyproject.toml")
    workbench_version = project_version(WORKBENCH_ROOT / "pyproject.toml")

    assert femforge_version == workbench_version == femforge.__version__


def test_workbench_extra_pins_the_workbench_version() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    extra = WORKBENCH_EXTRA.search(pyproject)
    assert extra is not None, "pyproject.toml has no workbench extra"

    assert f'"femforge-workbench=={femforge.__version__}"' in extra.group("requirements")


def test_workbench_depends_on_the_same_femforge_version() -> None:
    pyproject = (WORKBENCH_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dependencies = DEPENDENCIES.search(pyproject)
    assert dependencies is not None, "packages/femforge-workbench/pyproject.toml has no dependencies list"

    assert f'"femforge[viz]=={femforge.__version__}"' in dependencies.group("requirements")


def test_citation_version_matches() -> None:
    citation = (REPO_ROOT / "CITATION.cff").read_text(encoding="utf-8")

    assert f"version: {femforge.__version__}" in citation.splitlines()


def test_changelog_has_release_section() -> None:
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert f"## [{femforge.__version__}]" in changelog


def test_the_project_template_pins_this_minor_release_and_python() -> None:
    script = (scaffold.TEMPLATE / scaffold.SCRIPT).read_text(encoding="utf-8")
    block = SCRIPT_BLOCK.search(script)
    assert block is not None, "the template script has no PEP 723 script block"

    lines = block.group("content").splitlines(keepends=True)
    metadata = tomllib.loads("".join(line.removeprefix("#").removeprefix(" ") for line in lines))
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    major, minor = femforge.__version__.split(".")[:2]

    assert metadata["dependencies"] == [f"femforge[workbench]~={major}.{minor}.0"]
    assert metadata["requires-python"] == pyproject["project"]["requires-python"]


def test_license_and_notice_copies_are_identical() -> None:
    for name in ("LICENSE", "NOTICE"):
        root_copy = (REPO_ROOT / name).read_bytes()
        workbench_copy = (WORKBENCH_ROOT / name).read_bytes()

        assert root_copy == workbench_copy, f"packages/femforge-workbench/{name} differs from the root {name}"


def test_notice_names_existing_paths() -> None:
    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")

    for text, path in NOTICE_PATHS:
        assert text in notice, f"NOTICE does not name {text}"
        assert (REPO_ROOT / path).exists(), f"NOTICE names {text}, but {path} does not exist"


def test_notice_names_every_bundled_license() -> None:
    def license_identifier(section: str) -> str | None:
        header = section.strip().partition("\n")[0]
        match = LICENSE_HEADER.fullmatch(header)

        return match.group("identifier") if match else None

    licenses = THIRD_PARTY_LICENSES.read_text(encoding="utf-8")
    _preamble, _, packages = licenses.partition("\n\n")
    sections = SECTION_RULE.split(packages)
    # A notice can hold an 80-dash rule of its own (monaco-editor's does), which opens a section without a header.
    found = [license_identifier(section) for section in sections]
    identifiers = {identifier for identifier in found if identifier is not None}

    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
    missing = sorted(identifier for identifier in identifiers if identifier not in notice)

    assert identifiers, f"{THIRD_PARTY_LICENSES.name} has no package header"
    assert not missing, f"NOTICE does not name {', '.join(missing)}"
