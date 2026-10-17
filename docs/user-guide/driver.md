# Driver and runs

The [Driver][femforge.run.Driver] turns a [SimulationConfig][femforge.config.SimulationConfig] into a [Result][femforge.run.Result]. It derives the run identity from the config and returns a finished run with that identity when one exists. Otherwise it meshes with the mesh backend, gmsh today, writes the sif and solves with the solver backend, Elmer today. It then parses the output and records the run in its own bundle directory. Where each step executes is a driver setting, never part of the config, so the same config names the same run on every machine and every backend.

## Evaluate a config

```python
import femforge as ff

config = ff.SimulationConfig(
    mesh=ff.MeshConfig(step="models/actuator.step", global_mesh_size=2 * ff.U.mm),
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
    meta=ff.RunMeta(description="Baseline gap", tags={"study": "gap"}),
)

driver = ff.Driver(runs_dir="runs", backend=ff.elmer.DockerElmer(), mesher=ff.WorkerMesher())
result = driver.evaluate(config)

print(result.run_id, result.status)
print(result.axial_force("mover", ff.Axis.Z))
```

Run it twice: the second call finds the finished bundle and returns its result without solving.

## Construction

Every parameter is keyword-only.

| Parameter | Default | What it does |
|---|---|---|
| `runs_dir` | `"runs"` | The directory each run's bundle is written under. |
| `code_sha` | `None` | The code version each manifest records; None reads it from git. |
| `solver` | `None` | A replacement for the staged pipeline, for tests that run without gmsh or Elmer. |
| `mpi_processes` | `1` | The ElmerSolver rank count; more than 1 needs a partitioned backend. |
| `backend` | `None` | Where ElmerGrid and ElmerSolver run; None picks Docker or native Elmer from `docker`, `docker_image` and `mpi_processes`. |
| `mesher` | `None` | Where gmsh meshes each STEP; None meshes in this process. |
| `docker` | `None` | True runs Elmer in Docker and False runs it natively; it cannot be combined with `backend`. |
| `docker_image` | `None` | An image tag to run Elmer in instead of femforge's own image; it cannot be combined with `backend`. |
| `retention` | `None` | What leaves each bundle after its evaluation; None is `RetentionPolicy.default()`. |
| `artifacts` | `None` | The cache the STEP, mesh and bundle stages read from and publish to; None keeps runs store-free. |
| `reuse` | `True` | Whether to return a finished bundle with the same identity instead of solving again. |
| `bundle_policy` | `BundlePolicy.FIELDS` | Which bundle files a publish carries, and so which files a reused bundle must still hold. |
| `telemetry` | `None` | The telemetry root for each run's index row and log records; None falls back to `FEMFORGE_TELEMETRY`. |

[Artifacts and retention](artifacts.md) covers `retention`, `artifacts` and `bundle_policy`; [Telemetry](telemetry.md) covers `telemetry`.

## Execution backends

Solving runs through the [ExecutionBackend][femforge.elmer.ExecutionBackend] interface, and Elmer is the solver implemented today:

| Backend | Runs Elmer | Partitioned |
|---|---|---|
| [NativeElmer][femforge.elmer.NativeElmer] | from a local install, located on first use | no: native builds lack the MUMPS a partitioned solve needs |
| [DockerElmer][femforge.elmer.DockerElmer] | in `ghcr.io/femforge-io/femforge-elmer:0.2.0` by default | yes, with the image's MUMPS |

`DockerElmer` reuses a matching local image, else pulls the released one, else builds it from its recipe. When the recipe in your checkout differs from the released one, it builds a local development image instead. Pass `DockerElmer(DockerImage(tag))` or `Driver(docker_image=tag)` to run another image. [Elmer native and Docker](elmer.md) covers the image and the native install.

Without `backend`, the driver picks one:

| `docker` | `docker_image` | `mpi_processes` | Backend |
|---|---|---|---|
| `True` | any | any | `DockerElmer` |
| `None` | set | any | `DockerElmer` in that image |
| `None` | `None` | more than 1 | `DockerElmer` |
| `None` | `None` | 1 | `NativeElmer` when `ElmerSolver` and `ElmerGrid` are installed, else `DockerElmer` |
| `False` | `None` | any | `NativeElmer` |

`docker=False` with a `docker_image` raises.

### Parallel solves

Set `mpi_processes` above 1 to solve on several ranks. The driver checks at construction that the backend is partitioned, so `NativeElmer` raises there. Before the solve it:

