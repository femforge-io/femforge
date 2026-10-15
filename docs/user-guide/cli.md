# CLI

femforge installs two commands. `femforge` ships with the library: it starts a project, browses the artifact store, purges run bundles, reads the telemetry store, manages the Elmer Docker image and checks the machine. `femforge-workbench` ships with the `workbench` extra and opens the browser workbench. The help text on this page is generated from the parsers at build time, so it always matches the installed commands.

## femforge

```python exec="true" result="text" session="cli"
import os

os.environ["COLUMNS"] = "100"

import argparse

from femforge.cli import build_parser


def subcommands(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def render(parser):
    parser.color = False
    print(parser.format_help())
    for child in subcommands(parser).values():
        render(child)


femforge = build_parser()
femforge.color = False
print(femforge.format_help())
```

Every subcommand prints its own help with `-h`.

## New

Use `femforge new` to start a project: a folder with one runnable script, its README and a `.gitignore`. `uvx femforge new NAME` runs it without installing femforge first, and it prints `uv run NAME/main.py`, the command that runs the project. It refuses a folder that is not empty unless you pass `--force`. The [Quickstart](../overview/quickstart.md) walks through the project.

```python exec="true" result="text" session="cli"
render(subcommands(femforge)["new"])
```

## Artifacts

Use `femforge artifacts` to look inside a local artifact store: list what it holds, show one artifact's record and files, print its directory, copy its files out, or put a bundle's store-sourced STEP and mesh back beside it. A reference is a key, a key prefix, a name, an artifact directory or a path inside one. [Artifacts and retention](artifacts.md#browse-the-store) covers the store.

```python exec="true" result="text" session="cli"
render(subcommands(femforge)["artifacts"])
```

## Purge

Use `femforge purge` to apply a retention policy to bundles that already exist, such as after a sweep. It is a dry run unless you pass `--apply`. Start from a `--preset`, override single file classes, and narrow the bundles it touches with `--only-status`, `--older-than` and `--running-grace`. `--only-status abandoned` selects the bundles whose claim went silent before they finished. [Artifacts and retention](artifacts.md#purge-existing-bundles) covers the policies.

```python exec="true" result="text" session="cli"
render(subcommands(femforge)["purge"])
```

## Telemetry

Use `index`, `runs`, `logs`, `sweeps` and `telemetry` to read and maintain a telemetry store: build the run and artifact indexes, list runs, reap runs whose process died, query or tail the logs, follow a sweep's progress, and compact the parquet shards. Each takes `--telemetry`, else `FEMFORGE_TELEMETRY`. [Telemetry](telemetry.md) covers the store.

```python exec="true" result="text" session="cli"
for name in ("index", "runs", "logs", "sweeps", "telemetry"):
    render(subcommands(femforge)[name])
```

## Image

Use `femforge image` to manage the Docker image the Docker backend runs Elmer in: print its Dockerfile or tag, pull the released image, build it locally, or raise its version after a recipe change. [Elmer: native and Docker](elmer.md) covers the image.

```python exec="true" result="text" session="cli"
render(subcommands(femforge)["image"])
```

## Doctor

Use `femforge doctor` after an install or on a new machine to check that the machine can mesh and solve. It names the fix for each failed check and exits with code 1 when one fails. [Install](../overview/install.md#check-the-install) lists the checks.

```python exec="true" result="text" session="cli"
render(subcommands(femforge)["doctor"])
```

## femforge-workbench

Use `femforge-workbench` to open the launcher, a catalogue entry by name, or your own module with `-m`. `--app` and `--webview` open a window instead of a browser tab, and `--dev` restarts the server whenever you save a file in the shell package. [Workbench](workbench.md) covers the panels.

```python exec="true" result="text" session="cli"
from femforge_workbench.cli import build_parser as build_workbench_parser

render(build_workbench_parser())
```
