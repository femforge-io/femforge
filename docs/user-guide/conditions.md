# Conditions and physics

A [Physics][femforge.elmer.Physics] member picks what the solver backend, Elmer today, solves, and conditions supply the sources and constraints of that solve. Each condition knows its physics and whether it attaches to a body or a boundary, and it rides on an [EntityTag][femforge.physical.EntityTag] that selects bodies by their tags. One domain can carry conditions for several physics: a solve reads the conditions of its own physics and leaves the rest inert.

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

The STEP holds an air box and a body named `Magnet` tagged `Material=N52`. The `Magnetization` sets the magnet's direction, and the N52 remanence sets its strength. The `CalculateMagneticForce` lumps the magnetic force and torque on the magnet into one Elmer Component named `MagnetForce`. The result reads them back as `result.component_forces["MagnetForce"]` and `result.component_torques["MagnetForce"]`. See [Results](results.md).

## Physics

| Member | Solves | Preset solver stack |
| --- | --- | --- |
| `MAGNETOSTATICS` | The static magnetic field of magnets and steel. | `MagnetoDynamicsSolver`, `MagnetoDynamicsCalcFieldsSolver`, `SaveScalarsSolver`, `ResultOutputSolver` |
| `MAGNETOSTATICS_2D` | Axisymmetric magnetostatics on the r-z half-plane, with `CurrentDensity` coil drives. | `MagnetoDynamics2DSolver`, `MagnetoDynamicsCalcFieldsSolver`, `SaveScalarsSolver`, `ResultOutputSolver` |
| `THERMAL` | Steady-state heat conduction. | `HeatSolver`, `SaveScalarsSolver` (peak temperature), `ResultOutputSolver` |
| `ELECTROSTATICS` | The electrostatic potential and field. | `ElectrostaticsSolver`, `ResultOutputSolver` |
| `CURRENT_CONDUCTION` | The static current density driven by electrode potentials. | `StatCurrentSolver`, `SaveScalarsSolver`, `ResultOutputSolver` |
| `JOULE_HEATING` | Current conduction coupled to heat conduction: the temperature its Joule losses produce. | `StatCurrentSolver`, `HeatSolver`, `SaveScalarsSolver`, `ResultOutputSolver` |
| `COIL_MAGNETOSTATICS` | Current conduction chained into magnetostatics: the field of a coil's solved current distribution. | `CoilCurrentSolver`, `MagnetoDynamicsSolver`, `MagnetoDynamicsCalcFieldsSolver`, `SaveScalarsSolver`, `ResultOutputSolver` |
| `LINEAR_ELASTICITY` | Linear elastic stress and strain. | `StressSolver`, `ResultOutputSolver` |

`LINEAR_ELASTICITY` is a scaffold: no condition targets it yet, and the material model has no Poisson ratio.

Select a physics with [PhysicsSelection][femforge.elmer.config.PhysicsSelection]. The config's frame decides the form a selection solves as, and a physics with no form in the frame raises when you build the config. You never select `MAGNETOSTATICS_2D` yourself; each frame's page lists the physics it solves and as what, see [Frames](frames/index.md).

## Conditions

| Class | Physics | Target | What it sets |
| --- | --- | --- | --- |
| [Magnetization][femforge.physical.Magnetization] | `MAGNETOSTATICS` | body | The magnetization direction. The magnitude B_r/μ₀ comes from the material. |
| [CurrentDensity][femforge.physical.CurrentDensity] | `MAGNETOSTATICS_2D` | body | `value` [A/m²]: a uniform signed azimuthal current density, the homogenized drive of a stranded coil. |
| [FixedTemperature][femforge.physical.FixedTemperature] | `THERMAL` | boundary | `value` [K]: a fixed temperature. |
| [HeatFlux][femforge.physical.HeatFlux] | `THERMAL` | boundary | `value` [W/m²]: a heat flux through the surface. |
| [Convection][femforge.physical.Convection] | `THERMAL` | outer shell | `coefficient` [W/(m²·K)] and `ext_temperature` [K]: Newton cooling to an ambient. |
| [HeatSource][femforge.physical.HeatSource] | `THERMAL` | body | `power_density` [W/m³]. With `temperature_coefficient` and `reference_temperature`, the source scales linearly with the local temperature. |
| [FixedPotential][femforge.physical.FixedPotential] | `CURRENT_CONDUCTION` | boundary | `value` [V]: an electrode potential. The run also integrates the electrode current. |
| [JouleHeat][femforge.physical.JouleHeat] | `JOULE_HEATING` | body | Heats the body with its solved Joule losses. It takes no value. |
| [CoilDrive][femforge.physical.CoilDrive] | `COIL_MAGNETOSTATICS` | body | `current` [A]: the total current of a massive coil. Bodies that share one `CoilDrive` instance form one coil. |
| [CoilTerminal][femforge.physical.CoilTerminal] | `COIL_MAGNETOSTATICS` | outer shell | `role`: the coil's `start` or `end` electrode. Positive current flows from start to end. |
| [CalculateMagneticForce][femforge.physical.CalculateMagneticForce] | `MAGNETOSTATICS` | body | Lumps the magnetic force, and optionally the torque about `torque_origin` and `torque_axis`, over the matched bodies. |

