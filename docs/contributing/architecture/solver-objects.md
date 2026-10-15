# Solver objects

Elmer is the solver backend femforge implements today. femforge models every block of Elmer's solver input file (the sif) as a typed pydantic model whose fields are its sif keywords. A preset builds one such stack per physics, the sif writer turns the stack and the mesh groups into a sif, and two parsers read ElmerSolver's output back into typed values. This page covers the model tree, the field-to-keyword mapping, the writer, the checks a solve runs before it writes, and the parsers.

## The solver model tree

`src/femforge/elmer/config` groups the models by physics, and its `__init__.py` re-exports every public name, so callers import from `femforge.elmer.config` (`src/femforge/elmer/config/__init__.py:5-32`).

| Subpackage | Holds |
|---|---|
| `common` | the keyword base, the solver base and its mixins, the `Simulation` and `Constants` blocks, the operating point, the generic solver, [PhysicsConfig][femforge.elmer.config.PhysicsConfig], the presets and the `physics` config area |
| `electromagnetism` | 3D and axisymmetric magnetostatics, the field post-processor, current conduction, the coil current source, electrostatics |
| `thermal` | heat conduction |
| `mechanics` | linear elasticity |
| `output` | VTU output and scalar reductions |

The hierarchy has one base and three mixins:

- [SifKeywordModel][femforge.elmer.config.SifKeywordModel]: fields carry sif keywords, and `to_elmer()` reflects them out (`src/femforge/elmer/config/common/keywords.py:15-41`)
    - [SolverBase][femforge.elmer.config.SolverBase]: `kind`, `equation`, `procedure`, `variable` and the other keywords every solver block has (`src/femforge/elmer/config/common/solver_base.py:172-191`)
    - [SimulationSettings][femforge.elmer.config.SimulationSettings] and [Constants][femforge.elmer.config.Constants]: the `Simulation` and `Constants` blocks
- Mixins a concrete solver composes beside `SolverBase`:
    - [LinearSystem][femforge.elmer.config.LinearSystem]: the `Linear System *` keywords (`src/femforge/elmer/config/common/solver_base.py:138-152`)
    - [NonlinearSystem][femforge.elmer.config.NonlinearSystem]: the `Nonlinear System *` keywords (`src/femforge/elmer/config/common/solver_base.py:155-163`)
    - [SteadyState][femforge.elmer.config.SteadyState]: `Steady State Convergence Tolerance` (`src/femforge/elmer/config/common/solver_base.py:166-169`)

This table is generated from the classes at build time:

```python exec="true" session="solvers"
from enum import Enum

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from femforge.common import get_map_key
from femforge.elmer.config import (
    CoilCurrentSolver,
    Constants,
    ElectrostaticsSolver,
    GenericSolver,
    HeatSolver,
    LinearSystem,
    MagnetoDynamics2DSolver,
    MagnetoDynamicsCalcFieldsSolver,
    MagnetoDynamicsSolver,
    NonlinearSystem,
    ResultOutputSolver,
    SaveScalarsSolver,
    SimulationSettings,
    StatCurrentSolver,
    SteadyState,
    StressSolver,
    physics_config,
)
from femforge.elmer.physics import Physics


def default(field: FieldInfo) -> str:
    if field.is_required():
        return "required"

    value = field.default
    if value is None:
        return "unset"

    if isinstance(value, Enum):
        value = value.value

    return f"`{value}`"


def keyword_table(*models: type[BaseModel]) -> None:
    for model in models:
        print(f"**{model.__name__}**")
        print()
        print("| Field | Sif keyword | Default |")
        print("| --- | --- | --- |")
        for name, field in model.model_fields.items():
            keyword = get_map_key(field)
            if keyword is not None:
                print(f"| `{name}` | `{keyword}` | {default(field)} |")
        print()


SOLVERS = [
    MagnetoDynamicsSolver,
    MagnetoDynamics2DSolver,
    MagnetoDynamicsCalcFieldsSolver,
    CoilCurrentSolver,
    StatCurrentSolver,
    ElectrostaticsSolver,
    HeatSolver,
    StressSolver,
    ResultOutputSolver,
    SaveScalarsSolver,
    GenericSolver,
]

print("| Solver | Sif label | Procedure | Mixins | Required material keywords | Conductors only |")
print("| --- | --- | --- | --- | --- | --- |")
for solver in SOLVERS:
    fields = solver.model_fields
    mixins = ", ".join(mixin.__name__ for mixin in (LinearSystem, NonlinearSystem, SteadyState) if issubclass(solver, mixin)) or "none"
    keys = ", ".join(f"`{key.value}`" for key in sorted(solver.required_material_keys)) or "none"
    only = "yes" if solver.conductor_only else "no"
    print(f"| `{solver.__name__}` | {default(fields['kind'])} | {default(fields['procedure'])} | {mixins} | {keys} | {only} |")
```

