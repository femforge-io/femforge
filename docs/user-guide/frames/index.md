# Frames

A frame says how the model you author maps onto the coordinates the solver backend, Elmer today, solves in. Some frames solve the model whole; others solve a section of it, which turns a symmetric 3D problem into a smaller 2D one. You always author the full 3D model, and the frame prepares what the solve meshes. The frame is the `frame` field of [SimulationConfig][femforge.config.SimulationConfig] and defaults to `Solid()`.

Every frame is a [Frame][femforge.frames.Frame]: a frozen config model with a `kind` that names it in a serialized config, and the hooks each pipeline stage calls.

## The authoring frame

You model in whatever frame suits the part, for example Z up in your CAD or in build123d. Every axis a frame names lives in that authored frame. An [Axis][femforge.frames.Axis] is a line through `origin` (in mm) along `direction`; a scaled direction names the same axis. `ff.Axis.X`, `ff.Axis.Y` and `ff.Axis.Z` are the coordinate axes through the origin.

```python
import femforge as ff

axis = ff.Axis(origin=ff.Vec3(10, 0, 0), direction=ff.Direction(0, 0, 1))

print(axis.label, ff.Axis.Z.label)
```

Results stay in the solve frame, and [Result.frame][femforge.run.Result.frame] records which frame a run solved in.

## Shipped frames

| Frame | `kind` | Mesh dimension | Summary |
|---|---|---|---|
| [Solid](solid.md) | `"solid"` | 3 | Solves the authored 3D model as-is. |
| [Axisymmetric](axisymmetric.md) | `"axisymmetric"` | 2 | Solves the r-z half-plane of a model symmetric about an axis. |

## How a frame plugs into each stage

Each stage asks the frame one question rather than naming a frame. The frame's page says how it answers each one.

| Stage | What the frame decides | Hook |
|---|---|---|
| Geometry | Whether the solve meshes the authored STEP or a converted one, and the conversion. | [prepare_geometry][femforge.frames.Frame.prepare_geometry], [rewrites_geometry][femforge.frames.Frame.rewrites_geometry] |
| Mesh | The body dimension the mesher meshes, and whether the section has a symmetry axis to name. | [model_dim][femforge.frames.Frame.model_dim], [revolves][femforge.frames.Frame.revolves] |
| Physics | The form each selected physics solves as, the Elmer coordinate system of the stack, and the plane state of a 2D elasticity solve. | [solves][femforge.frames.Frame.solves], [coordinate_system][femforge.frames.Frame.coordinate_system], [plane_stress][femforge.frames.Frame.plane_stress] |
| Conditions | Which conditions the frame can drive: a vector condition must have a form in solve coordinates, and torque needs a frame that lumps one. | [solve_vector][femforge.frames.Frame.solve_vector], [supports][femforge.frames.Frame.supports], [lumps_torque][femforge.frames.Frame.lumps_torque] |
| Results | The whole model's total of what Elmer integrated over the solve domain, the solve domain's share of a whole-model total, and a force's component along an authored axis. | [total][femforge.frames.Frame.total], [solve_share][femforge.frames.Frame.solve_share], [along][femforge.frames.Frame.along] |
| Viewer | Where a sectioned solve's plane sits in the authored model, and whether the section revolves, so the workbench can expand the section back into the authored frame. | [plane][femforge.frames.Frame.plane], [revolves][femforge.frames.Frame.revolves] |

The config checks the frame against the physics and the conditions when you build it and when you assign a field, and [SimulationConfig.solved_physics][femforge.config.SimulationConfig.solved_physics] gives the physics a selection solves as under the frame. Set the frame before you customize the physics: a stack customized under one frame raises under a frame with another coordinate system.

With an artifact store, the converted STEP of a frame that rewrites the geometry is cached as a STEP artifact keyed by the geometry source and the frame (see [Artifacts and retention](../artifacts.md)).
