# Workbench

The workbench is a browser app for looking at runs: a dock of panels that shows solved fields in 3D and 2D, browses the run and artifact indexes, and follows runs and sweeps while they solve. It runs as a local [trame](https://kitware.github.io/trame/) server that you start from a script or from the `femforge-workbench` command, and it reads the same telemetry root and run bundles as the rest of femforge. You can also build your own dock from the same panels and list it beside the built-in ones.

![The bearing-seat example's dock: the Runs list at the left, the field view in the middle with its legend, the run's details below it and the field viewer's controls at the right](../assets/examples/bearing-seat.webp)

## Install

The workbench is a separate package, `femforge-workbench`, that the `workbench` extra installs:

```console
pip install "femforge[workbench]"
```

`ff.workbench` raises an `ImportError` that names this command when the extra is missing. The package has extras of its own, listed on the [install page](../overview/install.md#install-the-package), among them `app` for the `--webview` window and `pty` for an interactive terminal on Windows.

## Open the field viewer from a script

```python
import femforge as ff

display = ff.DisplaySpec(
    field="magnetic flux density",
    mode=ff.DisplayMode.MAGNITUDE,
    colormap="plasma",
    hidden_bodies=("air",),
    body_styles=(ff.BodyStyle(body="mover", paint=ff.BodyPaint.SOLID, color="#c0c0c0", opacity=0.5),),
    streamlines=ff.FieldLineOptions(seed_placement=ff.SeedPlacement.CENTER_PLANE, line_count=16),
)

viewer = ff.workbench.field_viewer(display=display, telemetry="telemetry")
ff.workbench.show(viewer, title="Field viewer")
```

[field_viewer][femforge.workbench.field_viewer] builds the viewer dock. Its run picker lists the indexed runs of the telemetry root, `FEMFORGE_TELEMETRY` when you pass none. Pass `run=` a [Result][femforge.run.Result], a [Run][femforge.run.Run], a run id or a bundle directory to open that run once the server starts. Without one, the viewer opens the newest indexed run as soon as one lands. The [DisplaySpec][femforge.viz.DisplaySpec] sets where the controls start; [Results](results.md#display-state) covers it. A `DisplaySpec` without a `field` opens each run on [default_display][femforge.viz.default_display].

[show][femforge.workbench.show] serves a workbench and returns when its window closes:

| `show(workbench)` | Opens |
|---|---|
| a dock `Split` | that dock, as built |
| a module-level function that returns a `Split` or a `Renderable` | its result, rebuilt on every save of a `.py` file under its source roots |
| any of these, with `tasks=` a module-level function listing `Task`s | the same, while the tasks run in the background, one at a time |
| a `WorkbenchSpec` | that catalogue card, as the command opens it by name |
| nothing | the launcher |

| `window=` | Opens in |
|---|---|
| `Window.APP`, the default | a Chrome or Edge app window; closing it stops the server |
| `Window.BROWSER` | a tab in the default browser |
| `Window.WEBVIEW` | pywebview, from the `app` extra |

`show` also takes `port`, `telemetry`, `persist` and `watch`, which match the command's flags below, and `tasks`, which the next section covers.

## Solve in the background

```python
from functools import partial

import femforge as ff

GAPS = (0.5, 1.0, 2.0)


def solve(gap: float) -> None:
    """Solves one gap."""


def solves() -> list[ff.workbench.Task]:
    """One task per gap."""
    return [ff.workbench.Task(f"Gap {gap:g} mm", partial(solve, gap)) for gap in GAPS]


def viewer() -> ff.workbench.Split:
    """The field viewer, which opens each run as it lands."""
    return ff.workbench.field_viewer(telemetry="runs-index")


if __name__ == "__main__":
    ff.workbench.show(viewer, tasks=solves)
```

`tasks` takes a module-level function that lists `Task`s; the workbench runs them one at a time in child processes while it serves, shows each in the status strip, and lists them again on every save. A [Task][femforge.workbench.Task] pairs a label with a function that takes no arguments; a module-level function listed bare is labelled by its name. Each child imports the script by module name, calls the function again and runs the task with the same label, so a `partial` or a lambda works and nothing is pickled. Guard the `show` call under `if __name__ == "__main__":`, or every child serves a workbench of its own.

| The strip shows | When |
|---|---|
| `Queued`, with its place in line | the task waits for the one before it |
| `Starting`, `Preparing Geometry`, `Meshing`, `Solving`, `Finishing` | the stage its child reports; `Solving` names the solver and, once known, the percent converged |
| `Done`, `Reused` | the task returned; `Reused` when every run it asked for was solved before |
| `Failed`, with the error | the task raised, or its child died; Details shows the traceback |
| `Cancelled` | Cancel, pressed twice, stopped it |
| `Script Error` | a save broke the script; the rows of the last good save stay |

- A save while a task runs lets it finish on its old code, then runs it once more. A task the save dropped finishes, then leaves the strip.
- When a task finishes on a newer run while the viewer shows the run it finished on before, the viewer opens the newer one.
- An unchanged task re-runs in about 3 s, the start of a child process: the [Driver][femforge.run.Driver] reuses its finished runs.
- The child stores its own records in the telemetry root, and the server only relays them to its terminal.

## The femforge-workbench command

```console
femforge-workbench                                # the launcher
femforge-workbench field-viewer --telemetry telemetry
femforge-workbench -m lab.workbenches:gap_study --app
```

| Argument | Does |
|---|---|
| `name` | Open the catalogue card with this name, such as `demo` or `field-viewer`. Without a name or `-m`, open the launcher. |
| `-m pkg.module:attr` | Open the module attribute, a function that returns a dock `Split` or a `Renderable`. |
| `-m pkg.module` | Open the module's `main()`, which returns a `Renderable`. |
| `--app` | Open a Chrome or Edge app window; closing it stops the server. |
| `--webview` | Open in pywebview, from the `app` extra. |
| `--dev` | Run the server as a child that restarts after every save to the shell's own modules; the page reloads itself. |
| `--port N` | Serve on port `N`, 8080 by default. |
| `--watch DIR` | Also rebuild the open workbench when a `.py` file under `DIR` is saved. Repeatable. |
| `--telemetry DIR` | The telemetry root the panels read. It also sets `FEMFORGE_TELEMETRY` for the process, so a card opened later reads the same root. |
| `--persist DIR` | Where layouts and choices are saved, the per-user config directory by default. |

Without `--app` or `--webview`, the command opens a browser tab. An unknown name, or the `browsers` or `monitors` card without a telemetry root, exits with code 2 and a message. [CLI](cli.md) lists the command beside `femforge`.

## The built-in cards

| Card | Opens |
|---|---|
| `launcher` | Every catalogue card: open and close docks, open previews, run scripts and browse what they produced. |
| `demo` | A sphere, a line figure and a grouped table, to try the shell without data. |
| `browsers` | The run browser over the run detail, beside the artifact browser over the artifact detail. |
| `field-viewer` | A solved run's fields: the view, the symmetry expansion, colormaps, bodies and field lines. |
| `monitors` | A run's heartbeat, stages, convergence and logs; a sweep's progress and failures; the log browser; a terminal. |

The `browsers` and `monitors` cards read the telemetry root; the [Telemetry](telemetry.md) page covers what it holds. The artifact panels read the store at `FEMFORGE_ARTIFACTS` when it is set, else the store the index rows name.

## The field viewer

The field viewer puts its controls beside one view, which draws every run, flat or solid. The app bar holds the run picker, which lists the 200 newest indexed runs that have fields, are still solving or failed, newest first, and shows the open run's bundle path beside it. Each run reads as its description, with its run id muted beside it. A run that is still solving reads Solving and opens once its solve ends; one whose solve failed reads Failed. The list follows the run index every second and holds still while its dropdown is open, so a run that lands never moves the row under the pointer and never replaces the open run. With no run given, the viewer opens the newest run with fields as soon as one lands, else the newest one still solving, else the newest failed one. The camera stays put when the next run frames alike.

**Each run opens on its own field.** A run colors the primary field of its solver stack: the flux density of a magnetostatics run, the temperature of a thermal or Joule heating run, the displacement of an elasticity run. A displacement opens on the component its surface forces share, such as Y under a pull along Y, and on Magnitude when they point several ways. Show Field Lines and Opacity Mapping start on for a flux density, a field strength, an electric field or a current, and off otherwise. A Quantity, Mode, Show Field Lines or Opacity Mapping choice you make carries over to the next run that has the quantity shown, and a run without it opens on its own field. The other controls carry over from the run before. A `DisplaySpec` that names a `field` opens each run on that field, mode, field lines and opacity mapping instead. The workbench keeps the panel layout, the open cards and each view's mode between sessions, never the field display.

The first time a run opens, a short tour points out the run picker, each section of the controls and the view's toolbar. Skip or Esc ends it, and it stays closed on later starts. The help button at the right of the app bar replays it.

The controls come in five sections:

| Section | Controls |
|---|---|
| Symmetry | Solved As, Show As, Sweep |
| Deformation | Deformed Shape, Scale |
| Field | Quantity, Mode, Colormap, Invert Colormap, Log Scale, Outline, Auto Range with the color range slider, Legend, Opacity Mapping |
| Field Lines | Show Field Lines, Seeds, Integrate Within, the seed count, Length |
| Bodies | An All Bodies row with the body colormap and the overall opacity, then one row per body and per material that owns several bodies, each with a visibility checkbox, a color and an opacity |

**Symmetry expands a run solved on a section.** Solved As names the solve frame. For an axisymmetric run, Show As shows the r–z half-plane as solved, mirrors it into the full plane, or revolves it around the axis into a solid in the frame the model was authored in. Revolve is the default, at a full Sweep of 360°; a smaller sweep cuts the solid open so the cut faces show the field inside the bodies. Only the surfaces are revolved, so a section costs about as much to draw revolved as flat. The field lines of a revolved run are seeded as in a solid run, on the center plane, the gap line or through the volume of the swept solid, integrated in the section and swept back to their seeds. A run solved in 3D or on a planar Cartesian 2D section has nothing to expand. [DisplaySpec][femforge.viz.DisplaySpec] holds the choice as `expansion` and `sweep`.

**Deformation draws the bodies displaced by the solved displacement.** For a run with a displacement, such as a linear elasticity run, Deformed Shape starts on and moves every point by the displacement times the scale. The scale starts where the largest displacement moves a tenth of the diagonal of the solved bodies' bounding box, and its slider reaches five times that. A new scale is drawn when the thumb is released and keeps the view. A scale of zero, or the switch off, draws the bodies as solved. The colors and the color range keep the solved values, and the field lines run through the displaced bodies. A section is displaced after it is mirrored or revolved, and an elemental displacement is first averaged onto each body's points. A run without a displacement reads No Displacement. [DisplaySpec][femforge.viz.DisplaySpec] holds the choice as `warp`, the vector array or None, and `warp_scale`, None for the automatic scale.

**The legend starts hidden.** The range slider wears the colormap between its thumbs, so the controls read as the legend. Set Legend to Vertical or Horizontal to draw a color bar in the view, titled with the field's unit, before a screenshot. Field lines share that bar while they map like the field, and get a bar of their own only when their colormap or range differs. Moving a thumb of the range slider pins the range and turns Auto Range off.

The Quantity picker lists every scalar, vector and principal-value array of the run. The stress and strain tensors are left out, with the `stress_xx` and `strain_xx` columns Elmer splits them into; read them with [Run.mesh()][femforge.run.Run.mesh]. The Mode picker offers what the quantity supports:

| [DisplayMode][femforge.viz.DisplayMode] | Colors by | Offered for |
|---|---|---|
| `MAGNITUDE` | the vector magnitude | vectors, the default |
| `X`, `Y`, `Z` | one component | vectors |
| `POLAR_XY` | the in-plane direction as hue, brightened by the in-plane magnitude | vectors |
| `VALUE` | the value | scalars, the default |
| `ABSVALUE` | the absolute value | scalars |
| `MAX_PRINCIPAL` | the largest principal value | principal stresses and strains, the default |
| `MID_PRINCIPAL`, `MIN_PRINCIPAL` | the middle or the smallest principal value | principal stresses and strains |

Each body's color comes from one of three sources, its [BodyPaint][femforge.viz.BodyPaint]:

| Paint | Color | In the body row |
|---|---|---|
| `FIELD` | the field colormap, by the displayed value | a swatch of the field colormap; click it for the body colormap |
| `COLORMAP` | one color of the body colormap, spread evenly over every body that takes it | a color chip; pick a color to fix it |
| `SOLID` | the body's own color | a color chip; the cross returns the body to the field colormap |

**The automatic color range spans every body.** With Auto range on, the range is the union of each body's own range, over every body whether it is shown, hidden or painted another way, so hiding a body never rescales the rest. Each body is clamped to its 2nd to 98th percentile, so a small body keeps its range beside a large one. Log scale keeps only the positive values and starts at their 1st percentile. Turn Auto range off to pin the range with the slider.

Field lines follow the shown vector field. While a scalar or a principal value is shown, they follow the magnetic flux density, else the first vector field, such as the displacement of an elasticity run. The switch stays off for a run without a vector field. They start on, from 18 × 18 seeds on the center plane. Seeds sit on a grid in the domain's bounding box: a volume, the center plane or a line along X through the center. A run shown flat moves volume seeds to the center plane. Length is the fraction of the domain diagonal a line may run, and each line ends at its first sharp reversal. Integrate Within limits the lines to one body.

## Runs and figures

| Panel | Shows |
|---|---|
| [RunBrowserPanel][femforge.workbench.RunBrowserPanel] | The run index as a table, refreshed every 5 s, with text, status, tag and physics filters and the runs claimed right now marked. A row click selects the run. |
| [RunDetailPanel][femforge.workbench.RunDetailPanel] | The selected run: its manifest summary, stage timeline, results, artifact inputs, files and retention history, re-read every second while it solves. Fields opens the run in the field viewer, Folder opens its directory, Rehydrate fetches its store inputs back and Purge opens a dialog that removes bundle files by a retention plan. |
| [FigurePanel][femforge.workbench.FigurePanel] | One plotly figure; `update()` swaps it, a figure with a `uirevision` keeps its zoom and selections, and `bind()` routes plotly events to a handler. |
| [TablePanel][femforge.workbench.TablePanel] | A polars frame that the browser sorts, filters, groups and hides columns of, with its layout saved per panel. |

## The dock

A dock is a tree of three nodes:

| Node | Holds |
|---|---|
| [Panel][femforge.workbench.Panel] | One tab: a title, a builder that makes the component once the shell exists, a `weight` and an optional `id`. |
| [Tabs][femforge.workbench.Tabs] | Panels sharing one tab group, the first one shown. |
| [Split][femforge.workbench.Split] | Panels, tab groups and splits along one axis, shared by weight. The root is horizontal and the axis alternates with depth. |

A panel's id is its `id`, else its title in lower case with dashes. The builder receives the shell, the id and the title.

The workbench saves its layout and its open cards when they change, and restores the layout on the next start when it names exactly the panels the dock defines; a changed dock starts from its authored layout. Popped-out and floating groups come back docked. The reset button in the app bar rebuilds the authored layout. Each table saves its column layout and each 3D view its render mode.

The documents live in the per-user config directory, such as `%LOCALAPPDATA%\femforge-workbench` on Windows or `~/.config/femforge-workbench` on Linux, as `<user>/<workbench>/<key>.json`. Pass `--persist DIR` to keep them elsewhere.

## Write your own workbench

A workbench is a module-level function that returns a `Split`. Register it as a card through the `femforge.workbenches` entry point group:

```python
import femforge as ff
import plotly.graph_objects as go
import polars as pl
from femforge_workbench.registry import WorkbenchSpec, panel


def force_curve(app: ff.workbench.Workbench, id: str, title: str) -> ff.workbench.FigurePanel:
    """The force against the gap."""
    figure = go.Figure(go.Scatter(x=[1.0, 2.0, 3.0], y=[8.7, 6.1, 4.4], name="force"))
    return ff.workbench.FigurePanel(app, id, title, figure)


def points(app: ff.workbench.Workbench, id: str, title: str) -> ff.workbench.TablePanel:
    """The solved points."""
    frame = pl.DataFrame({"gap": [1.0, 2.0, 3.0], "force": [8.7, 6.1, 4.4]})
    return ff.workbench.TablePanel(app, id, title, frame, filterable=True)


def gap_study() -> ff.workbench.Split:
    """The force curve beside its points."""
    return ff.workbench.Split([ff.workbench.Panel("Force", force_curve, weight=2), ff.workbench.Panel("Points", points)])


WORKBENCHES: list[WorkbenchSpec] = [
    panel("gap-study", "Gap Study", target=gap_study, description="Force against air gap.", group="Lab"),
]


if __name__ == "__main__":
    ff.workbench.show(gap_study)
```

```toml
[project.entry-points."femforge.workbenches"]
lab = "lab.workbenches:WORKBENCHES"
```

An entry point provides one [WorkbenchSpec][femforge_workbench.registry.WorkbenchSpec] or a sequence of them. Three functions in [femforge_workbench.registry][] declare one:

| Function | Card | Opens |
|---|---|---|
| `panel` | dock | the dock its target returns |
| `renderable` | viewer | the `Renderable` its target returns, in one 3D panel |
| `script` | script | its module, run with `python -m` in a subprocess; the launcher shows the files it lists in `outputs` |

A target is the function itself or its `pkg.module:attr` name, and its module is imported only when the card opens, so listing the catalogue imports nothing heavy. Two different specs under one name raise an error. A dock or viewer card rebuilds in place when you save a `.py` file under its source roots or a `--watch` directory, keeping the layout; a builder that raises leaves the old panels and names the error in the status line.

## Limits

- **One VTK.wasm mirror per page.** The newest 3D panel mirrors its scene in the browser; older ones stream rendered frames from the server and say so in their status line.
- A scene whose geometry exceeds 64 MB streams from the server instead of mirroring.
- `--app` and `Window.APP` look for Chrome and Edge at their default Windows install paths. Without either, the workbench opens a browser tab, and closing it leaves the server running.
- The terminal panel is interactive on Windows with the `pty` extra. Elsewhere it shows the live log stream, read-only.
- A `Split` passed to `show` is served as built. Pass the function that builds it to rebuild on save.
- The `browsers` and `monitors` cards need a telemetry root.
- Ctrl+C or closing the window stops the running task and waits up to 10 s for it to release its run. A task stuck in a long CAD or meshing call past that is killed, and its claim on the run is broken so the next start need not wait.
