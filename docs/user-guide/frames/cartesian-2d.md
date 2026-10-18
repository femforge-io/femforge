# Cartesian 2D

[Cartesian2D][femforge.frames.Cartesian2D] solves the planar section of a prismatic model, a part whose cross-section does not change along one direction, such as a flexure cut from plate or a long extrusion. You author the full 3D model, and the frame sections it on the plane normal to that direction. Elmer solves the section per metre of depth, and femforge scales the typed forces by the thickness you give. See [Frames](index.md) for what a frame decides.

```python
import femforge as ff

config = ff.SimulationConfig(
    frame=ff.Cartesian2D(
        normal=ff.Axis(origin=ff.Vec3(0, 0, 5), direction=ff.Direction(0, 0, 1)),
        thickness=10 * ff.U.mm,
    ),
    mesh=ff.MeshConfig(step="models/flexure.step", global_mesh_size=0.5 * ff.U.mm),
    physics=ff.PhysicsSelection(selected=ff.Physics.LINEAR_ELASTICITY),
    domain=ff.DomainConfig(
        materials=[ff.materials.Steel_316()],
        tags=[
            ff.EntityTag(selector={ff.TagKey.NAME: "LeftFace"}, conditions=[ff.FixedDisplacement()]),
            ff.EntityTag(
                selector={ff.TagKey.NAME: "RightFace"},
                conditions=[ff.SurfaceForce(force=ff.quantity(ff.Vec3(100, 0, 0), "N"))],
            ),
        ],
    ),
)

print(config.frame.label, config.solved_physics())
```

| Property | Value |
|---|---|
| `kind` | `"cartesian_2d"` |
| Mesh dimension | 2 |
| Solve coordinates | X = `x_dir`, Y = normal × X, Z = 0 |
| Elmer coordinate system | `Cartesian 2D`, which integrates per metre of depth |

## Fields

| Field | Default | Meaning |
|---|---|---|
| `normal` | `ff.Axis.Z` | The section plane: through the axis origin, normal to its direction. |
| `x_dir` | `None` | The authored direction of the solve +X. |
| `thickness` | required | The depth of the prism along the normal, which turns per-metre results into whole-model totals. |
| `state` | `PlaneState.STRESS` | How a linear elasticity solve treats the normal direction: [PlaneState][femforge.frames.PlaneState] `STRESS` for a thin plate, `STRAIN` for a long prism between rigid ends. |

femforge stores `x_dir` as its unit part in the plane and rejects an `x_dir` along the normal. With `x_dir=None`, the solve +X is the world axis least aligned with the normal, X first, and the solve +Y is normal × X, so the basis is right-handed:

| Normal | Solve X | Solve Y |
|---|---|---|
| `Z` | `X` | `Y` |
| `-Z` | `X` | `-Y` |
| `X` | `Y` | `Z` |
| `Y` | `X` | `-Z` |

[Cartesian2D.plane][femforge.frames.Cartesian2D.plane] is the [SolvePlane][femforge.frames.SolvePlane] that rule picks: the authored origin of the section and the authored directions of its +X and +Y. An unset `x_dir` is left out of the serialized config, so it adds nothing to the run identity. The thickness and the plane state are part of the frame, so changing either sections the model again.

## How femforge sections the model

The driver converts the STEP in its first stage, `prepare_geometry`, through [Cartesian2D.prepare_geometry][femforge.frames.Cartesian2D.prepare_geometry]:

1. It builds the parametric geometry when the config has one, else it reads the authored STEP.
2. [import_tagged_step][femforge.geometry.step.import_tagged_step] imports the STEP as tagged parts, one per named solid, and its named faces as markers. A STEP that holds no solid is rejected: author the 3D model and let the frame section it.
3. [planar_cross_section][femforge.geometry.section.planar_cross_section] places the parts in the solve frame through `plane`, then intersects each solid with the whole Z = 0 plane. Each face keeps its solid's tags and color, and each named face becomes a named edge, a [TaggedEdge][femforge.geometry.tags.TaggedEdge] with the face's tags.
4. It writes the section to `geometry.step` and the named edges to `geometry.boundaries.json` beside it, since gmsh imports no names on free edges. The mesher names each matching curve from that file and meshes the section in 2D.

