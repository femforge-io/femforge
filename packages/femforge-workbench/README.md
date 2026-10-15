# femforge-workbench

The browser workbench for [femforge](https://pypi.org/project/femforge/): a dock of panels that browse runs and artifacts, follow solves and logs as they happen, and show the solved fields in 3D. It runs on [trame](https://kitware.github.io/trame/), and every workbench opens from a script as well as from the command line.

## Install

Install it through femforge's extra, which pins the matching version:

```console
pip install "femforge[workbench]"
```

## From a script

`femforge.workbench` holds the panels and `show`, which serves a workbench until you stop it with `Ctrl+C` or close its app window:

```python
import femforge as ff


def show_field(config: ff.SimulationConfig) -> None:
    """Solve the config, then show its flux density with the air hidden."""
    result = ff.Driver().evaluate(config)
    display = ff.DisplaySpec(field="magnetic flux density", mode=ff.DisplayMode.MAGNITUDE, hidden_bodies=("AIR",))
    ff.workbench.show(ff.workbench.field_viewer(result, display=display), title="Field", window=ff.workbench.Window.BROWSER)
```

`show` takes a built dock, or a module-level function that returns one, which it rebuilds every time you save its file. Without an argument, it opens the launcher.

## The command

```console
femforge-workbench                          # the launcher, listing every workbench
femforge-workbench field-viewer             # one workbench, by name
femforge-workbench -m lab.board:workbench   # a module attribute that returns a dock
```

| Flag | What it does |
| --- | --- |
| `--port N` | The server port, 8080 by default. |
| `--telemetry DIR` | The telemetry root the panels read. The default is `$FEMFORGE_TELEMETRY`. |
| `--persist DIR` | Where layouts and choices are saved. The default is the per-user config directory. |
| `--watch DIR` | Another directory whose saves rebuild the workbench. Repeat it for more. |
| `--app` | Open a Chrome or Edge app window, and stop the server when it closes. |
| `--webview` | Open a pywebview window. It needs the `app` extra. |
| `--dev` | Restart the server after every save to the shell itself, and reload the page. |

## Your own workbenches

A package lists its workbenches under the `femforge.workbenches` entry point group:

```toml
[project.entry-points."femforge.workbenches"]
lab = "lab.workbenches:WORKBENCHES"
```

The target is a `WorkbenchSpec` or a sequence of them, built with `panel(...)`, `script(...)` or `renderable(...)` from `femforge_workbench.registry`:

```python
from femforge_workbench.registry import panel

WORKBENCHES = [panel("board", "Board", target="lab.board:workbench", description="The board layout and its fields.")]
```

The launcher lists them beside the built-in ones, and `femforge-workbench board` opens one directly. A target resolves only when it opens, so the launcher starts without importing your models.

## Extras

| Extra | Adds | Needed for |
| --- | --- | --- |
| `app` | pywebview | the `--webview` window |
| `ocp` | ocp_vscode | sending CAD to the OCP CAD Viewer in VS Code |
| `pty` | pywinpty, on Windows only | an interactive terminal panel; without it, the terminal shows the live log read-only |

The [workbench guide](https://docs.femforge.io/user-guide/workbench/) covers the panels. The [workbench architecture](https://docs.femforge.io/contributing/architecture/workbench/) covers the widgets, the client build, the theme, reloading and persistence.