Two class variables carry the cross-object rules, and as class variables they never reach the sif. `required_material_keys` names the material keywords every body the solver acts on must supply (`src/femforge/elmer/config/common/solver_base.py:178-179`). It is typed as `ElmerMaterialKeyword`, the same enum a material's properties emit, so the requirement and the emission share one definition. `conductor_only` restricts the solver's equation to conductor bodies, because a conduction variable solved over dielectrics too puts about 21 decades of conductivity contrast into one matrix: singular for a direct factorization and a stall for preconditioned CG (`src/femforge/elmer/config/common/solver_base.py:181-183`).

### The simulation, constants and operating-point blocks

| Model | Becomes | Notes |
|---|---|---|
| [SimulationSettings][femforge.elmer.config.SimulationSettings] | the `Simulation` block | Defaults to a 3D Cartesian steady-state run. `Post File` and `Output File` stay unset: setting `Post File` beside the VTU output solver triggers Elmer's VTU output hack and its log noise, and nothing reads the restart file, which grows to gigabytes (`src/femforge/elmer/config/common/simulation.py:62-98`). |
| [Constants][femforge.elmer.config.Constants] | the `Constants` block | Every constant is unset until a preset activates it, and an unset one stays out of the sif (`src/femforge/elmer/config/common/constants.py:15-25`). |
| [OperatingPoint][femforge.elmer.config.OperatingPoint] | no block | The temperature material property functions are evaluated at when they are stripped to SI floats (`src/femforge/elmer/config/common/operating_point.py:10-25`). |

`CoordinateSystem.per_radian` marks the two symmetric 2D systems, where Elmer integrates per radian and lumped outputs need a 2π factor (`src/femforge/elmer/config/common/simulation.py:30-33`).

### PhysicsConfig and the presets

[PhysicsConfig][femforge.elmer.config.PhysicsConfig] is the whole stack: the ordered solvers, the simulation block, the constants and the operating point (`src/femforge/elmer/config/common/physics_config.py:47-62`). `solvers` is a list of `SolverUnion`, a union discriminated on `kind`, so every concrete field survives a dump and a reload. A plain `list[SolverBase]` would drop them, and the stack could not ride inside `SimulationConfig`, the run identity or the manifest (`src/femforge/elmer/config/common/physics_config.py:28-44`). `solver(kind)` returns the live instance of one solver class for typed editing (`src/femforge/elmer/config/common/physics_config.py:64-69`).

[physics_config][femforge.elmer.config.physics_config] builds the preset for one [Physics][femforge.elmer.Physics] with an exhaustive `match`, so a new physics is a type error until it has a preset (`src/femforge/elmer/config/common/presets.py:34-139`). The table lists each preset as `physics_config` builds it. `magnetostatics_2d` is the form `magnetostatics` solves as under `Axisymmetric`, never an authored choice, and `solved_stack` switches the thermal preset to `Axi Symmetric` under that frame (`src/femforge/elmer/config/common/presets.py:24-31`).

```python exec="true" session="solvers"
print("| Physics | Solvers, in order | Coordinate system | Constants set |")
print("| --- | --- | --- | --- |")
for physics in Physics:
    stack = physics_config(physics)
    solvers = ", ".join(type(solver).__name__ for solver in stack.solvers)
    constants = ", ".join(f"`{key}`" for key, _ in stack.constants.to_elmer().items()) or "none"
    print(f"| `{physics.value}` | {solvers} | `{stack.simulation.coordinate_system.value}` | {constants} |")
```

