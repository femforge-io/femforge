"""The ``femforge new`` command: a project folder with one runnable script, its README and a ``.gitignore``."""

from __future__ import annotations

import argparse
from pathlib import Path
from string import Template
from typing import cast

from femforge.common.cli import CliError


COMMAND = "new"
"""The ``femforge`` subcommand that starts a project."""

TEMPLATE = Path(__file__).with_name("templates") / "new"
"""The directory the project files come from."""

SCRIPT = "main.py"
"""The script a project runs, copied byte for byte."""

README = "README.md"
"""The README, whose ``$name`` becomes the project's folder name."""

GITIGNORE = "gitignore"
"""The template's ignore file, stored without its dot so it does not act on this repository."""


def new_project(project: Path, *, force: bool = False) -> None:
    """Write the script, the README and the ``.gitignore`` into ``project``, which must be empty or missing unless ``force``."""
    if project.exists() and not project.is_dir():
        raise CliError(f"{project} is a file, not a directory")

    if project.is_dir() and any(project.iterdir()) and not force:
        raise CliError(f"{project} is not empty; pass --force to write into it anyway")

    project.mkdir(parents=True, exist_ok=True)
    readme = Template((TEMPLATE / README).read_text(encoding="utf-8")).substitute(name=project.resolve().name)

    _ = (project / SCRIPT).write_bytes((TEMPLATE / SCRIPT).read_bytes())
    _ = (project / README).write_text(readme, encoding="utf-8", newline="\n")
    _ = (project / ".gitignore").write_bytes((TEMPLATE / GITIGNORE).read_bytes())


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The ``femforge new`` arguments."""
    _ = parser.add_argument("name", help="The project folder to create, such as magnet-plate.")
    _ = parser.add_argument("--force", action="store_true", help="Write into a folder that is not empty, replacing the three files.")


def run(args: argparse.Namespace) -> int:
    """Create the project ``femforge new`` names and print the command that runs it."""
    project = Path(cast("str", args.name))
    new_project(project, force=cast("bool", args.force))
    print(f"uv run {(project / SCRIPT).as_posix()}")

    return 0
