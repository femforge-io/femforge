# Config objects

One typed model, [SimulationConfig][femforge.config.SimulationConfig], describes a whole run. It composes one area model per concern, and each pipeline stage reads only the area it needs. This page explains how the areas fit together, how tags carry conditions from the CAD model to the solver input, how the run identity is derived from the config, and which rules keep the config cheap to import.

## The composition root

`src/femforge/config.py` is the composition root: the only module that imports the geometry, mesh, domain and physics areas together (`src/femforge/config.py:7-14`). Every area inherits [ConfigArea][femforge.common.ConfigArea], a leaf base that imports only pydantic, so any package can inherit it without an import cycle (`src/femforge/common/config.py:2-10`).

- [SimulationConfig][femforge.config.SimulationConfig] (`src/femforge/config.py:37-61`)
    - `geometry`: a [GeometryArea][femforge.geometry.GeometryArea] or None (`src/femforge/geometry/config.py`), read by the driver's geometry stage
    - `frame`: a [Frame][femforge.frames.Frame] (`src/femforge/frames`), read by the geometry stage, the mesher, the sif writer and the parser
    - `mesh`: [MeshConfig][femforge.meshing.MeshConfig] (`src/femforge/meshing/config.py`), read by the mesher
    - `domain`: [DomainConfig][femforge.physical.DomainConfig] with materials and [EntityTag][femforge.physical.EntityTag] overrides (`src/femforge/physical/domain.py`), read by the mesher and, through the mesh groups, by the sif writer
    - `physics`: [PhysicsSelection][femforge.elmer.config.PhysicsSelection] (`src/femforge/elmer/config/common/selection.py`), resolved into a [PhysicsConfig][femforge.elmer.config.PhysicsConfig] solver stack
    - `meta`: [RunMeta][femforge.config.RunMeta] (`src/femforge/config.py:20-34`), recorded on the manifest and never hashed

Each area constructs and validates on its own, which is why the JSON schema export lists every area as a model of its own (`src/femforge/schemas.py:47-54`).

`SimulationConfig` turns on `validate_assignment` (`src/femforge/config.py:40-41`), and three field validators cross-check the frame against the domain and the physics selection (`src/femforge/config.py:63-98`). Pydantic validates fields in declaration order, so at construction `domain_fits_frame` and `physics_fits_frame` see the frame declared above them. On reassignment `frame_fits_inputs` sees the other two. `validate_default=True` on `domain` and `physics` runs the check on their defaults as well (`src/femforge/config.py:54-57`).

## Backends read a slice