The comments in the presets record why each stack looks the way it does. Three matter most:

- `COIL_MAGNETOSTATICS` chains CoilSolver into WhitneyAV and drives the field solve from CoilSolver's elemental current, because a nodal current source dilutes the drive about twofold on thin conductors (`src/femforge/elmer/config/common/presets.py:96-104`).
- `MAGNETOSTATICS_2D` pins CalcFields' potential variable and keeps nodal fields on, because turning them off also suppresses the nodal force variable the force reduction reads (`src/femforge/elmer/config/common/presets.py:45-55`).
- `LINEAR_ELASTICITY` has a complete stack, but its per-body wiring in `Sim` is a stub (`src/femforge/elmer/config/common/presets.py:132`, `src/femforge/elmer/sim.py:473-479`).

Three functions adjust or read a whole stack:

| Function | Does | Where |
|---|---|---|
| [prepare_for_mpi][femforge.elmer.config.prepare_for_mpi] | switches CoilSolver's serial umfpack solve to MUMPS and raises for any other direct solve, which is unvalidated under partitioning | `src/femforge/elmer/config/common/physics_config.py:82-88` |
| `needs_halobc` | asks for halo elements only when the stack holds CoilSolver: its boundary-terminated electrodes need them, and elsewhere they skew the force lumping | `src/femforge/elmer/config/common/physics_config.py:91-93` |
| `convergence_targets` | the iteration cap and tolerance of each solver's nonlinear and steady-state loop, keyed the way Elmer tags its convergence lines | `src/femforge/elmer/config/common/physics_config.py:96-115` |

