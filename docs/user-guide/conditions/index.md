# Conditions and physics

A [Physics][femforge.elmer.Physics] member picks what the solver backend, Elmer today, solves, and conditions supply the sources and constraints of that solve. Each condition knows its physics, the sif section it lands in, and whether it attaches to a body or a boundary. It rides on an [EntityTag][femforge.physical.EntityTag] that selects bodies or named faces by their tags. One domain can carry conditions for several physics: a solve reads the conditions of its own physics and leaves the rest inert.

## Drive a magnet and measure its force

```python
import femforge as ff

magnet = ff.EntityTag(
    selector={ff.TagKey.NAME: "Magnet"},
    conditions=[
        ff.Magnetization(direction=ff.Direction(0, 0, 1)),
        ff.CalculateMagneticForce(
            label="MagnetForce",
            calculate_magnetic_torque=True,
            torque_origin=ff.Vec3(0, 0, 0),
            torque_axis=ff.Direction(0, 0, 1),
        ),
    ],
)

config = ff.SimulationConfig(
    mesh=ff.MeshConfig(step="magnet_in_air.step"),
    domain=ff.DomainConfig(materials=[ff.materials.Air(), ff.materials.N52()], tags=[magnet]),
    physics=ff.PhysicsSelection(selected=ff.Physics.MAGNETOSTATICS),
)
```

The STEP holds an air box and a body named `Magnet` tagged `Material=N52`. The `Magnetization` sets the magnet's direction, and the N52 remanence sets its strength. The `CalculateMagneticForce` lumps the magnetic force and torque on the magnet into one Elmer Component named `MagnetForce`. The result reads them back as `result.component_forces["MagnetForce"]` and `result.component_torques["MagnetForce"]`. See [Results](../results.md).

## Find a condition

Each physics has a page that lists the conditions it reads, grouped by the sif section they land in. Pick a physics row and a section column:

```python exec="true" session="conditions"
from femforge_docs.conditions import finder_table, physics_table, reads_table, shipped_table

print(finder_table())
```

## Physics

```python exec="true" session="conditions"
print(physics_table())
```

Select a physics with [PhysicsSelection][femforge.elmer.config.PhysicsSelection]. The config's frame decides the form a selection solves as, and a physics with no form in the frame raises when you build the config. You never select `MAGNETOSTATICS_2D` yourself; each frame's page lists the physics it solves and as what, see [Frames](../frames/index.md).

## Shipped conditions

```python exec="true" session="conditions"
print(shipped_table())
```