A backend reads a Protocol slice or plain data, never `SimulationConfig`, so the composition root depends on the backends and no backend depends on it. [Principles](../principles.md#configuration) states the rule; the table shows where it holds.

| Consumer | Reads | Where |
|---|---|---|
| `Mesher` | [SupportsMeshing][femforge.meshing.SupportsMeshing]: `frame`, `mesh`, `domain` | `src/femforge/meshing/config.py:38-50`, `src/femforge/meshing/mesher.py:77-79` |
| `MeshEntity` | the bare `DomainConfig` | `src/femforge/meshing/mesh_entity.py:16-27` |
| [MeshBackend][femforge.meshing.MeshBackend] | a [MeshJob][femforge.meshing.MeshJob]: the mesh spec, the frame and the STEP path | `src/femforge/meshing/backend.py:66-107` |
| `Sim` | the mesh groups, a physics, a solver stack and a frame | `src/femforge/elmer/sim.py:79-86` |
| [ExecutionBackend][femforge.elmer.ExecutionBackend] | directories | `src/femforge/elmer/runtime/backend.py:14-35` |

`SupportsMeshing` is structural, so `SimulationConfig` satisfies it without inheriting anything, and the mesh backend never imports the composition root. The mesh worker goes one step further: [mesh_inputs][femforge.meshing.mesh_inputs] rebuilds a `SupportsMeshing` from the job alone, with bare materials named by their tags and tag selectors without conditions (`src/femforge/meshing/backend.py:121-138`). The mesher therefore cannot read a condition or a material property, which is what lets a cached mesh serve every config that groups the bodies the same way.

Two comments guard the same direction inside the core: `physical/conditions.py` must not import `meshing.config` or `elmer.sim` (`src/femforge/physical/conditions.py:2`), and `physics_config.py` must not import `elmer.sim` (`src/femforge/elmer/config/common/physics_config.py:2`).

## The geometry interface

A [GeometryArea][femforge.geometry.GeometryArea] is a parametric geometry with one method, `build_step(path)`, which writes the model as a STEP in the authored frame (`src/femforge/geometry/config.py:12-20`). [Build123dGeometry][femforge.geometry.Build123dGeometry] implements it for a build123d assembly: a subclass returns its tagged assembly from `model()`, and `build_step` exports it (`src/femforge/geometry/build123d_geometry.py:16-26`). A config without a geometry meshes the STEP at `mesh.step` instead, which any CAD can write as long as the tagged body names reach the file.

```python
import build123d as b3d

import femforge as ff


class MagnetInAir(ff.Build123dGeometry):
    """A cubic magnet at the centre of an air box."""

    magnet_size: float = 10.0
    """`a` `[mm]`: Edge length of the magnet."""

    def model(self) -> ff.TaggedCompound:
        air = ff.TaggedPart(b3d.Box(40, 40, 40), tags={ff.TagKey.MATERIAL: ff.materials.Air().tag})
        size = self.magnet_size
        magnet = ff.TaggedPart(b3d.Box(size, size, size), tags={ff.TagKey.MATERIAL: ff.materials.N42().tag, ff.TagKey.MAGNETIZATION: "N"})
        return ff.TaggedCompound(children=[air, magnet])


small = ff.SimulationConfig(geometry=MagnetInAir(magnet_size=10.0))
large = ff.SimulationConfig(geometry=MagnetInAir(magnet_size=12.0))
assert ff.run.step_key(small) != ff.run.step_key(large)
```

The interface has three rules:

- **The parameters are the identity.** The STEP spec hashes the geometry model itself, so two configs with equal parameters and an equal frame share one STEP, and a changed parameter makes a new one (`src/femforge/run/kinds.py:131-134`). The STEP file is derived output.
- **Subclass fields survive a round trip.** The `geometry` field is `SerializeAsAny[GeometryArea]`, so a dump carries the subclass's fields (`src/femforge/config.py:43`), and `GeometryArea` sets `extra="allow"`, so a manifest loaded back as the base class keeps them readable (`src/femforge/geometry/config.py:15-16`).
- **The geometry builds in the authored frame.** The driver calls `build_step` into the run directory, converts the result with the frame's [prepare_geometry][femforge.frames.Frame.prepare_geometry], and repoints `mesh.step` at the solve-frame STEP (`src/femforge/run/driver.py:70-91`). The repointed path uses forward slashes, because ElmerGrid on Windows fails on backslashes in its mesh path (`src/femforge/run/driver.py:65-68`).

`import femforge.geometry` stays light: `build123d_geometry.py` imports `TaggedCompound`, and with it build123d, only under `TYPE_CHECKING` (`src/femforge/geometry/build123d_geometry.py:12-13`).

## The frame area

The frame is a seam like the geometry and the mesh backend. [Frame][femforge.frames.Frame] is an abstract, frozen config model (`src/femforge/frames/base.py:245-335`), and each shipped frame is one subclass in its own module: [Solid][femforge.frames.Solid] (`src/femforge/frames/solid.py`), [Axisymmetric][femforge.frames.Axisymmetric] (`src/femforge/frames/axisymmetric.py`) and [Cartesian2D][femforge.frames.Cartesian2D] (`src/femforge/frames/cartesian_2d.py`). The solve stages call hooks on the frame rather than naming a frame class, so a new frame adds hook implementations rather than branches; the physics selection and a few error messages still name the shipped frames.

`FrameUnion`, the discriminated union on `kind`, types every serialized frame field: `SimulationConfig.frame`, `Result.frame`, `StepSpec.frame` and `MeshJob.frame` (`src/femforge/frames/__init__.py:13`). Every function and attribute that only calls the hooks takes the base `Frame`. `default_frame(model_dim)`, the frame of a bundle whose manifest names none, takes the first shipped frame of that dimension that builds from defaults alone, so `Cartesian2D`, whose `thickness` is required, follows `Axisymmetric` in the union and a 2D bundle without a manifest reads as axisymmetric.

| Hook | What it answers | Called by |
|---|---|---|
| `label` | a short name for messages and the UI | condition errors, the workbench |
| `model_dim` | the body dimension the mesher meshes | `Mesher.body_dim` (`src/femforge/meshing/mesher.py:93-95`), `mesh_spec`, the run index |
| `coordinate_system` | the Elmer coordinate system of the solved stack | `solved_stack` (`src/femforge/elmer/config/common/presets.py:29-35`), `PhysicsConfig.check_frame` (`src/femforge/elmer/config/common/physics_config.py:74-82`) |
| `plane` | the solve plane in the authored model, None for a whole-model solve; its `x_dir` and `y_dir` are the authored solve +X and +Y, and `normal` their cross product | `axisymmetric_cross_section`, `planar_cross_section`, the workbench's symmetry expansion, `FixedDisplacement.on_boundary` and `check_frame` |
| `rewrites_geometry` | whether the solve meshes a converted STEP; defaults to `plane is not None` | `materialize_geometry` (`src/femforge/run/driver.py:70-91`), `StepSpec.passes_through` (`src/femforge/run/kinds.py:96-99`) |
| `prepare_geometry` | the STEP the solve meshes | `materialize_geometry`, the STEP artifact producer (`src/femforge/run/kinds.py:102-121`) |
| `measure_geometry` | every body's and named face's mismatch against the frame's rule, as a `GeometryCheck`; None for a frame that takes any model | `check_geometry`, each sectioning frame's `prepare_geometry` |
| `check_geometry` | raises `FrameGeometryError` when the authored model does not fit the frame; concrete on the base | each sectioning frame's `prepare_geometry`, through the check's `raise_for_mismatch` |
| `solves` | the form a physics solves as, raising when it has none | `SimulationConfig.solved_physics`, `PhysicsSelection.check_frame`, `solved_stack`, `Sim` |
| `supports` | whether `solves` has a form; concrete on the base | `CurrentDensity.check_frame` |
| `lumps_torque` | whether Elmer's force lumping yields a torque | `CalculateMagneticForce.check_frame` |
| `solve_vector` | an authored vector in solve coordinates | `Magnetization.to_elmer` and `check_frame` (`src/femforge/physical/conditions.py:164-184`) |
| `total` | the whole-model total of a vector Elmer integrated over the solve domain | `parse_result` (`src/femforge/run/parse.py:224-227`) |
| `solve_share` | the share the solve domain carries of a whole-model solve-frame vector, the inverse of `total` | `authored_share` |
| `authored_share` | an authored whole-model vector's solve-frame share, `solve_share` of `solve_vector`; concrete on the base | `SurfaceForce.on_boundary`, `Run.clapeyron_energy` |
| `revolves` | whether the section stands for a model revolved about an axis; defaults to False | the mesher's `<BODY>__AXIS` groups, the field viewer's symmetry basis |
| `plane_stress` | whether a 2D elasticity solve is plane stress or plane strain; defaults to None, no plane state, and `Cartesian2D` answers from its `state` | a solver's `fit_frame` |
| `along` | a solve-frame vector's component along an authored axis | `Result.axial_force` (`src/femforge/run/result.py:127-131`) |

The config checks the frame on construction and on assignment through `SimulationConfig`'s three validators, each condition's `check_frame` and `PhysicsSelection.check_frame`, and `Sim` repeats the condition check, over the tags of the body groups and the boundary groups, for conditions edited in place after the config validated them (`src/femforge/elmer/sim.py:110-118`). `PhysicsSelection.selected` rejects `MAGNETOSTATICS_2D` as an authored choice and leaves it out of the JSON schema enum, because it is the form `MAGNETOSTATICS` solves as under a 2D frame (`src/femforge/elmer/config/common/selection.py:18-38`).

Solvers fit the frame too: `solved_stack` replaces every solver with its `fit_frame(frame)`, and `PhysicsConfig.check_frame` requires the frame's coordinate system exactly and calls every solver's `check_frame`. Both solver hooks are no-ops on `SolverBase`.

A frame imports `femforge.elmer.physics` and nothing else from the solver side: `coordinate_system` imports `CoordinateSystem` in its body, and each sectioning frame's `prepare_geometry` imports the geometry stack in its body, so `femforge.elmer.config` and `femforge.physical` can import the frame without a cycle.

To add a frame:

1. Subclass `Frame` in a new module under `src/femforge/frames/` with a `kind: Literal["..."]` default and its fields, and implement every abstract hook.
2. Add it to `FrameUnion` and `__all__` in `src/femforge/frames/__init__.py`, and to the root stub `src/femforge/__init__.pyi`.
3. Give it a test module under `tests/frames/` that covers every hook.
4. Write its user-guide page under `docs/user-guide/frames/`, add a row to the shipped-frames table on the [Frames](../../user-guide/frames/index.md) page and an entry to the `mkdocs.yml` nav, and update the coordinate systems table in [Status](../../overview/status.md#frames).

## How conditions and tags bind

A condition reaches the solver input through tags, never through a body id. The chain runs from the STEP part names to the sif blocks:

1. **The STEP names carry tags.** `MeshEntity` parses each gmsh entity's name into `key=value` tags (`src/femforge/meshing/mesh_entity.py:29-30`). It matches the material whose `tag` equals the entity's `Material` tag, ignoring case (`src/femforge/meshing/mesh_entity.py:54-61`), and every [EntityTag][femforge.physical.EntityTag] whose selector the entity's tags contain, in domain order (`src/femforge/meshing/mesh_entity.py:63-66`). It writes the name back in canonical form, and that rewrite must parse to the same tags again, because the mesher resolves entities both before and after the conformal fragment (`src/femforge/meshing/mesh_entity.py:34-37`).
2. **The mesher groups and names them.** Entities that share a material, an authored `Name` and a set of matched tags form one physical group (`src/femforge/meshing/mesher.py:193-209`). `compose_group_name` names it: the authored `Name` tag when there is one, else the material tag followed by the tag names (`src/femforge/meshing/naming.py:19-32`). The mesher, which writes the names, and the bundle readers, which require them, share this one grammar (`src/femforge/meshing/naming.py:1`). Elmer reads names into 128-character buffers and a boundary name joins two body names, so the function warns above 60 characters (`src/femforge/meshing/naming.py:9-11`).
3. **The group carries the tag objects.** A `PhysicalGroup` keeps the `EntityTag` instances it matched, conditions included (`src/femforge/meshing/groups.py:48-49`). A cached mesh rebinds its groups to the current domain by selector, so today's conditions ride along on yesterday's mesh (`src/femforge/run/artifacts.py:48-56`).
4. **The sif writer collects them per physics.** [conditions_for][femforge.physical.conditions_for] returns the conditions on a group's tags that belong to the active physics and attach to the requested target (`src/femforge/physical/domain.py:18-32`). `condition_physics` widens the active set for a coupled preset, so one set of tags drives the standalone and the coupled solves (`src/femforge/elmer/physics.py:52-66`).

Each condition is self-describing. It pins its `physics` and its body or boundary `target`, carries a `kind` discriminator for the `ConditionUnion` (`src/femforge/physical/conditions.py:462-465`), and emits its own sif keywords. The base `to_elmer` reflects each field's `ElmerKeyword` metadata, which pairs the sif keyword with the SI unit the quantity is stripped to (`src/femforge/physical/conditions.py:67-124`). A condition with `external_only` set lands only on a body's external shell, never on an interface (`src/femforge/physical/conditions.py:102-104`). [Solver objects](solver-objects.md#bodies-boundaries-and-components) covers where each kind of condition lands in the sif.

## The run identity

[cache_key][femforge.run.cache_key] is the SHA-256 of one canonical JSON payload: the bundle kind name, `RUN_PRODUCER_VERSION`, and the run spec (`src/femforge/run/kinds.py:202-204`). The run spec has three parts (`src/femforge/run/kinds.py:187-199`):

| Part | Holds | Leaves out |
|---|---|---|
| `config` | the config dump | `physics.overrides`, `meta`, `mesh.step`, every `HashExclude()` field at any depth |
| `step_key` | the STEP artifact key: the geometry model's parameters, or the authored STEP's geometric digest, plus the frame | the authored file's path |
| `physics_config` | the resolved solver stack | |

The pieces each have one job:

- **One serialization.** Every key in the codebase hashes `json.dumps(payload, sort_keys=True)` (`src/femforge/artifacts/keys.py:49-51`).
- **`HashExclude()` marks a field the hash skips.** `spec_payload` merges the marked fields at any depth into the exclude spec before dumping (`src/femforge/artifacts/keys.py:91-94`). The walk starts from the instance, so a polymorphic subclass contributes its own marked fields (`src/femforge/common/pydantic.py:57-72`). The gmsh `terminal_output` and `num_threads` options carry it (`src/femforge/meshing/gmsh_options.py:82`, `src/femforge/meshing/gmsh_options.py:108`).
- **Quantities hash in base units.** `run_spec_payload` dumps the config and the resolved stack with the `CANONICAL_UNITS` serialization context, so every quantity enters the key in base units at twelve significant digits and unit spelling does not change the run id, while every stored dump keeps the authored spelling (`src/femforge/run/kinds.py:187-199`, `src/femforge/physical/units.py:284-308`).
- **An authored STEP keys on its geometry.** `step_digest` imports the file and returns its `TaggedCompound.fingerprint`: the hash of every leaf's kind, tags, RGB color, size, volume, area or length, center of mass, bounding box, face areas and centers, and vertices, sorted so the leaf order does not count (`src/femforge/run/kinds.py:40-70`, `src/femforge/geometry/tags.py:69-113`). The size is the bounding-box diagonal to nine significant digits, and every other quantity is quantized to a billionth of it, so integration noise drops out and a scaled model still keys apart. The faces and vertices tell apart changes that keep the whole-body properties, such as a rotated hole. Two files with the same bytes share one memoized digest wherever they live. The bytes cannot be the identity: build123d numbers assembly occurrences from a process-wide counter and writes the style entities in a varying order, so two exports of one model differ. A file that does not import keys on its bytes with the `FILE_NAME` export timestamp masked, and `AuthoredStep.path` is `HashExclude()` (`src/femforge/run/kinds.py:73-82`).
- **The identity comes from the authored config.** The driver builds the manifest from the config it was given, before the geometry stage repoints `mesh.step` at the derived STEP, which would otherwise digest the derived STEP (`src/femforge/run/driver.py:290-297`).
- **Producer versions invalidate.** Bump `RUN_PRODUCER_VERSION`, `MESH_PRODUCER_VERSION` or `STEP_PRODUCER_VERSION` when a pipeline change alters what the same spec yields (`src/femforge/run/kinds.py:27-34`).

The run id is `bundle_name(cache_key)`: an adjective and a noun picked by hash bits, then the first 8 hex characters (`src/femforge/common/names.py:1044-1054`). The word lists are frozen, because any edit renames every existing bundle, and together they give 50 bits (`src/femforge/common/names.py:8-10`). The driver raises when a directory with the same name holds a different `cache_key` (`src/femforge/run/driver.py:280-286`). [mesh_key][femforge.run.mesh_key] and the other artifact keys follow the same scheme; [Artifacts and retention](artifacts.md#identity-and-names) covers them.

## customize_physics and the resolved stack

[SimulationConfig.resolved_stack][femforge.config.SimulationConfig.resolved_stack] returns `physics.overrides` when it is set, else the preset that `solved_stack` builds for the selection in the frame (`src/femforge/config.py:104-109`). [SimulationConfig.customize_physics][femforge.config.SimulationConfig.customize_physics] writes that preset into `physics.overrides` once and returns it for typed editing (`src/femforge/config.py:111-116`).

The identity hashes the resolved stack and drops the authored overrides, so customizing without an edit leaves the key where it was (`src/femforge/run/kinds.py:191-198`):

```python
import femforge as ff
from femforge.elmer.config import MagnetoDynamicsSolver

config = ff.SimulationConfig()
before = ff.run.cache_key(config)

stack = config.customize_physics()
assert ff.run.cache_key(config) == before

stack.solver(MagnetoDynamicsSolver).linear_system_max_iterations = 2000
assert ff.run.cache_key(config) != before
```

An edit through `customize_physics` enters the identity. An edit in the driver's `configure_sim` hook runs after the identity is computed, so it changes the solve without changing the run id (`src/femforge/run/driver.py:653-657`). Customize after you set the frame: overrides built for one frame fail `check_frame` under the other (`src/femforge/elmer/config/common/physics_config.py:79`).

## The JSON schema export

`SCHEMA_MODELS` lists every model with a published schema: the composition root and its areas, the solver tree, the run, retention and artifact records, the convergence targets, the display state and the doctor report (`src/femforge/schemas.py:46-95`). `python -m femforge.schemas --out DIR` writes one `<Name>.json` per model, with LF line endings (`src/femforge/schemas.py:111-121`). The docs build runs it before the site build (`.github/workflows/docs.yml:26`); the output is generated, never committed, and [JSON schemas](../../reference/schemas.md) lists it.

Each field description in a schema is the field's attribute docstring, because the models turn on `use_attribute_docstrings`. Two fields need help to export cleanly:

- `PhysicsSelection.selected` declares its schema enum by hand, so the internal `MAGNETOSTATICS_2D` stays out of it (`src/femforge/elmer/config/common/selection.py:22`).
- `OperatingPoint.temperature` uses a `default_factory`, because pydantic cannot encode a bare pint quantity as a schema default (`src/femforge/elmer/config/common/operating_point.py:18-20`).

A new persisted model joins `SCHEMA_MODELS`; `tests/test_schemas.py` checks that the export covers every registered model and writes valid JSON.

## Import cost

`import femforge` loads almost nothing. The root module attaches its stub with `lazy_loader` (`src/femforge/__init__.py:3-8`), and `src/femforge/__init__.pyi` lists every root name with the submodule it comes from. Six packages are lazy this way (`tests/test_public_api.py:76-79`). lazy_loader accepts only one-dot relative imports in a stub and reads it in the locale encoding, so a stub holds `from .module import Name as Name` lines in ASCII.

`tests/test_import_time.py` measures each step in fresh interpreters and compares the fastest of five runs against a budget:

| Step | Budget | Must not load | Where |
|---|---|---|---|
| `import femforge` | 60 ms | pydantic, pint, numpy, loguru, rich, any `femforge.*` submodule | `tests/test_import_time.py:77-93` |
| the first `ff.SimulationConfig` | 500 ms | gmsh, build123d, polars, pyvista, vtk, trame, loguru, `femforge.elmer.runtime`, `femforge.execution`, `femforge.run`, `femforge.telemetry`, `femforge_workbench` | `tests/test_import_time.py:81-82`, `tests/test_import_time.py:96-115` |

`FEMFORGE_IMPORT_BUDGET_SCALE` multiplies every budget on a slow machine (`tests/test_import_time.py:21`). New code that `SimulationConfig` imports counts against the 500 ms budget. These rules keep the heavy stacks off the config path:

| Rule | Where |
|---|---|
| `Mesher` and `MeshEntity` stay submodule imports, because both import gmsh at module level; `femforge.meshing` re-exports neither | `src/femforge/meshing/mesher.py:8`, `src/femforge/meshing/mesh_entity.py:3`, `src/femforge/meshing/__init__.py:5-13` |
| Code that drives gmsh imports it inside the function | `src/femforge/meshing/boundary.py:165`, `src/femforge/meshing/gmsh_options.py:58` |
| The geometry interface names build123d types only under `TYPE_CHECKING` | `src/femforge/geometry/build123d_geometry.py:12-13` |
| `femforge.elmer` is lazy, so `femforge.elmer.config` loads without the runtime | `src/femforge/elmer/__init__.py` |
| Telemetry, and with it polars, loads only when a root is set | `src/femforge/run/driver.py:367-376`, `src/femforge/common/log.py:206-211` |
