# Optimization

The optimization seam turns free parameters into an objective number through ordinary driver runs. A [Parameterization][femforge.optimize.Parameterization] maps a point in parameter space to a [SimulationConfig][femforge.config.SimulationConfig], the driver solves it, an [Objective][femforge.optimize.Objective] reduces the [Result][femforge.run.Result] to a float, and a [Study][femforge.optimize.Study] records every point. The pipeline knows nothing about optimization, so every point is a normal run with its own bundle, run id and provenance.

## Run a grid search

```python
import femforge as ff

base = ff.SimulationConfig(
    mesh=ff.MeshConfig(step="models/actuator.step", global_mesh_size=2 * ff.U.mm),
    domain=ff.DomainConfig(
        materials=[ff.materials.Air(), ff.materials.N52()],
        tags=[
            ff.EntityTag(
                selector={ff.TagKey.MAGNETIZATION: "N"},
                conditions=[ff.Magnetization(direction=ff.Direction(0, 0, 1))],
            ),
            ff.EntityTag(selector={ff.TagKey.NAME: "mover"}, conditions=[ff.ForceComponent(label="mover")]),
            ff.EntityTag(selector={ff.TagKey.NAME: "gap"}, mesh_size=1 * ff.U.mm),
        ],
    ),
)

parameterization = ff.optimize.Parameterization(
    base=base,
    knobs=[
        ff.optimize.GlobalMeshSize(name="global", low=0.001, high=0.004),
        ff.optimize.RegionMeshSize(name="gap", tag="GAP", low=0.0002, high=0.001),
    ],
)

search = ff.optimize.GridSearch(
    parameterization=parameterization,
    grid={"global": [0.004, 0.002], "gap": [0.001, 0.0005, 0.00025]},
    objective=ff.optimize.NetForceMagnitude(),
)

study = search.run(ff.Driver(runs_dir="runs", backend=ff.elmer.DockerElmer()))

best = study.best()
print(best.point, best.objective, best.run_id)
print(study.to_dataframe())
```

The grid walks the Cartesian product of its value lists, here six points in grid order. Run the script again and every point returns its finished bundle without solving, because each run id comes from the config alone.

## The objects

| Object | Role |
|---|---|
| [Parameterization][femforge.optimize.Parameterization] | Holds a fixed `base` config and the knobs that perturb it. `to_config(point)` builds one config and raises when the point's keys differ from the knob names; `space()` returns each knob's bounds. |
| [Knob][femforge.optimize.Knob] | One free variable: a `name`, inclusive `low` and `high` bounds, and an `apply` that returns a changed copy of a config. |
| [ParamSpec][femforge.optimize.ParamSpec] | The declared bounds of one knob, as `space()` returns them. |
| [Objective][femforge.optimize.Objective] | A protocol: any callable from a `Result` to a float. |
| [GridSearch][femforge.optimize.GridSearch] | Walks the grid through a driver and returns a `Study`. |
| [DriverLike][femforge.optimize.DriverLike] | The protocol `GridSearch.run` takes: anything with `evaluate(config) -> Result`. |
| [Study][femforge.optimize.Study] | The records in evaluation order, with `best()` and `to_dataframe()`. |
| [StudyRecord][femforge.optimize.StudyRecord] | One point: its coordinates, run id, objective and the full typed result. |

`best()` takes the largest objective, or the smallest with `maximize=False`. `to_dataframe()` is where units leave: one row per record with the knob columns, `run_id`, `objective`, `status`, the force and torque magnitudes, the force components, `flux_density_max`, `residual` and `wall_time`, all as bare SI floats.

## Knobs and objectives

| Knob | What it sets |
|---|---|
| [GlobalMeshSize][femforge.optimize.GlobalMeshSize] | `MeshConfig.global_mesh_size`, with the knob value in metres. |
| [RegionMeshSize][femforge.optimize.RegionMeshSize] | The `mesh_size` of the [EntityTag][femforge.physical.EntityTag] whose `name` equals `tag`, such as `GAP` for a `{Name: "gap"}` selector, with the knob value in metres; it raises when the base config has no such tag. |