Every value is a pint quantity of the dimensionality its unit names. Build it with `ff.quantity(300, "kelvin")` or `300 * ff.U.K`. See [Materials](../materials.md#units).

## Where conditions land

A condition's `section` names the sif section its keywords land in:

- **Material**: `Magnetization` goes into the magnet's own Material block, where the magnetic solver reads it.
- **Component**: `CoilDrive` and `CalculateMagneticForce` become Elmer Components. One condition instance gathers every body it matches into one Component.
- **Body Force**: the other body conditions become one Body Force block per body.
- **Boundary Condition**: a boundary condition lands on the boundary groups its tag reaches, and several conditions on one group merge into one block.

A boundary condition on a body's tag lands on every boundary group that body borders, both its interfaces with other bodies and its outer shell. `Convection` and `CoilTerminal` land on the outer shell only (`<BODY>__EXT`): a film coefficient on an interior interface would bypass the conduction path, and a coil terminal is the electrode's exposed end face. A boundary condition on a named face's tag lands on that face's own group, as the next section shows.

For example, with a copper body named `Trace` on an FR4 body named `Board`:

| The `Board` tag carries | Lands on `BOARD__TRACE` | Lands on `BOARD__EXT` |
| --- | --- | --- |
| `FixedTemperature` | yes | yes |
| `Convection` | no | yes |

### Named faces

To load one face rather than a whole shell, tag the face. A [TaggedFace][femforge.geometry.tags.TaggedFace] laid on a face of a 3D body becomes a boundary group of its own, named after the face, such as `TIP` for `name="Tip"`. The mesher carves it out of the shell or interface it lies on, so `BEAM__EXT` keeps the rest of the shell. A face smaller than the body face it lies on splits that face.

```python
import build123d as b3d

import femforge as ff

box = b3d.Box(100, 10, 10)
beam = ff.TaggedPart([box], tags={ff.TagKey.MATERIAL: "Steel316"}, name="Beam")
x_faces = box.faces().filter_by(b3d.Axis.X).sort_by(b3d.Axis.X)
root = ff.TaggedFace(x_faces[0], name="Root")
model = ff.TaggedCompound(children=[beam, root])

cold_root = ff.EntityTag(selector={ff.TagKey.NAME: "Root"}, conditions=[ff.FixedTemperature(value=300 * ff.U.K)])
```

Take the face from the body's own `faces()`, so it coincides with the body face exactly, and sort by position: build123d does not order `faces()` along an axis.

- An `EntityTag` whose selector matches the face puts its boundary conditions on the face's group.
- The bordering body's boundary conditions still reach the face, `Convection` included when the face lies on the outer shell. Where the face and the body set the same keyword, the face's value wins.
- A face that lies on no body boundary stops the mesh with an error.
- `SurfaceForce` spreads its total force over the face's area, so it needs a named face; on a body's tag it raises. See [Linear elasticity](linear-elasticity.md).

Some conditions need no authoring:

- The magnetic physics pin the potential to zero on every outer shell, a far-field condition.
- A section solved about an axis pins the potential to zero on the symmetry axis (`<BODY>__AXIS`), which is exact there; see [Axisymmetric](../frames/axisymmetric.md).
- Every magnet body gets its own force Component, named after its body group. An authored `CalculateMagneticForce` that matches the magnet replaces it.

## Which conditions a physics reads

A coupled physics also reads the conditions of the physics it couples, so the same tags drive the standalone and the coupled solves.

```python exec="true" session="conditions"
print(reads_table())
```

`COIL_MAGNETOSTATICS` leaves `FixedPotential` inert: it sources the coil from `CoilDrive` and `CoilTerminal`.

## Checks before the solve

The config rejects a condition its frame cannot solve as soon as you build or assign it: a condition whose physics has no form in the frame, a direction with no form in solve coordinates, or torque in a frame that lumps none. Each frame's page lists its rules; see [Frames](../frames/index.md).

The sif writer checks the rest before it writes:

| Problem | Result |
| --- | --- |
| A material lacks a keyword its solvers need | Error |
| A magnet has no `Magnetization` with a direction | Error |
| `THERMAL`, `ELECTROSTATICS`, `CURRENT_CONDUCTION` or `JOULE_HEATING` resolves no boundary condition | Warning: the solve is likely ill-posed |
| `COIL_MAGNETOSTATICS` finds no `CoilDrive` | Warning: the coil has no source |
| A force Component exists but the stack cannot compute or export it | Warning |

## Customize the solver stack

Each physics has a preset: a typed [PhysicsConfig][femforge.elmer.config.PhysicsConfig] that holds the ordered solvers, the Simulation settings, the constants and the operating point. `config.resolved_stack()` returns the stack the run solves. To change it, call [customize_physics][femforge.config.SimulationConfig.customize_physics]:

```python
import femforge as ff

config = ff.SimulationConfig(
    mesh=ff.MeshConfig(step="magnet_in_air.step"),
    physics=ff.PhysicsSelection(selected=ff.Physics.MAGNETOSTATICS),
)

stack = config.customize_physics()
solver = stack.solver(ff.elmer.MagnetoDynamicsSolver)
solver.linear_system_max_iterations = 1000
stack.simulation.max_output_level = 3
```

`customize_physics()` copies the preset into `config.physics.overrides` once and returns it, so later calls edit the same stack. Every field is a typed sif keyword, validated when you assign it.

- The overrides are part of the config, so they enter the run identity and the manifest. Overrides equal to the preset leave the identity unchanged.
- Set the frame before you customize. A stack customized for one frame raises under a frame with another coordinate system.
- Every solver and the Simulation settings take `extra_keywords` for a sif keyword femforge has no field for. [GenericSolver][femforge.elmer.config.GenericSolver] adds a whole solver block femforge has no model for.

The solver models and their keyword tables live in the [Elmer solvers API](../../reference/elmer-config.md).

## Reference

- [Physical API](../../reference/physical.md): the conditions, `EntityTag` and `DomainConfig`.
- [Elmer API](../../reference/elmer.md): `Physics` and the backends.
- [Elmer solvers API](../../reference/elmer-config.md): `PhysicsSelection`, `PhysicsConfig`, the presets and every solver.
