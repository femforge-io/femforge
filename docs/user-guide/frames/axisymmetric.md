# Axisymmetric

[Axisymmetric][femforge.frames.Axisymmetric] solves the r-z half-plane of a model that is symmetric about an axis, so a rotationally symmetric part such as a coil or a tubular actuator solves as a 2D problem. You author the full 3D model, and the frame sections it for the 2D solve. See [Frames](index.md) for what a frame decides.

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
            ff.EntityTag(selector={ff.TagKey.NAME: "mover"}, conditions=[ff.CalculateMagneticForce(label="mover")]),
        ],
    ),
)

print(config.frame.label, config.solved_physics())
```

| Property | Value |
|---|---|
| `kind` | `"axisymmetric"` |
| Mesh dimension | 2 |
| Solve coordinates | X = radial (>= 0), Y = along the axis, Z = 0 |
| Elmer coordinate system | `Axi Symmetric`, which integrates per radian |

## Fields

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

print(frame.label, frame.plane)
```

[Axisymmetric.plane][femforge.frames.Axisymmetric.plane] is the [SolvePlane][femforge.frames.SolvePlane] that rule picks: the authored origin of the section and the authored directions of its +X, `x_dir` (radial), and its +Y, `y_dir` (axis). The section and the workbench's expansion both read it.

## How femforge sections the model

The driver converts the STEP in its first stage, `prepare_geometry`, through [Axisymmetric.prepare_geometry][femforge.frames.Axisymmetric.prepare_geometry]:

1. It builds the parametric geometry when the config has one, else it reads the authored STEP.
2. [import_tagged_step][femforge.geometry.step.import_tagged_step] imports the STEP as tagged parts, one per named solid, and its named faces as markers. Every body needs a `key=value` name, and a STEP that holds only faces is rejected: author the 3D model and let the frame section it.
3. [check_geometry][femforge.frames.Frame.check_geometry] turns every body and named face 37° about the axis and raises [FrameGeometryError][femforge.frames.FrameGeometryError] for any that changes by more than 1e-4 of its volume or area. A box of air fails, so bound the model with [boundary_cylinder][femforge.geometry.utils.boundary_cylinder].
4. [axisymmetric_cross_section][femforge.geometry.section.axisymmetric_cross_section] places the parts in the solve frame through `plane`, then intersects each solid with the X >= 0 half of the Z = 0 plane. Each face keeps its solid's tags and color, and each named face becomes a named edge of the section.
5. It writes the section to `geometry.step` in the run directory, its named edges to `geometry.boundaries.json` and the check to `geometry.check.json` beside it, and the mesher meshes it in 2D. The run manifest records the check as `geometry_check`, on a failure too.

The section raises when a solid does not reach the half-plane, and when a section reaches X < 0, which Elmer's axisymmetric solve would reject silently. The mesher names each body's edge on the symmetry axis `<BODY>__AXIS`, and magnetostatics pins the potential to zero there.

[cross_section_3D][femforge.geometry.section.cross_section_3D] is the 3D counterpart for pictures: it cuts away a quarter wedge and keeps the tags.

## Physics

| Selected physics | Solves as |
|---|---|
| `MAGNETOSTATICS` | `MAGNETOSTATICS_2D` |
| `THERMAL` | `THERMAL` |
| every other [Physics][femforge.elmer.Physics] member | raises: solve it with `Solid()` |

The solved stack runs in the `Axi Symmetric` coordinate system. Authored `physics.overrides` must run in that coordinate system too, so customize the physics after you set the frame.

## Conditions

| Condition | Rule |
|---|---|
| [CurrentDensity][femforge.physical.CurrentDensity] | The axisymmetric coil drive: a signed azimuthal current density over a winding window. |
| [Magnetization][femforge.physical.Magnetization] | The direction must be parallel to the symmetry axis, either sign; it is emitted as its signed axial component. |
| [CalculateMagneticForce][femforge.physical.CalculateMagneticForce] | Torque is rejected: drop `calculate_magnetic_torque`, `torque_origin` and `torque_axis`. |

## Results

Results come back in the solve frame: a vector's Y component is the axial one and its X component the radial one. Elmer integrates per radian in its 2D symmetric coordinate systems, and femforge converts only the typed force and torque fields to full-revolution totals:

| Quantity | Value |
|---|---|
| `Result.force`, `Result.torque`, `Result.component_forces`, `Result.component_torques` | Full revolution: the axial component times 2π, the radial component set to zero (its net is zero by symmetry). |
| `Result.raw` | Per radian, exactly as Elmer printed it. |
| [magnetic_field_energy][femforge.run.magnetic_field_energy], [eddy_current_power][femforge.run.eddy_current_power], [force_triples][femforge.run.force_triples] and the other readers of `raw` | Per radian. |
| [FieldQuery.integrate][femforge.run.FieldQuery.integrate] | The plane-slice integral ∫ value dA. |
| [FieldQuery.integrate_revolved][femforge.run.FieldQuery.integrate_revolved] | The full-revolution volume integral ∫ value 2πr dA, with r the cell center's X. |

[Result.axial_force][femforge.run.Result.axial_force] takes an axis in the authored frame:

| `axis` | Returns |
|---|---|
| `None` | The force along the symmetry axis. |
| parallel to the symmetry axis | The axial force times the cosine to `axis`. |
| any other | Raises. |

The [workbench](../workbench.md) field viewer shows the section as solved, mirrors it into the full plane, or revolves it around the axis back into the authored frame.

## Limits

- Only magnetostatics and heat conduction have an axisymmetric form.
- An axisymmetric run computes no torque.
- femforge checks that every body and named face is a solid or surface of revolution about the axis. Half and quarter models fail the check, so author the whole revolution.
- Every solid must cross the half-plane; a part off to the side of the section raises.
