# Concepts

femforge runs one pipeline. The mesh backend, gmsh today, meshes a STEP file whose bodies are named with their tags. The solver backend, Elmer today, solves the mesh, and the parsed outputs become a typed result inside a run bundle. One [SimulationConfig][femforge.config.SimulationConfig] describes every stage, and a [Driver][femforge.run.Driver] executes it. This page names each concept once and links the guide page that covers it.

```python
import femforge as ff

config = ff.SimulationConfig(
    frame=ff.Axisymmetric(),
    mesh=ff.MeshConfig(step="seat.step", global_mesh_size=0.5 * ff.U.mm),
    domain=ff.DomainConfig(
        materials=[ff.materials.N52(), ff.materials.Steel_1080(), ff.materials.Air()],
        tags=[
            ff.EntityTag(selector={ff.TagKey.NAME: "SEAT"}, conditions=[ff.Magnetization(direction=ff.Direction(0, 0, 1))]),
            ff.EntityTag(selector={ff.TagKey.NAME: "BEARING"}, conditions=[ff.ForceComponent()]),
        ],
    ),
    physics=ff.PhysicsSelection(selected=ff.Physics.MAGNETOSTATICS),
    meta=ff.RunMeta(description="Bearing pull of a 4 mm magnet"),
)

driver = ff.Driver(backend=ff.elmer.DockerElmer(), mesher=ff.GmshMesher())
result = driver.evaluate(config)
print(result.axial_force("BEARING"))
```

## Geometry and tags

The geometry is a STEP file whose bodies carry `key=value` tags in their names, such as `Material=N52, Name=MAGNET`. Any CAD that writes body names into the STEP produces one, such as Fusion 360, SolidWorks or FreeCAD. In a build123d script, a [TaggedPart][femforge.geometry.tags.TaggedPart] carries the tags and writes them into its STEP name for you. The mesher reads the tags back from the names. The [TagKey][femforge.common.TagKey] vocabulary names the shared keys: `Material` picks the body's material, and `name=` sets the `Name` tag. A tag value cannot carry `/`, `,`, `_` or whitespace, so femforge removes them and warns. A body's group name is its `Name` tag when you set one, else its material tag joined with the names of the region tags it matched, uppercased in both cases. The mesh groups, the Elmer sif, `entities.json` and the workbench all use that one name. The geometry comes from an authored STEP at `mesh.step`, or from a parametric `geometry` area that the driver builds into the run directory. See [Geometry and tags](../user-guide/geometry.md).

## The config

[SimulationConfig][femforge.config.SimulationConfig] composes one typed area per concern:

| Field | Type | Holds |
| --- | --- | --- |
| `geometry` | a [GeometryArea][femforge.geometry.GeometryArea], or None | the parametric model the driver builds into a STEP |
| `frame` | [Solid][femforge.common.frame.Solid] or [Axisymmetric][femforge.common.frame.Axisymmetric] | how the authored model maps onto the solve |
| `mesh` | [MeshConfig][femforge.meshing.MeshConfig] | the STEP path, the global element size and the gmsh options |
| `domain` | [DomainConfig][femforge.physical.DomainConfig] | the materials and the per-region [EntityTag][femforge.physical.EntityTag] overrides |
| `physics` | [PhysicsSelection][femforge.elmer.PhysicsSelection] | the physics to solve and optional solver overrides |
| `meta` | [RunMeta][femforge.config.RunMeta] | a description and free tags, kept out of the run identity |

Each area validates on its own, and the config rejects a frame that its domain or physics cannot solve, on construction and on assignment. Every pipeline stage reads only the areas it needs.

## Materials

A [MaterialProperties][femforge.materials.MaterialProperties] model holds a material's mechanical, thermal, magnetic and electrical properties under a registry `tag`, such as `N52` or `Air`. The mesher matches each body to the domain material whose tag equals the body's `Material` tag. Each property is a function of the operating point: a constant, an interpolated calibration or a closed-form expression, with pint units. See [Materials](../user-guide/materials.md).

## Conditions and physics