Every value is a pint quantity of the dimensionality in brackets. Build it with `ff.quantity(300, "kelvin")` or `300 * ff.U.K`. See [Materials](materials.md#units).

Reverse a coil by swapping its terminal roles, and keep `current` positive.

## Where conditions land

A body condition applies to every body its tag matches, and its `section` names the sif block it lands in:

- `Magnetization` goes into the magnet's own Material block, where the magnetic solver reads it.
- `CoilDrive` and `CalculateMagneticForce` become Elmer Components. One condition instance gathers every body it matches into one Component.
- The rest become one Body Force block per body.

You never tag a surface. A boundary condition on a body's tag lands on every boundary group that body borders, both its interfaces with other bodies and its outer shell. `Convection` and `CoilTerminal` land on the outer shell only (`<BODY>__EXT`): a film coefficient on an interior interface would bypass the conduction path, and a coil terminal is the electrode's exposed end face. When several conditions reach one surface, they merge into one boundary block.

For example, with a copper body named `Trace` on an FR4 body named `Board`:

| The `Board` tag carries | Lands on `BOARD__TRACE` | Lands on `BOARD__EXT` |
| --- | --- | --- |
| `FixedTemperature` | yes | yes |
| `Convection` | no | yes |

Some conditions need no authoring:

- The magnetic physics pin the potential to zero on every outer shell, a far-field condition.
- A section solved about an axis pins the potential to zero on the symmetry axis (`<BODY>__AXIS`), which is exact there; see [Axisymmetric](frames/axisymmetric.md).
- Every magnet body gets its own force Component, named after its body group. An authored `CalculateMagneticForce` that matches the magnet replaces it.

## Which conditions a physics reads

A coupled physics also reads the conditions of the physics it couples, so the same tags drive the standalone and the coupled solves.

| Solved physics | Reads the conditions of |
| --- | --- |
| `JOULE_HEATING` | `JOULE_HEATING`, `CURRENT_CONDUCTION`, `THERMAL` |
| `COIL_MAGNETOSTATICS` | `COIL_MAGNETOSTATICS`, `MAGNETOSTATICS` |
| `MAGNETOSTATICS_2D` | `MAGNETOSTATICS_2D`, `MAGNETOSTATICS` |
| Any other | Its own |

`COIL_MAGNETOSTATICS` leaves `FixedPotential` inert: it sources the coil from `CoilDrive` and `CoilTerminal`.

## Checks before the solve

The config rejects a condition its frame cannot solve as soon as you build or assign it: a condition whose physics has no form in the frame, a direction with no form in solve coordinates, or torque in a frame that lumps none. Each frame's page lists its rules; see [Frames](frames/index.md).

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
- Set the frame before you customize. A stack customized for one frame raises under the other, since the coordinate system differs.
- Every solver and the Simulation settings take `extra_keywords` for a sif keyword femforge has no field for. [GenericSolver][femforge.elmer.config.GenericSolver] adds a whole solver block femforge has no model for.

The solver models and their keyword tables live in the [Elmer solvers API](../reference/elmer-config.md).

## Reference

- [Physical API](../reference/physical.md): the conditions, `EntityTag` and `DomainConfig`.
- [Elmer API](../reference/elmer.md): `Physics` and the backends.
- [Elmer solvers API](../reference/elmer-config.md): `PhysicsSelection`, `PhysicsConfig`, the presets and every solver.