| Objective | Score |
|---|---|
| [NetForceMagnitude][femforge.optimize.NetForceMagnitude] | The net force magnitude on the moving array, in N; `-inf` when the run produced no force. |
| [PhaseResistanceFromPower][femforge.optimize.PhaseResistanceFromPower] | The mean per-phase resistance `phases·V²/P` of a current-conduction run, from its Joule power; `nan` without a positive Joule power. |

Write your own knob as a `Knob` subclass with a `kind` literal and an `apply` that copies the config. Never mutate the config passed in: every point starts from the same base.

```python
from typing import Literal

import femforge as ff
from femforge.optimize.max_current import scale_fixed_potentials


class DriveScale(ff.optimize.Knob):
    """Multiplies every FixedPotential drive by the knob value."""

    kind: Literal["drive_scale"] = "drive_scale"

    def apply(self, config: ff.SimulationConfig, value: float) -> ff.SimulationConfig:
        return scale_fixed_potentials(config, value)


knob = DriveScale(name="drive", low=0.5, high=2.0)
```

## How a sweep runs through the driver

Each point becomes a config, and [Driver.evaluate][femforge.run.Driver.evaluate] runs it like any other. The run id is the [cache_key][femforge.run.cache_key] of that config, so points that differ get separate bundles, and a point you already solved comes back from its bundle. [Driver and runs](driver.md) covers identity and reuse.

Configs in a sweep differ, but their meshes need not. The mesh key leaves out conditions, material properties, physics and run metadata, so a sweep over drives or material properties meshes the geometry once when the driver has an artifact cache, and every later point fetches that mesh. A mesh-size knob changes the mesh key, so each of its values meshes on its own. [Artifacts and retention](artifacts.md) covers the cache and the keys.

`GridSearch.run` accepts any `DriverLike`, so a stand-in tests a search without gmsh or Elmer:

```python
import femforge as ff


class FakeDriver:
    """Scores a finer mesh with a larger force, without solving."""

    def evaluate(self, config: ff.SimulationConfig) -> ff.Result:
        force = ff.quantity(ff.Vec3(1.0 / config.mesh.global_mesh_size.m_as("m"), 0.0, 0.0), ff.U.N)
        return ff.Result(frame=ff.Solid(), status="ok", force=force)


parameterization = ff.optimize.Parameterization(
    base=ff.SimulationConfig(mesh=ff.MeshConfig(step="models/actuator.step", global_mesh_size=1.0 * ff.U.m)),
    knobs=[ff.optimize.GlobalMeshSize(name="global", low=0.2, high=1.0)],
)
search = ff.optimize.GridSearch(
    parameterization=parameterization,
    grid={"global": [1.0, 0.5, 0.2]},
    objective=ff.optimize.NetForceMagnitude(),
)

study = search.run(FakeDriver())
assert study.best().point == {"global": 0.2}
```

A stand-in stamps no run id, so `GridSearch` derives each record's run id from the config's cache key.

## Where results land

| Where | What |
|---|---|
| The `Study` | In memory: every point, its objective and its typed result. |
| `runs_dir/<run_id>/` | One bundle per point, with its manifest, `result.json` and the files the bundle policy keeps. |
| The run index | One row per run when the driver has a telemetry root, read back with `femforge runs ls`. |
| Sweep progress | The sweeps table and the sweep event journals, when you record the sweep with [SweepJournal][femforge.telemetry.SweepJournal]. |

`GridSearch` writes no sweep progress by itself. To see a sweep in `femforge sweeps ls|show`, wrap the driver: open a `SweepJournal`, journal each point's transitions, and evaluate inside [sweep_point][femforge.telemetry.sweep_point], which binds the sweep id and the point key onto every log record and onto the run's index row.

```python
import femforge as ff
from femforge.telemetry import SweepJournal, SweepState, sweep_point


class JournalledDriver:
    """A DriverLike that records each point it evaluates as a sweep event."""

    def __init__(self, driver: ff.Driver, journal: SweepJournal) -> None:
        self.driver = driver
        self.journal = journal

    def evaluate(self, config: ff.SimulationConfig) -> ff.Result:
        key = ff.run.cache_key(config)[:12]
        self.journal.event(key, SweepState.RUNNING)
        with sweep_point(self.journal.sweep_id, key):
            result = self.driver.evaluate(config)

        self.journal.event(key, SweepState.DONE, point_name=result.run_id)
        return result


def run_sweep(search: ff.optimize.GridSearch, total: int) -> ff.optimize.Study:
    """Run ``search`` with its progress recorded under the telemetry root ``telemetry``."""
    journal = SweepJournal(ff.Telemetry("telemetry"), sweep_id="gap-sweep")
    journal.start("Gap mesh sweep", total=total)
    try:
        return search.run(JournalledDriver(ff.Driver(runs_dir="runs", telemetry="telemetry"), journal))
    finally:
        journal.finish()
```

