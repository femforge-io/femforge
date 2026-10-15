"""``femforge new``: the copied script, the named README, the .gitignore, the refusal, ``--force`` and the printed command."""

import argparse
from pathlib import Path

import pytest

from femforge import scaffold
from femforge.common.cli import CliError


REPO_ROOT = Path(__file__).resolve().parents[1]
"""The repository root."""


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "magnet-plate"
    scaffold.new_project(project)

    return project


def test_the_script_is_copied_byte_for_byte(tmp_path: Path) -> None:
    project = _project(tmp_path)

    assert (project / scaffold.SCRIPT).read_bytes() == (scaffold.TEMPLATE / scaffold.SCRIPT).read_bytes()


def test_the_readme_is_named_after_the_project_folder(tmp_path: Path) -> None:
    project = _project(tmp_path)

    readme = (project / scaffold.README).read_text(encoding="utf-8")

    assert readme.startswith("# magnet-plate\n")
    assert "$" not in readme


def test_a_project_in_the_current_folder_takes_its_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "bench"
    folder.mkdir()
    monkeypatch.chdir(folder)

    scaffold.new_project(Path("."))

    assert (folder / scaffold.README).read_text(encoding="utf-8").startswith("# bench\n")


def test_the_quickstart_embeds_the_template_script() -> None:
    page = (REPO_ROOT / "docs" / "overview" / "quickstart.md").read_text(encoding="utf-8")
    script = (scaffold.TEMPLATE / scaffold.SCRIPT).relative_to(REPO_ROOT).as_posix()

    assert f'--8<-- "{script}"' in page


def test_the_gitignore_lists_the_caches_and_the_run_folders(tmp_path: Path) -> None:
    project = _project(tmp_path)

    assert (project / ".gitignore").read_text(encoding="utf-8").splitlines() == ["__pycache__/", "runs/", "runs-index/"]
    assert sorted(path.name for path in project.iterdir()) == [".gitignore", "README.md", "main.py"]


def test_a_folder_that_is_not_empty_is_refused_untouched(tmp_path: Path) -> None:
    project = tmp_path / "magnet-plate"
    project.mkdir()
    _ = (project / "notes.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(CliError, match="not empty"):
        scaffold.new_project(project)

    assert [path.name for path in project.iterdir()] == ["notes.txt"]


def test_a_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "magnet-plate"
    _ = target.write_text("", encoding="utf-8")

    with pytest.raises(CliError, match="is a file"):
        scaffold.new_project(target)


def test_an_empty_folder_needs_no_force(tmp_path: Path) -> None:
    project = tmp_path / "magnet-plate"
    project.mkdir()

    scaffold.new_project(project)

    assert (project / scaffold.SCRIPT).is_file()


def test_force_replaces_the_three_files_and_keeps_the_rest(tmp_path: Path) -> None:
    project = tmp_path / "magnet-plate"
    project.mkdir()
    _ = (project / "notes.txt").write_text("keep", encoding="utf-8")
    _ = (project / scaffold.SCRIPT).write_text("old", encoding="utf-8")

    scaffold.new_project(project, force=True)

    assert (project / "notes.txt").read_text(encoding="utf-8") == "keep"
    assert (project / scaffold.SCRIPT).read_bytes() == (scaffold.TEMPLATE / scaffold.SCRIPT).read_bytes()


@pytest.mark.parametrize(("name", "command"), [("magnet-plate", "uv run magnet-plate/main.py"), (str(Path("work") / "magnet-plate"), "uv run work/magnet-plate/main.py")])
def test_run_prints_the_command_that_runs_the_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str, command: str) -> None:
    monkeypatch.chdir(tmp_path)

    code = scaffold.run(argparse.Namespace(name=name, force=False))

    assert code == 0
    assert capsys.readouterr().out == f"{command}\n"
    assert (tmp_path / name / scaffold.SCRIPT).is_file()
