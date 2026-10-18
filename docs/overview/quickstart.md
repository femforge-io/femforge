# Quickstart

This page creates a project from the femforge template, solves it and opens the result in a browser. The project is one script: a disc magnet pulls on a steel plate across an air gap, solved at four gaps on the axisymmetric section.

## Before you start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs Python and the packages, and either [Docker](https://docs.docker.com/get-started/get-docker/), which runs the Elmer solver, or a native Elmer install. On Linux, also install the [system libraries](install.md#linux-system-libraries) gmsh loads. [Install](install.md) covers all four.

## Create the project

```console
uvx femforge new magnet-plate
```

The command writes `main.py`, a `README.md` and a `.gitignore` into `magnet-plate/`, then prints the command that runs the project. It refuses a folder that is not empty unless you pass `--force`.

## Run it

```console
uv run magnet-plate/main.py
```

The first run downloads everything once:

1. uv reads the script's header and installs Python and `femforge[workbench]`: about 1.2 GB, a few minutes.
2. The script starts the solves in the background, and your browser opens the workbench at http://localhost:8080.
3. When Elmer is not installed natively, femforge pulls the solver's Docker image, about 1.3 GB. When the pull fails, it builds the image instead, which takes a few minutes longer.
4. The four solves run, each in seconds. Each gap joins the **Run** list as soon as it starts solving, and the view opens the first one to arrive. When its solve ends, the view shows its magnetic field, revolved from its section into 3D with the field lines drawn. The terminal logs the force on the plate for each gap and the run that holds it. A short tour points out the controls on the first start. Pick another run in the **Run** list to compare.

![The workbench after the solves: the last gap revolved into 3D with field lines through the air, and the Symmetry, Field, Field Lines and Bodies controls at the left](../assets/examples/magnet-plate.webp)

Press `Ctrl+C` to stop. A later run loads each solved gap from `magnet-plate/runs/` instead of solving it again, and opens the newest one at once. Pass `--solve-only` to solve without the workbench.

## The script

```{ .python .no-check }
--8<-- "src/femforge/templates/new/main.py"
```

- `MagnetAndPlate` is the geometry: a build123d model whose parts carry their material and name. See [Geometry and tags](../user-guide/geometry.md).
- `config()` builds one `SimulationConfig`: the solve frame, the mesh sizes, the materials and the conditions. See [Conditions and physics](../user-guide/conditions.md), [Frames](../user-guide/frames/index.md) and [Mesh](../user-guide/mesh.md).
- `solve()` runs each gap through a `Driver` and reads the force from the typed `Result`. See [Driver and runs](../user-guide/driver.md) and [Results](../user-guide/results.md).
- The script starts `solve()` on a thread, then `show()` serves the field viewer that `viewer()` builds. With no run given, the viewer opens the newest run as soon as one lands. See [Workbench](../user-guide/workbench.md).

## What to change next

- Change a dimension in `MagnetAndPlate`, or add a gap to `GAPS`, and run the script again. femforge solves only the gaps it has not solved before.
- Swap the magnet grade for `ff.materials.N42()`. See [Materials](../user-guide/materials.md).
- Change what the workbench shows first in `viewer()`, such as the colormap or the hidden bodies. A save of `main.py` rebuilds the open workbench.
- Pin every package at its current version with `uv lock --script magnet-plate/main.py`.

The [examples](../examples/index.md) go further: a bearing seat in 2D and 3D, a heated copper puck on an FR4 board, and a solenoid pulling a steel plunger.