- switches CoilSolver's direct solve to MUMPS and raises for any other direct solve in the stack;
- partitions the Elmer mesh with ElmerGrid into `partitioning.<n>/`, adding halo elements only when the stack needs them for coil electrodes;
- warns when a partitioned coil solve also computes nodal forces, because halo elements corrupt the Component force lumping.

[Results](results.md#scalars) explains how femforge reads the per-rank scalar files.

## Meshers

Meshing runs through the [MeshBackend][femforge.meshing.MeshBackend] interface, and gmsh is the mesher implemented today:

| Mesher | Where gmsh runs |
|---|---|
| [GmshMesher][femforge.meshing.GmshMesher] | In this process: gmsh loads on the first job and is finalized after every one. The default. |
| [WorkerMesher][femforge.meshing.WorkerMesher] | In a child Python process, so gmsh, its license and its crashes stay out of yours. `timeout_s` stops a job that runs too long. |

The mesher never enters the run identity. [Mesh](mesh.md) covers the mesh settings themselves.

## evaluate() and the stages

[Driver.evaluate][femforge.run.Driver.evaluate] takes the config and an optional `retention` that overrides the driver's policy for one call. It then:

1. Computes the identity: `cache_key(config)`, the run id derived from it, and the bundle directory `runs_dir/<run_id>`.
2. Returns a reusable bundle, local or from the artifact cache.
3. Claims the run directory, so a second process asking for the same run waits.
4. Writes the manifest with status `running` and runs the stages below.
5. Parses the result and writes the bundle.
6. Publishes the bundle to the artifact cache, when one is configured and the run did not end in `error`.
7. Applies the retention policy.

| [Stage][femforge.run.Stage] | Driver method | What it does |
|---|---|---|
| `PREPARE_GEOMETRY` | `prepare_geometry` | Build or fetch the solve-frame STEP. |
| `PRE_MESH` | `pre_mesh` | A hook before meshing. |
| `MESH` | `mesh` | Mesh the STEP and convert it with ElmerGrid, or fetch the cached mesh. |
| `POST_MESH` | `post_mesh` | A hook between meshing and building the sim. |
| `BUILD_SIM` | `build_sim` | Build the typed sim from the resolved solver stack. |
| `CONFIGURE_SIM` | `configure_sim` | A hook to edit the solver stack before the sif is written. |
| `WRITE_SIF` | `write_sif` | Write the sif into the run directory. |
| `RUN_SOLVER` | `run_solver` | Run ElmerSolver, partitioning the mesh first for a parallel solve. |
| `MESH_STATS` | `mesh_stats` | Record the mesh size summary. |
| `PUBLISH` | `publish` | Commit the bundle to the artifact cache. |
| `CLEANUP` | `cleanup` | Apply the retention policy. |

Subclass the driver and override a stage method or a hook to change the pipeline. `configure_sim` runs after the identity is computed, so an edit there does not change the run id; author `config.physics.overrides` instead when the change should. Each stage emits a [StageEvent][femforge.run.StageEvent] at its start and its finish through `on_stage_event`, which records the timeline on the manifest:

```python
import femforge as ff
from femforge.run import StageEvent


class TimedDriver(ff.Driver):
    """A driver that prints each stage's duration."""

    def on_stage_event(self, event: StageEvent) -> None:
        super().on_stage_event(event)
        if event.finished_at is not None:
            print(event.stage, event.finished_at - event.started_at)
```

A stage that raises sets the manifest status to `error`, applies the retention policy and re-raises. An interrupt leaves the bundle as it is.

## The run bundle

Each run writes one directory under `runs_dir`, named by its run id:

```text
runs/
├── calm-avocet-3f9a1c07/
│   ├── manifest.json              provenance: config, keys, status, stage timeline
│   ├── result.json                the typed Result
│   ├── geometry.step              the STEP the run meshed
│   ├── geometry.msh               the gmsh mesh, absent when the mesh came from a store
│   ├── mesh.header                ElmerGrid's mesh, with mesh.nodes, mesh.elements, mesh.boundary
│   ├── mesh.names                 ElmerGrid's ids by group name
│   ├── groups.json                the mesh groups a cached mesh is rebound from
│   ├── entities.json              named bodies and boundaries by mesh id
│   ├── boundaries.yml             boundary ids by name
│   ├── components.yml             sif Component ids by name
│   ├── case.sif                   the solver input, with ELMERSOLVER_STARTINFO
│   ├── scalars.dat                SaveScalars output, with scalars.dat.names
│   ├── case_t0001.vtu             the field output
│   ├── elmergrid.log              with elmersolver.log, convergence.jsonl, convergence_targets.json, run.log
│   └── partitioning.4/            the partitioned mesh of a 4-rank solve
└── calm-avocet-3f9a1c07.lock/     the claim, while a driver solves the run
```

The [RunManifest][femforge.run.RunManifest] in `manifest.json` describes the run:

| Field | Holds |
|---|---|
| `run_id` | The bundle directory name, derived from `cache_key`. |
| `cache_key` | The full content hash of the result-affecting inputs. |
| `created_at`, `git_sha`, `elmer_version` | Provenance; none of them feeds the run id. |
| `config` | The full typed input. |
| `physics`, `physics_config` | The physics solved and the resolved solver stack. |
| `mesh_key`, `mesh_name` | The mesh artifact key and its store directory name. |
| `mesh_stats` | Element count and size range. |
| `status` | `running`, then `ok`, `diverged` or `error`, or `abandoned` when its claim went silent first. |
| `stage_history` | One timed event per stage. |
| `artifacts`, `published` | The store-sourced inputs and the bundle's own store entry. |
| `cleanups` | Every retention pass applied to the bundle. |
| `notes` | A free-form note; cosmetic. |

## Run ids and the cache key

The run id has the form `<adjective>-<noun>-<hash8>` and is a pure function of the [cache_key][femforge.run.cache_key]. Identical inputs map to the same directory, and a second machine computes the same id.

| Enters the cache key | Stays out |
|---|---|
| The config, minus the fields on the right | `config.meta`: description and tags |
| The geometry: the parametric geometry's parameters, or the authored STEP's content digest with its export timestamp masked | The authored STEP's path |
| The frame (an unset `radial` adds nothing) | `physics.overrides` as written; the resolved stack stands in for it |
| The resolved solver stack | gmsh `num_threads` and `terminal_output` |
| The pipeline's producer version | The git commit, the machine, the run directory |
| | Every driver setting: backend, mesher, `mpi_processes`, retention, artifacts, bundle policy, telemetry |

Editing `config.meta` relabels a run without solving it again. Overrides identical to the preset leave the key unchanged. When two keys share a name, which is astronomically unlikely, the driver raises so that two runs never mix in one directory.

## Claims and reuse

With `reuse=True`, `evaluate()` returns the local bundle when its manifest has the same `cache_key`, its status is `ok` or `diverged`, it holds `result.json`, and, unless `bundle_policy` is `CORE`, retention has not purged its field output. Otherwise, with an artifact cache, it fetches the published bundle into the run directory. A run that ended in `error` solves again; divergence is a physics outcome and is reused. `reuse=False` always solves.

One process produces each bundle. The driver claims `runs_dir/<run_id>.lock/` and refreshes the claim every 10 s while it solves. Another process asking for the same run checks every 2 s and reuses the bundle once the first one finishes with a reusable status. A claim silent for 120 s is stale, and the next claimant breaks it.

## Reaping stale runs

A process that dies mid-solve leaves its bundle at `running` and its claim behind. [reap_runs][femforge.run.reap_runs] settles them: it marks each bundle left `running` with no live claim as `abandoned` and breaks every silent claim. A claim is silent when its directory is gone, its heartbeat is stale, or it was taken on this host by a process that no longer exists.

```console
femforge runs reap --runs-dir runs
```

The command also re-indexes the runs it changed in the telemetry store. See [CLI](cli.md) for its options.

## Finding runs again

[Run][femforge.run.Run] opens a bundle for reading: its result, manifest, named bodies, components and field output. `Run.from_result(result)` opens the bundle a result came from, and `Run.latest(runs_dir)` the newest one. [find_runs][femforge.run.find_runs] filters a runs directory by `config.meta`, newest first:

```python
import femforge as ff

for run in ff.find_runs("runs", tags={"study": "gap"}, text="baseline"):
    print(run.path.name, run.meta.description, run.result.status)
```

`tags` matches by subset, and `text` searches the run id, the description and the notes, ignoring case. `find_runs` reads every manifest; over thousands of bundles, query the [RunIndex][femforge.telemetry.RunIndex] instead.