A named face becomes the boundary group of its edge, so a condition on `LeftFace` in 3D lands on the same edge of the section. The section raises when a solid does not reach the plane, when a named face does not cross it, and when a named face lies in it. Put the plane at mid-depth, as the example does: a plane on a boundary face of the prism still sections it, but then each named end face meets the plane only along its own edge.

## Physics

| Selected physics | Solves as |
|---|---|
| `MAGNETOSTATICS` | `MAGNETOSTATICS_2D` |
| `THERMAL` | `THERMAL` |
| `LINEAR_ELASTICITY` | `LINEAR_ELASTICITY`, with two displacement components and the frame's plane state |
| every other [Physics][femforge.elmer.Physics] member | raises: solve it with `Solid()` |

The solved stack runs in the `Cartesian 2D` coordinate system. Authored `physics.overrides` must run in that coordinate system too, so customize the physics after you set the frame.

## Conditions

| Condition | Rule |
|---|---|
| [Magnetization][femforge.physical.Magnetization] | The direction must lie in the plane; it is emitted as its solve X and Y components. |
| [CurrentDensity][femforge.physical.CurrentDensity] | A signed current density along the normal, the solve +Z. |
| [CalculateMagneticForce][femforge.physical.CalculateMagneticForce] | Torque is rejected: drop `calculate_magnetic_torque`, `torque_origin` and `torque_axis`. |
| [FixedDisplacement][femforge.physical.FixedDisplacement] | A component along the normal must be zero or None, and is dropped; a constrained in-plane component must be the same solve component, so it needs a normal along ±Z, and a nonzero one also the same sign. |
| [SurfaceForce][femforge.physical.SurfaceForce] | The total force on the whole face; femforge divides it by the thickness and the edge length into a traction. The force must lie in the plane. |

## Results

Results come back in the solve frame. Elmer integrates per metre of depth in `Cartesian 2D`, and femforge converts only the typed force fields to whole-prism totals:

| Quantity | Value |
|---|---|
| `Result.force`, `Result.component_forces` | Whole prism: the in-plane components times the thickness in metres. |
| `Result.raw` | Per metre of depth, exactly as Elmer printed it. |
| [magnetic_field_energy][femforge.run.magnetic_field_energy], [force_triples][femforge.run.force_triples] and the other readers of `raw` | Per metre of depth. |
| [FieldQuery.integrate][femforge.run.FieldQuery.integrate] | The plane integral ∫ value dA, per metre of depth. |
| [FieldQuery.integrate_extruded][femforge.run.FieldQuery.integrate_extruded] | The prism integral ∫ value dA times a depth you pass, usually the thickness. |
| [Run.strain_energy][femforge.run.Run.strain_energy], [strain_energy][femforge.run.strain_energy] | Per metre of depth: multiply by `thickness_m` for the whole prism. |
| [clapeyron_energy][femforge.run.clapeyron_energy] | Per metre of depth when you pass the per-metre share of the force, `frame.solve_share(force)`, which balances the strain energy. |

[Result.axial_force][femforge.run.Result.axial_force] projects a force onto an authored axis you pass; the frame has no axis of its own, so `axis=None` raises.

The [workbench](../workbench.md) field viewer shows the section flat, as solved; its Symmetry section reads Nothing To Expand.

## Limits

- Only magnetostatics, heat conduction and linear elasticity have a Cartesian 2D form; electrostatics and current conduction do not.
- femforge does not check that the model is prismatic; it sections the plane you name.
- Plane stress assumes a thin part; use `PlaneState.STRAIN` for a long one.
- A Cartesian 2D run computes no torque.
- The viewer does not extrude the section back into a prism.