[Conditions and physics](../../user-guide/conditions.md#customize-the-solver-stack) shows the user side of editing a stack.

## Fields are sif keywords

A field names its sif keyword with [Map][femforge.common.Map] metadata on its `Annotated` type, as in `max_output_level: Annotated[int, Map("Max Output Level")]` (`src/femforge/elmer/config/common/simulation.py:65`). [mapped_items][femforge.common.mapped_items] reflects the `(keyword, value)` pairs back out and skips every field whose value is None (`src/femforge/common/pydantic.py:34-45`). No table beside the models lists the keywords, so a keyword cannot drift from its field.

`SifKeywordModel.to_elmer()` builds the keyword dict on demand and never stores it (`src/femforge/elmer/config/common/keywords.py:27-41`):

- A field left at None stays out of the sif, so Elmer keeps its own default for that keyword.
- A list expands to a numbered keyword family, such as `Vector Field 1` to `Vector Field n`.
- `extra_keywords` merges last and wins, so any Elmer keyword the typed fields lack stays authorable without a subclass (`src/femforge/elmer/config/common/keywords.py:23-25`).
- Fixed vocabularies are StrEnums whose values are the exact sif strings, so the value written is the enum member itself (`src/femforge/elmer/config/common/solver_base.py:15-96`).

```python
from femforge.elmer.config import MagnetoDynamicsSolver, physics_config
from femforge.elmer.physics import Physics

stack = physics_config(Physics.MAGNETOSTATICS)
solver = stack.solver(MagnetoDynamicsSolver)
solver.linear_system_max_iterations = 2000
solver.extra_keywords["Linear System GCR Restart"] = 50

block = solver.to_elmer()
assert block["Linear System Max Iterations"] == 2000
assert "Variable" not in block
```

A keyword several solvers share is declared once as a module-level `Annotated` alias, such as `LinearMaxIterationsField`, and every solver that overrides its default reuses the alias (`src/femforge/elmer/config/common/solver_base.py:99-135`). The alias also carries the field description, because a concrete override would reset a description that came from a docstring (`src/femforge/elmer/config/common/solver_base.py:143`). `kind` has no `Map`: it names the solver in the `SolverUnion` and doubles as the sif Solver block's label (`src/femforge/elmer/sim.py:229-234`).

The mesh side follows the same rule: `GmshOptions.apply` reflects the same `Map` tags into gmsh option setters (`src/femforge/meshing/gmsh_options.py:114-133`).

### Keyword tables

The tables below are generated from the models at build time. Rows follow the field order of the model, which is also the order `to_elmer` writes the keywords in. "unset" means the field defaults to None and the keyword stays out of the sif. A preset can override a default: the thermal stack, for instance, builds its own SaveScalars reductions.

#### Simulation and constants

```python exec="true" session="solvers"
keyword_table(SimulationSettings, Constants)
```

#### Electromagnetism

```python exec="true" session="solvers"
keyword_table(MagnetoDynamicsSolver, MagnetoDynamics2DSolver, MagnetoDynamicsCalcFieldsSolver, CoilCurrentSolver, StatCurrentSolver, ElectrostaticsSolver)
```

#### Thermal

```python exec="true" session="solvers"
keyword_table(HeatSolver)
```

#### Mechanics

```python exec="true" session="solvers"
keyword_table(StressSolver)
```

#### Output

```python exec="true" session="solvers"
keyword_table(ResultOutputSolver, SaveScalarsSolver)
```

#### Generic

[GenericSolver][femforge.elmer.config.GenericSolver] is the escape hatch for a solver the hierarchy does not model: set `equation` and `procedure`, and author the rest through `extra_keywords` and the shared typed knobs (`src/femforge/elmer/config/common/generic.py:10-17`).

```python exec="true" session="solvers"
keyword_table(GenericSolver)
```

## The sif writer

`src/femforge/elmer/sif.py` follows pyelmer's public API shape as an independent implementation (`NOTICE:6-7`). It writes the same text pyelmer writes, always with LF line endings (`src/femforge/elmer/sif.py:1`). `tests/elmer/test_sif_writer.py` compares its output with golden files pyelmer wrote, and checks the LF endings and the registry semantics below (`tests/elmer/test_sif_writer.py:1`).

| Behavior | Why | Where |
|---|---|---|
| Constructing a block under an existing name with the same arguments returns the registered block; different arguments raise | a block can be requested from several places, and a name clash is a wiring error | `src/femforge/elmer/sif.py:49-58` |
| Blocks initialize in `__new__`, with no `__init__` | Python must not re-run an initializer on a block the registry returns, which would reset its id and references | `src/femforge/elmer/sif.py:61-62` |
| Section ids are assigned 1 to N in insertion order at write time | a block references another by id, and the id exists only once every block is registered | `src/femforge/elmer/sif.py:432-436` |
| The document writes Header, Simulation, Constants, then Equation, Solver, Material, Body, Boundary Condition, Body Force and Component sections | the reference layout | `src/femforge/elmer/sif.py:393-412` |
| `boundaries.yml` and `components.yml` map each assigned id to its block name | the parsers join Elmer's id-named scalar columns back to names | `src/femforge/elmer/sif.py:420-430` |

## Sim and generate()

`Sim` turns the mesh groups and a solver stack into a sif in two phases (`src/femforge/elmer/sim.py:59-63`):

1. **The constructor is cheap.** It maps the physics through the frame, takes the given stack or the preset, and checks the stack and every group's conditions against the frame. It builds no sif objects (`src/femforge/elmer/sim.py:65-104`).
2. **`generate()` builds the sif graph** from the possibly edited stack: the simulation and constants blocks, then solvers, equations, materials, bodies and boundaries in dependency order, then `validate()` (`src/femforge/elmer/sim.py:108-129`).
3. **`write(dir)`** writes the start-info file, the sif and the two id maps, and requires `generate()` first (`src/femforge/elmer/sim.py:483-500`).

The driver's `build_sim` stage constructs `Sim` from the resolved stack and applies `prepare_for_mpi` for a partitioned solve; `write_sif` calls `generate().write()` (`src/femforge/run/driver.py:636-662`). `Sim` takes no config object: its bodies come from the mesh groups, and it owns only its solver stack.

### Bodies, boundaries and components

| Source | sif block | Where |
|---|---|---|
| each physical group | one `Body` targeting the group's id | `src/femforge/elmer/sim.py:305-306` |
| the stack | `Main Equation` with every solver, plus a second equation without the `conductor_only` solvers when a non-conductor body exists | `src/femforge/elmer/sim.py:236-256` |
| a coil stack | CoilSolver's equation covers only the bodies with a `CoilDrive`, because CoilSolver fails on a body outside every Component; WhitneyAV and CalcFields stay in both equations, because splitting them corrupts CalcFields' degree-of-freedom mapping | `src/femforge/elmer/sim.py:246-251` |
| each distinct material | one shared `Material` | `src/femforge/elmer/sim.py:263-287` |
| a magnet body | a `Material` of its own carrying the `Magnetization` vector, because WhitneyAV reads it from the material; a missing remanence or direction leaves a commented marker for `validate()` to raise on | `src/femforge/elmer/sim.py:445-471` |
| body-target conditions | one `Body Force` per body, without `Magnetization`, `CoilDrive` and `ForceComponent` | `src/femforge/elmer/sim.py:329-353` |
| each `CoilDrive` instance | one coil `Component` over every body that carries it | `src/femforge/elmer/sim.py:296-297`, `src/femforge/elmer/sim.py:355-357` |
| each `ForceComponent` instance, and each magnet without one | one force `Component`; the automatic one is named after the group | `src/femforge/elmer/sim.py:299-303`, `src/femforge/elmer/sim.py:359-365` |
| boundary-target conditions of the bordering bodies | one merged `Boundary Condition` per surface group, because Elmer gives each boundary element one section and silently ignores a second | `src/femforge/elmer/sim.py:377-397` |
| a magnetic stack's external shell | the far field `AV {e} = 0`, or `Potential = 0` in 2D; the 2D symmetry axis gets the exact `Potential = 0` | `src/femforge/elmer/sim.py:427-439` |

A component groups bodies by condition identity, so one `CoilDrive` authored on a phase's tag reaches every body of that phase as one Component.

## Checks before the solve

`validate()` runs at the end of `generate()` and covers what pydantic cannot see across objects (`src/femforge/elmer/sim.py:133-225`). It collects every error into one `ValueError` and issues each smell as a `SolverConfigWarning` (`src/femforge/elmer/sim.py:46-47`).

| Check | Outcome | Where |
|---|---|---|
| Each body's material emits every keyword its solvers require: the keys of every solver, plus the keys of `conductor_only` solvers on conductors; an equivalent keyword counts, such as an `H-B Curve` for `Relative Permeability` | error | `src/femforge/elmer/sim.py:143-160` |
| No emitted material value is still a pint quantity | error | `src/femforge/elmer/sim.py:162-167` |
| Every magnet under a magnet-driven physics has a `Magnetization` with a direction | error | `src/femforge/elmer/sim.py:169-175` |
| A coil solve has at least one `CoilDrive` | warning | `src/femforge/elmer/sim.py:177-184` |
| A physics that needs a boundary condition to be well posed resolved one | warning | `src/femforge/elmer/sim.py:26-28`, `src/femforge/elmer/sim.py:186-193` |
| A force Component has CalcFields with nodal forces on, and a SaveScalars to export it | warning | `src/femforge/elmer/sim.py:195-222` |

The constructor raises earlier for an unknown physics and for a stack or condition that cannot solve the frame (`src/femforge/elmer/sim.py:86-102`). Before a partitioned solve, the driver also warns when a coil stack computes nodal forces, because the halo elements it then partitions with corrupt the Component force lumping (`src/femforge/run/driver.py:671-680`).

## The solver-log parser

`SolverLogParser.feed` turns one ElmerSolver output line into a typed event, or None (`src/femforge/elmer/solver_log.py:156-188`):

| Event | From | Where |
|---|---|---|
| `ConvergenceSample` | a `ComputeChange` line: solver, loop, iteration, norm, relative change | `src/femforge/elmer/solver_log.py:26-46` |
| `LinearResidual` | a linear iteration line, only when the parser was built with `capture_linear=True` | `src/femforge/elmer/solver_log.py:49-60` |
| `SolverMessage` | a `WARNING` or `ERROR` line | `src/femforge/elmer/solver_log.py:63-68` |
| `SolveFinished` | the `SOLVER TOTAL TIME` line | `src/femforge/elmer/solver_log.py:71-79` |

The convergence line's solver tag is the sif `Equation` name lowercased, and some Elmer builds omit it (`src/femforge/elmer/solver_log.py:15-16`), so both sides normalize names with `normalize_solver_key` (`src/femforge/elmer/solver_log.py:100-102`).

[solve_case][femforge.elmer.solve_case] runs ElmerSolver through a backend and feeds every streamed line to the parser (`src/femforge/elmer/runtime/solve.py:30-108`). It writes each convergence sample to `convergence.jsonl` as one flushed line, logs it through a `ConvergenceRenderer` against the stack's targets, and logs each warning and error at its own level. It builds one renderer per solve, so one solve's progress baseline never leaks into the next (`src/femforge/elmer/runtime/solve.py:57-94`). The driver writes the same targets to `convergence_targets.json` immediately before the solve (`src/femforge/run/driver.py:682-692`); [Telemetry](telemetry.md#convergence) covers how that file is read back.

After the solve, [parse_result][femforge.run.parse_result] reads the finished bundle into a [Result][femforge.run.Result] (`src/femforge/run/parse.py:177-232`):

- **Status** comes from `elmersolver.log`: `error` when the log is missing, any line holds `ERROR` or the run never printed `ALL DONE`; `diverged` on a non-convergence marker or a NaN; `ok` otherwise (`src/femforge/run/parse.py:126-174`).
- **Scalars** come from each `.dat` and its `.dat.names` sidecar, reading the last row, because a sweep or a transient run appends rows (`src/femforge/run/parse.py:43-70`).
- **A partitioned solve takes rank 0.** It writes per-rank `.dat.<rank>` files, and the exported values are already globally reduced, so rank 0 stands in for the whole and the parser never sums. Disagreeing ranks log a warning, because they mean a reduction without a global operator (`src/femforge/run/parse.py:90-123`).
- **Forces** read only the global CalcFields sums, so per-Component triples never fold into the total. The per-Component values join `components.yml`, and an `Axisymmetric` frame revolves every force and torque to the full revolution (`src/femforge/run/parse.py:186-217`).

[Results](../../user-guide/results.md#scalars) covers the user side.

## The electrical flux mapping

A current-conduction run measures each phase's terminal current with Elmer's own flux integral, and `src/femforge/run/electrical.py` maps the result back to names:

1. [FixedPotential][femforge.physical.FixedPotential] emits `Save Scalars = Logical True` beside its potential, which marks the electrode for boundary integration (`src/femforge/physical/conditions.py:292-297`).
2. SaveScalars' default first reduction is the `diffusive flux` of `Potential` weighted by `Electric Conductivity`, which integrates σ ∂φ/∂n over each marked boundary to a current (`src/femforge/elmer/config/output/save_scalars.py:34-41`). Its second reduction integrates the Joule loss density to a power P, a cross-check through R = ΔV²/P (`src/femforge/elmer/config/output/save_scalars.py:43-47`).
3. Elmer names each flux column by its boundary index, as in `diffusive flux: potential over bc 3`; [boundary_fluxes][femforge.run.boundary_fluxes] reads the index out of the column name (`src/femforge/run/electrical.py:10-21`).
4. [read_boundary_names][femforge.run.read_boundary_names] reads the index-to-name map `Sim` wrote as `boundaries.yml` (`src/femforge/run/electrical.py:24-31`). The file is flat `int: name` YAML, and the reader splits each line at its first colon.
5. [electrode_currents][femforge.run.electrode_currents] joins the two into a current per named boundary block (`src/femforge/run/electrical.py:34-36`).
6. [phase_currents][femforge.run.phase_currents] keeps, per `PHASE<n>` index in the names, the current of largest magnitude, because Elmer integrates an internal electrode's flux from one side, and the pad side can read about zero (`src/femforge/run/electrical.py:43-54`).
7. [resistance][femforge.run.resistance] divides ΔV by that current and raises on a zero current (`src/femforge/run/electrical.py:57-61`).

`Run.electrode_currents` and `Run.phase_currents` read a bundle through these functions (`src/femforge/run/inspect.py:184-189`).