Then `femforge sweeps show gap-sweep --telemetry telemetry` prints the counts, the points and any failures, and `femforge runs ls --sweep gap-sweep` lists the runs. [Telemetry](telemetry.md) covers the store and [CLI](cli.md) the commands.

## Maximum current search

`find_max_current` in `femforge.optimize.max_current` finds the largest phase current whose steady temperature stays at or below a limit. It needs a physics stack that exports the maximum temperature, which the result carries as `temperature_max`.

```python
from collections.abc import Callable

import femforge as ff
from femforge.optimize.max_current import MaxCurrentSearch, VoltageDrive, find_max_current, seed_from_result


def rated_current(driver: ff.Driver, base: ff.SimulationConfig, current_of: Callable[[ff.Result], ff.Quantity]) -> MaxCurrentSearch:
    """The thermal current limit of ``base`` at a 120 °C coil limit and a 20 °C ambient."""
    ambient = ff.quantity(293.15, "kelvin")
    limit = ff.quantity(393.15, "kelvin")

    reference = driver.evaluate(base)
    reference_current = current_of(reference)
    seed = seed_from_result(reference, reference_current=reference_current, ambient_temperature=ambient, limit_temperature=limit)

    return find_max_current(
        evaluate=driver.evaluate,
        base_config=base,
        seed=seed,
        drive=VoltageDrive(current_of=current_of, reference_current=reference_current),
        limit_temperature=limit,
        ambient_temperature=ambient,
    )
```

The search works in the coordinate x = I², where the temperature rise is close to a straight line:

- Steady conduction is linear in heat, and Joule heat is quadratic in current. For a temperature-independent conductivity the rise is exactly linear in x; the σ(T) feedback bends it gently.
- x = 0 is an exact lower anchor at no cost: zero current holds ambient.
- The seed is the same line through one reference solve: `I_seed = I_ref·√(ΔT_limit/ΔT_ref)`. On a linear problem the seed is the root, and one verification solve accepts it.

Each step picks the next current by interpolation, never by blind bisection:

| Bracket | Next step |
|---|---|
| No upper bracket yet | A secant step: project the line through the lower anchors to the limit, capped at `expand` times the current per step. |
| Upper bracket with a steady temperature | Illinois-damped false position: when the same side of the bracket moves twice in a row, the anchor on the other side has its residual halved, and the proposal stays just inside the bracket. |
| Upper bracket at a runaway | Project from the lower anchors, and bisect when the projection leaves the bracket, because a runaway carries no residual. |

The search accepts once a steady temperature lands within `temperature_rtol` of the allowed rise below the limit, which usually takes two to four solves, or once the bracket narrows to `bracket_rtol` of its upper current. When `max_steps` runs out first, it returns the bracket with `converged=False`, and it raises when no step landed on one side of the limit.

A drive realizes each target current:

| Drive | Excitation | Solves per step |
|---|---|---|
| `DirectDrive(configure)` | `configure(config, current)` rebuilds the config for a prescribed current. | One. |
| `VoltageDrive(current_of, reference_current)` | Scales every `FixedPotential` by the iteration `V ← V·I_target/I_measured` until the measured current lands within `current_rtol`, starting from the amps-per-scale gain of the last step that reached its target. | One or more, at most `max_inner`. |

A diverged solve marks a runaway: no steady state exists at that current. So does a voltage drive whose current falls short of the target on every inner solve. Any other failed solve raises `SolveFailedError`, since a tool error says nothing about the physics.

The returned `MaxCurrentSearch` holds `max_current`, the largest current confirmed at or under the limit, `upper_bracket_current`, `converged`, `runaway`, every `SearchStep`, and `accepted`, the step whose solve set `max_current`, so you inspect that steady state without solving again.

## Reference

- [femforge.optimize](../reference/optimize.md): every object on this page except the maximum current search.
- [femforge.run](../reference/run.md): the driver, the result and the cache key.
