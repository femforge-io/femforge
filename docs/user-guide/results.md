# Results

A [Result][femforge.run.Result] is the typed outcome of one solve: its status, the forces and peak values the solver backend, Elmer today, exported, and the solve frame they are expressed in. Quantities carry their units as pint quantities. The driver writes each result to `result.json` in the run bundle, so you can read it back later by run id without the solver. The field output stays beside it as a VTU file, which [Run][femforge.run.Run] opens as a numpy query.

## Load a result

```python
from pathlib import Path

import femforge as ff

result = ff.Result.load(Path("runs") / "calm-avocet-3f9a1c07")

print(result.status, result.frame.label)
print(result.component_forces["mover"])
print(result.axial_force("mover", ff.Axis.Z))
```

[Result.load][femforge.run.Result.load] takes the bundle directory or the `result.json` file and stamps `run_dir` with where it read from. [Driver.evaluate][femforge.run.Driver.evaluate] returns the same object for a fresh solve.

## Fields of a result

| Field | Unit | Holds |
|---|---|---|
| `status` | | `ok`, `diverged` or `error`. |
| `run_id` | | The run's identity, equal to the bundle directory name; None for a bare parse. |
| `run_dir` | | The bundle directory; never serialized, and None once retention removed the directory. |
| `frame` | | The run's solve frame. |
| `force` | N | Net force, from Elmer's global nodal force sum. |
| `torque` | N·m | Net torque, from Elmer's global nodal torque sum. |
| `component_forces` | N | Lumped magnetic force per sif Component name. |
| `component_torques` | N·m | Lumped magnetic torque per sif Component name. |
| `flux_density_max` | T | Peak flux density magnitude. |
| `temperature_max` | K | Peak temperature, from a thermal or joule-heating solve. |
| `residual` | | The last relative change the solver reported. |
| `wall_time` | s | Solver wall-clock time. |
| `elmer_version` | | The ElmerSolver version from the log. |
| `raw` | as printed | Every exported scalar column by name, including the ones `Result` does not type. |

A field stays None when the solve exported nothing for it, for example `force` on a thermal run.

The status comes from `elmersolver.log`: `error` when a line contains `ERROR` or the log never reaches `ALL DONE`, `diverged` on a non-convergence marker such as `FAILED to converge` or `NaN`, and `ok` otherwise.

## Forces

Add a [CalculateMagneticForce][femforge.physical.CalculateMagneticForce] to the tag of the bodies whose force you want. Elmer lumps the magnetic force over those bodies into one Component, named by the condition's `label` or else by the tag's name. `Result.component_forces` holds one vector per Component name.

[Result.axial_force][femforge.run.Result.axial_force] returns a Component's force along an axis in the authored frame, as a pint quantity in newtons; `axis=None` uses the frame's own axis. The typed force and torque fields are the frame's totals over the whole model. Each frame's page says how it answers both; see [Frames](frames/index.md). `Run.components` lists each Component with its sif block number, force and torque.

## Scalars

The numbers come from SaveScalars, which writes `scalars.dat` and its `scalars.dat.names` sidecar. [parse_result][femforge.run.parse_result] reads the last row of every `.dat` file in the bundle, because a sweep or a transient solve appends one row per step and the last is the converged state.

A partitioned solve writes one `.dat.<rank>` file per rank instead. Elmer reduces the exported values globally, so every rank writes the same row, and femforge reads rank 0 alone, never a sum. When the ranks disagree, it logs a warning and still uses rank 0: a SaveScalars operator without a global reduction is partition-local.

The solver log supplies the rest: the residual is the last relative change on a `RELC` line, and the wall time is the real time on the `SOLVER TOTAL TIME(CPU,REAL)` line.

For a conduction solve, `Run.electrode_currents` gives the terminal current per named electrode boundary and `Run.phase_currents` the current per phase.

## Field output

[result_vtu][femforge.run.result_vtu] and `Run.vtu_path` return the run's ParaView output: the `.pvtu` master when the solve was partitioned, else the newest `.vtu`. Use them instead of a `*.vtu` glob, which also matches a partitioned run's per-rank pieces. Open the file in ParaView, or read it with pyvista. Its `GeometryIds` cell array holds each cell's mesh id, and `Run.bodies` maps those ids to body names.

`Run.mesh()` reads the VTU into a [FieldMesh][femforge.run.FieldMesh] with the body names attached. `get()` starts a [FieldQuery][femforge.run.FieldQuery] over one array; filters return refined copies, and terminals return numpy values:

