# femforge

[![PyPI](https://img.shields.io/pypi/v/femforge?include_prereleases)](https://pypi.org/project/femforge/)
[![License](https://img.shields.io/github/license/femforge-io/femforge)](https://github.com/femforge-io/femforge/blob/main/LICENSE)
[![Quality](https://github.com/femforge-io/femforge/actions/workflows/quality.yml/badge.svg)](https://github.com/femforge-io/femforge/actions/workflows/quality.yml)
[![Docs](https://img.shields.io/badge/docs-docs.femforge.io-1f9e09)](https://docs.femforge.io)
[![Examples](https://img.shields.io/badge/examples-4_workbenches-1f9e09)](https://docs.femforge.io/examples/)

femforge is finite-element analysis as a typed Python API.

[![The femforge workbench on the quickstart project: a disc magnet over a steel plate, revolved from its axisymmetric section into 3D, with field lines drawn through the air](https://raw.githubusercontent.com/femforge-io/femforge/main/docs/assets/examples/magnet-plate.webp)](https://docs.femforge.io/overview/quickstart/)

<table>
  <tr>
    <td align="center">
      <a href="https://docs.femforge.io/examples/bearing-seat/"><img src="https://raw.githubusercontent.com/femforge-io/femforge/main/docs/assets/examples/bearing-seat.webp" width="290" alt="The bearing-seat workbench: a magnet holding a steel ball, revolved into 3D with field lines, beside the list of runs"></a><br>
      <a href="https://docs.femforge.io/examples/bearing-seat/">Bearing seat</a><br>
      <sub>a magnet holds a ball, in 2D and 3D</sub>
    </td>
    <td align="center">
      <a href="https://docs.femforge.io/examples/coil-current/"><img src="https://raw.githubusercontent.com/femforge-io/femforge/main/docs/assets/examples/coil-current.webp" width="290" alt="The coil-current workbench: a solenoid's section mirrored across its axis, with field lines through the plunger and the frame"></a><br>
      <a href="https://docs.femforge.io/examples/coil-current/">Coil current</a><br>
      <sub>a coil pulls a plunger</sub>
    </td>
    <td align="center">
      <a href="https://docs.femforge.io/examples/thermal/"><img src="https://raw.githubusercontent.com/femforge-io/femforge/main/docs/assets/examples/thermal.webp" width="290" alt="The thermal workbench: a copper puck on an FR4 board in 3D, colored by temperature"></a><br>
      <a href="https://docs.femforge.io/examples/thermal/">Thermal</a><br>
      <sub>a puck heats a board</sub>
    </td>
  </tr>
</table>

Open-source solvers can do remarkable physics. Getting them to do it has meant hours in a manual and in forum threads, hunting for the keyword spelling that makes a case run. femforge moves that knowledge into the type system. Every setting is a typed Pydantic field with a docstring, and every physical quantity carries its unit, so your editor completes the config and validation rejects a mistake before anything is meshed or solved.

You describe a simulation once, as one config, and a `Driver` meshes it, solves it and returns results that carry their units. Geometry, meshing and solving run behind interfaces, so the same config can target new backends as they land.

> **NOTE**: femforge is pre-alpha. The API will change between 0.x releases, and the [changelog](https://github.com/femforge-io/femforge/blob/main/CHANGELOG.md) records each change.

## What you get

- **One typed config.** [Pydantic](https://docs.pydantic.dev/) models check the geometry, the mesh, the materials, the conditions and the physics before anything runs.
- **Units that are checked.** Lengths, material properties, conditions and results carry [pint](https://pint.readthedocs.io/) quantities, so a value with the wrong dimension fails validation instead of corrupting a solve.
- **CAD to result in one call.** A tagged STEP from your CAD or a [build123d](https://github.com/gumyr/build123d) script, meshed and solved.
- **Runs you can reuse and query.** Each run is a bundle named after the content hash of its inputs, with its config, logs and fields. An identical config reuses the finished run, and a telemetry index lists every run.
- **A browser workbench.** Browse the runs, view the fields and compose your own panels from a script.
- **Extensible backends.** Geometry, meshing and solving run behind interfaces, so a new mesher or solver plugs in without changing your config. [Status](https://docs.femforge.io/overview/status/) lists what each interface and each physics supports today.

## Try it

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and either [Docker](https://docs.docker.com/get-started/get-docker/), which is recommended, or a native [Elmer](https://github.com/ElmerCSC/elmerfem) install. On Linux, also install the system libraries gmsh loads, as [Install](https://docs.femforge.io/overview/install/#linux-system-libraries) lists.

```console
uvx femforge new magnet-plate
uv run magnet-plate/main.py
```

The script solves a disc magnet pulling on a steel plate at four air gaps, then opens the fields in your browser. The first run downloads about 1.2 GB of packages once, plus a 1.3 GB Docker image when Elmer is not installed natively; later runs start in seconds. The [Quickstart](https://docs.femforge.io/overview/quickstart/) walks through it, and the [examples](https://docs.femforge.io/examples/) go further.

## A solve in a few lines

```python
import femforge as ff

config = ff.SimulationConfig(
    mesh=ff.MeshConfig(step="magnet_and_plate.step"),
    frame=ff.Axisymmetric(),
    domain=ff.DomainConfig(
        materials=[ff.materials.N52(), ff.materials.Steel_1080(), ff.materials.Air()],
        tags=[
            ff.EntityTag(selector={ff.TagKey.NAME: "MAGNET"}, conditions=[ff.Magnetization(direction=ff.Direction(0, 0, 1))]),
            ff.EntityTag(selector={ff.TagKey.NAME: "PLATE"}, conditions=[ff.CalculateMagneticForce()]),
        ],
    ),
)
result = ff.Driver().evaluate(config)
print(result.axial_force("PLATE").m_as("newton"), "N")
ff.workbench.show(ff.workbench.field_viewer(result), window=ff.workbench.Window.BROWSER)
```

The STEP holds a magnet, a steel plate and an air box, each solid named with its tags, such as `Material=N52, Name=MAGNET`. [Geometry and tags](https://docs.femforge.io/user-guide/geometry/) builds the same model in build123d.

## Install in your own project

|                 | Command                        | Size   | Pick it for                                                       |
| --------------- | ------------------------------ | ------ | ----------------------------------------------------------------- |
| **Recommended** | `uv add "femforge[workbench]"` | 1.2 GB | solves, field plots and the browser workbench                     |
| Headless        | `uv add femforge`              | 0.8 GB | servers, CI and pipelines that need solves and typed results only |

With pip: `pip install "femforge[workbench]"` or `pip install femforge`. Elmer runs natively when it is installed, else in the femforge Docker image, pulled on the first solve; [Install](https://docs.femforge.io/overview/install/) covers both, and `femforge doctor` checks the result.

---

[Documentation](https://docs.femforge.io) · [Examples](https://docs.femforge.io/examples/) · [Status](https://docs.femforge.io/overview/status/) · [Changelog](https://github.com/femforge-io/femforge/blob/main/CHANGELOG.md) · [License](https://github.com/femforge-io/femforge/blob/main/LICENSE) (Apache-2.0)
