# Frames

A frame says how the model you author maps onto the coordinates the solver backend, Elmer today, solves in. [Solid][femforge.common.frame.Solid] solves the 3D model as it is. [Axisymmetric][femforge.common.frame.Axisymmetric] solves the r-z half-plane of a model that is symmetric about an axis, so a rotationally symmetric part such as a coil or a tubular actuator solves as a 2D problem. You always author the full 3D model, and femforge sections it for the 2D solve. The frame is the `frame` field of [SimulationConfig][femforge.config.SimulationConfig] and defaults to `Solid()`.

## The authoring frame

You model in whatever frame suits the part, for example Z up in your CAD or in build123d. Every axis a frame names lives in that authored frame. An [Axis][femforge.common.frame.Axis] is a line through `origin` (in mm) along `direction`; a scaled direction names the same axis. `ff.Axis.X`, `ff.Axis.Y` and `ff.Axis.Z` are the coordinate axes through the origin.

| Frame | `kind` | Mesh dimension | Solve coordinates |
|---|---|---|---|
| `ff.Solid()` | `"solid"` | 3 | the authored coordinates |
| `ff.Axisymmetric()` | `"axisymmetric"` | 2 | X = radial (>= 0), Y = along the axis, Z = 0 |

## Solid

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

A solid frame meshes the authored STEP itself when the config has no parametric geometry. Nothing is converted, so nothing is cached for the geometry stage.

## Axisymmetric

```python
import femforge as ff

config = ff.SimulationConfig(
    frame=ff.Axisymmetric(axis=ff.Axis.Z),
    mesh=ff.MeshConfig(step="models/tubular.step", global_mesh_size=0.5 * ff.U.mm),
    domain=ff.DomainConfig(
        materials=[ff.materials.Air(), ff.materials.N52()],
        tags=[
            ff.EntityTag(
                selector={ff.TagKey.MAGNETIZATION: "N"},
                conditions=[ff.Magnetization(direction=ff.Direction(0, 0, 1))],
            ),
            ff.EntityTag(selector={ff.TagKey.NAME: "mover"}, conditions=[ff.ForceComponent(label="mover")]),
        ],
    ),
)

print(config.frame.label, config.solved_physics())
```

| Field | Default | Meaning |
|---|---|---|
| `axis` | `ff.Axis.Z` | The axis of symmetry, in the authored frame. |
| `radial` | `None` | The direction from the axis toward the half-plane femforge sections. |

femforge stores `radial` as its unit part perpendicular to the axis and rejects a radial parallel to the axis. With `radial=None`, the half-plane faces the world axis least aligned with the symmetry axis, X first: `Axis.Z` maps the authored X to the radial direction, and `Axis.Y` leaves the coordinates as authored. An unset `radial` is left out of the serialized config, so it adds nothing to the run identity.

For an axis that does not pass through the origin, give it an origin:

```python
import femforge as ff

frame = ff.Axisymmetric(
    axis=ff.Axis(origin=ff.Vec3(10, 0, 0), direction=ff.Direction(0, 0, 1)),
    radial=ff.Direction(0, 1, 0),
)

print(frame.label)
```

## How femforge sections the model

The driver converts the STEP in its first stage, `prepare_geometry`, through [apply_frame][femforge.geometry.frame.apply_frame]:

1. It builds the parametric geometry when the config has one, else it reads the authored STEP.
2. It imports the STEP as tagged parts, one per named solid. Every body needs a `key=value` name, and a STEP that holds only faces is rejected: author the 3D model and let the frame section it.
3. [axisymmetric_cross_section][femforge.geometry.section.axisymmetric_cross_section] places the parts in the solve frame, then intersects each solid with the X >= 0 half of the Z = 0 plane. Each face keeps its solid's tags and color.
4. It writes the section to `geometry.step` in the run directory, and the mesher meshes it in 2D.

The section raises when a solid does not reach the half-plane, and when a section reaches X < 0, which Elmer's axisymmetric solve would reject silently. With an artifact store, the section is cached as a STEP artifact keyed by the geometry source and the frame (see [Artifacts and retention](artifacts.md)).

[cross_section_3D][femforge.geometry.section.cross_section_3D] is the 3D counterpart for pictures: it cuts away a quarter wedge and keeps the tags.

## Physics in 2D

[SimulationConfig.solved_physics][femforge.config.SimulationConfig.solved_physics] gives the physics a selection solves as under the frame. The config checks the frame against the physics and the conditions when you build it and when you assign a field.

| Selected physics | `Solid()` | `Axisymmetric()` |
|---|---|---|
| `MAGNETOSTATICS` | `MAGNETOSTATICS` | `MAGNETOSTATICS_2D` |
| `MAGNETOSTATICS_2D` | raises: select `MAGNETOSTATICS` | `MAGNETOSTATICS_2D` |
| `THERMAL` | `THERMAL` | `THERMAL` |
| every other [Physics][femforge.elmer.Physics] member | itself | raises |

| Condition or setting | Rule under `Axisymmetric()` |
|---|---|
| [CurrentDensity][femforge.physical.CurrentDensity] | Required frame: it raises under `Solid()`. |
| [Magnetization][femforge.physical.Magnetization] | The direction must be parallel to the symmetry axis, either sign. |
| [ForceComponent][femforge.physical.ForceComponent] | Torque is rejected: drop `calculate_magnetic_torque`, `torque_origin` and `torque_axis`. |
| `physics.overrides` | The stack's coordinate system must integrate per radian. Customize the physics after you set the frame. |

## Results in the solve frame

Results come back in the solve frame, and [Result.frame][femforge.run.Result.frame] records which one. Under `Axisymmetric()`, a vector's Y component is the axial one and its X component the radial one.

Elmer integrates per radian in its 2D symmetric coordinate systems. femforge converts only the typed force and torque fields to full-revolution totals:

| Quantity | Under `Axisymmetric()` |
|---|---|
| `Result.force`, `Result.torque`, `Result.component_forces`, `Result.component_torques` | Full revolution: the axial component times 2π, the radial component set to zero (its net is zero by symmetry). |
| `Result.raw` | Per radian, exactly as Elmer printed it. |
| [magnetic_field_energy][femforge.run.magnetic_field_energy], [eddy_current_power][femforge.run.eddy_current_power], [force_triples][femforge.run.force_triples] and the other readers of `raw` | Per radian. |
| [FieldQuery.integrate][femforge.run.FieldQuery.integrate] | The plane-slice integral ∫ value dA. |
| [FieldQuery.integrate_revolved][femforge.run.FieldQuery.integrate_revolved] | The full-revolution volume integral ∫ value 2πr dA, with r the cell center's X. |

[Result.axial_force][femforge.run.Result.axial_force] takes an axis in the authored frame. An axisymmetric run defaults to its symmetry axis and raises for an axis that is not parallel to it. A solid run has no default axis, so you pass one.

## Limits

- Only magnetostatics and heat conduction have an axisymmetric form.
- An axisymmetric run computes no torque.
- femforge sections one half-plane and does not check that the model is symmetric.
- Every solid must cross the half-plane; a part off to the side of the section raises.