```python
import femforge as ff

run = ff.Run("runs/calm-avocet-3f9a1c07")
flux = run.mesh().get(ff.elmer.ElmerVariable.MAGNETIC_FLUX_DENSITY)

magnets = flux.where_material("N52").magnitude()
print(len(magnets), magnets.max(), magnets.mean())

gap = flux.where_tag(ff.TagKey.NAME, "gap").where_pos(z=(None, 0.0))
print(gap.integrate(), gap.total_measure())
```

| Filter | Keeps |
|---|---|
| `where_body(*bodies)` | The named bodies' own cells. |
| `where_material(tag)` | The cells of every body with this material tag. |
| `where_tag(key, value)` | The cells of every body tagged `key=value`. |
| `where_ids(ids)` | Cells by raw mesh id, boundary cells included. |
| `where_pos(x, y, z)` | Cells whose center lies inside per-axis `(min, max)` bounds; None leaves a side open. |
| `where_mask(mask)` | Cells an arbitrary boolean mask flags. |
| `magnitude()` | The same cells, as the vector norm or the absolute value. |

| Terminal | Returns |
|---|---|
| `values`, `measures`, `cell_indices` | The selected cells' values, volumes (areas in 2D) and grid indices. |
| `sum()`, `min()`, `max()` | Plain reductions, per component for a vector. |
| `mean()` | The measure-weighted mean. |
| `integrate()` | ∫ value dV, or the plane-slice ∫ value dA in 2D. |
| `integrate_revolved()` | ∫ value 2πr dA, the full-revolution integral of a section solved about an axis; see [Axisymmetric](frames/axisymmetric.md#results). |
| `total_measure()` | The selection's volume, or area in 2D. |
| `cells()` | The selected cells as a pyvista grid, for plotting. |

Array names resolve loosely: the exact name first, then the elemental `"<name> e"` array, then any name that contains it. When retention purged the VTU, `Run.mesh()` raises and names the cleanup pass that removed it.

## Visualization

The viz helpers need the `viz` extra (`pip install "femforge[viz]"`), which brings pyvista, VTK, matplotlib, meshio and magpylib. They import pyvista only when they draw, so the models stay importable without it.

```python
import femforge as ff

run = ff.Run("runs/calm-avocet-3f9a1c07")
vtu = run.vtu_path
if vtu is not None:
    ff.viz.render_field_lines(vtu, ff.FieldLineOptions(line_count=8), off_screen=True, screenshot="field-lines.png")
```

| Helper | Draws |
|---|---|
| [render_field_lines][femforge.viz.render_field_lines] | The flux-density field lines of a VTU, in a window or headless to a PNG. |
| [FieldLines][femforge.viz.FieldLines] | The same lines as a renderable that integrates once and draws on demand. |
| [Scene][femforge.viz.Scene] | Several renderables in one plotter refresh, later items on top. |
| [FieldScene][femforge.viz.FieldScene] | One run's solved bodies as a recolorable field display, driven by a `DisplaySpec`. |

## Validation against magpylib

The [validation][femforge.validation] package cross-checks a magnetostatic solve against magpylib, which computes the field of uniformly magnetized cuboids analytically. It needs the `viz` extra. The comparison is a sanity bound, so expect some disagreement. It catches a field wrong by an order of magnitude or pointing the wrong way.

| Step | Function |
|---|---|
| Describe the magnets | [sources_from_assembly][femforge.validation.sources_from_assembly] reads each magnet's box and its magnetization from the same labels and domain the mesher uses. |
| Sample the solve | [extract_vtu_b][femforge.validation.extract_vtu_b] or [sample_vtu_b][femforge.validation.sample_vtu_b] reads B from the VTU. |
| Sample magpylib | [sample_magpylib_b][femforge.validation.sample_magpylib_b] evaluates B at the same points. |
| Compare | [compare_fields][femforge.validation.compare_fields] returns a [FieldComparison][femforge.validation.FieldComparison]; `rel_rms_error` is the headline metric. |
| Draw | [MagpylibField][femforge.validation.MagpylibField] draws the analytical field lines, and it fits in the same `Scene` as `FieldLines`. |

## Display state

[DisplaySpec][femforge.viz.DisplaySpec] is the complete, serializable state of one field display: the array, how it reduces (value, a component, the magnitude), the colormap and its limits, the legend, how a run solved on a section is expanded, hidden bodies, per-body styles and opacity. [FieldLineOptions][femforge.viz.FieldLineOptions] is the same for field lines: seeding, line count, step lengths, colors. Both are plain pydantic models, so they round-trip through JSON, and `DisplaySpec` has a published schema on the [JSON schemas](../reference/schemas.md) page. The workbench field viewer starts from a `DisplaySpec` you pass it.
