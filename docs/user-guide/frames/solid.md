# Solid

[Solid][femforge.frames.Solid] solves the authored 3D model as-is, in the authored coordinates. It is the default frame. See [Frames](index.md) for what a frame decides.

```python
import femforge as ff

config = ff.SimulationConfig(
    frame=ff.Solid(),
    mesh=ff.MeshConfig(step="models/actuator.step", global_mesh_size=2 * ff.U.mm),
    domain=ff.DomainConfig(
        materials=[ff.materials.Air(), ff.materials.N52()],
        tags=[
            ff.EntityTag(
                selector={ff.TagKey.MAGNETIZATION: "N"},
                conditions=[ff.Magnetization(direction=ff.Direction(1, 0, 0))],
            ),
            ff.EntityTag(selector={ff.TagKey.NAME: "mover"}, conditions=[ff.ForceComponent(label="mover")]),
        ],
    ),
)

print(config.frame.label, config.frame.model_dim)
```

| Property | Value |
|---|---|
| `kind` | `"solid"` |
| Fields | none |
| Mesh dimension | 3 |
| Solve coordinates | the authored coordinates |
| Elmer coordinate system | `Cartesian` |

## Geometry

A solid frame meshes the authored STEP itself when the config has no parametric geometry. Nothing is converted, so nothing is cached for the geometry stage.

## Physics

Every [Physics][femforge.elmer.Physics] member solves as itself, except `MAGNETOSTATICS_2D`, which is the axisymmetric stack: select `MAGNETOSTATICS` with [Axisymmetric](axisymmetric.md) instead.

## Conditions

| Condition | Rule |
|---|---|
| [Magnetization][femforge.physical.Magnetization] | Any direction; its three components are emitted as authored. |
| [CurrentDensity][femforge.physical.CurrentDensity] | Rejected: it drives `MAGNETOSTATICS_2D`, which a solid frame has no form for. |
| [ForceComponent][femforge.physical.ForceComponent] | Force and torque about any authored axis. |

## Results

Force and torque come back as Elmer integrated them over the whole model. [Result.axial_force][femforge.run.Result.axial_force] projects a Component's force onto the axis you pass; a solid frame has no axis of its own, so it raises without one.