A condition is a typed body force or boundary condition, such as [Magnetization][femforge.physical.Magnetization], [FixedTemperature][femforge.physical.FixedTemperature] or [Convection][femforge.physical.Convection]. Each condition names the [Physics][femforge.elmer.Physics] it feeds and whether it targets a body or a boundary. You attach conditions to an [EntityTag][femforge.physical.EntityTag], whose selector matches bodies by their tags. The mesher derives boundary groups from body adjacency: `A__B` for the faces two bodies share, `A__EXT` for a body's outer shell and `A__AXIS` for the symmetry axis of a 2D model. A body's boundary conditions reach every boundary group it borders, and an external-only condition such as convection lands on its outer shell alone. The physics selection resolves to a typed solver stack that you can edit field by field, and the sif writer checks materials against solvers before Elmer starts. See [Conditions and physics](../user-guide/conditions.md).

## Frames

The frame decides what femforge solves. [Solid][femforge.common.frame.Solid] solves the authored 3D model as-is. [Axisymmetric][femforge.common.frame.Axisymmetric] sections the model into its r-z half-plane, with X radial and Y along the symmetry axis, and meshes it in 2D. Magnetostatics then solves with the 2D axisymmetric stack, and forces come back as full-revolution totals. Results stay in the solve frame. See [Frames](../user-guide/frames.md).

## The driver and the run bundle

[Driver.evaluate][femforge.run.Driver.evaluate] runs a config through named [stages][femforge.run.Stage]: prepare the geometry, mesh and convert with ElmerGrid, build and write the sif, and solve. It then parses the outputs, writes the bundle, publishes it and cleans up. Each run writes a bundle under `runs/<run_id>/`: the manifest, `result.json`, the sif, the Elmer mesh, the field output and `run.log`. The `run_id` is a memorable name derived from the run's content key, which hashes every input that affects the result. The code commit, the machine, the metadata and the execution settings stay out of it, so the same config always lands in the same bundle. A finished bundle with the same key is returned without solving, and a second process asking for a run in progress waits for it. The mesher is a `MeshBackend`: `GmshMesher` runs gmsh in process and `WorkerMesher` runs it in its own process. The backend is an `ExecutionBackend`: `NativeElmer` runs an installed Elmer and `DockerElmer` runs Elmer in the femforge Docker image. See [Driver and runs](../user-guide/driver.md).

## Results

A [Result][femforge.run.Result] holds the solve status and pint-typed outputs: net force and torque, the force and torque on each force component, peak flux density and peak temperature. Values the parser finds but does not type yet stay in `raw`. [Run][femforge.run.Run] opens a bundle for reading: the result, the manifest, the named bodies and boundaries, and the solved field mesh. [find_runs][femforge.run.find_runs] searches bundles by their metadata tags or text. See [Results](../user-guide/results.md).

## Artifacts and reuse

An [ArtifactCache][femforge.artifacts.ArtifactCache] stores content-addressed artifacts that several runs share: the solve-frame STEP, the Elmer mesh and the finished run bundle. The mesh key ignores material properties, conditions, physics and metadata, so a sweep over material properties or drives meshes once. Pass a cache as `Driver(artifacts=...)` to read from and publish to it. A [RetentionPolicy][femforge.run.RetentionPolicy] decides which bundle files each run keeps after it finishes, and `femforge purge` applies one to existing bundles. See [Artifacts and retention](../user-guide/artifacts.md).

## Telemetry

Telemetry records logs, the run and artifact indexes and sweep progress as hive-partitioned parquet tables under one root. Each process writes a JSONL journal first, so live readers see records before parquet settles them. Set the root with `Driver(telemetry=...)` or the `FEMFORGE_TELEMETRY` environment variable, and query it with [Telemetry][femforge.telemetry.Telemetry] or the `femforge runs`, `femforge logs` and `femforge sweeps` commands. See [Telemetry](../user-guide/telemetry.md).

## The workbench

The workbench is a browser application in the `femforge-workbench` package. The `femforge-workbench` command opens a launcher that lists the installed workbenches, and `ff.workbench.show()` serves a workbench that you build from panels in a script. Its panels browse runs and artifacts, follow live runs, sweeps and logs, and draw solved fields in 3D. See [Workbench](../user-guide/workbench.md).
