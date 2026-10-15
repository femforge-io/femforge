# Mesh

[MeshConfig][femforge.meshing.MeshConfig] decides how gmsh turns the STEP into a mesh of tetrahedra, or of triangles for a 2D model: the domain-wide element size, the typed gmsh options and whether meshing is reproducible. Per-region sizes ride on the same tags that carry materials and conditions. A mesh backend decides where gmsh runs, and the driver caches each mesh by the inputs that decide it, so a sweep over currents or temperatures meshes once.

## Set the sizes

```python
import femforge as ff

magnet = ff.EntityTag(
    selector={ff.TagKey.NAME: "Magnet"},
    conditions=[ff.Magnetization(direction=ff.Direction(0, 0, 1))],
    mesh_size=0.0005,
)

config = ff.SimulationConfig(
    mesh=ff.MeshConfig(
        step="magnet_in_air.step",
        global_mesh_size=0.002,
        gmsh_options=ff.GmshOptions(mesh_size_min=0.0002),
        deterministic=True,
    ),
    domain=ff.DomainConfig(materials=[ff.materials.Air(), ff.materials.N52()], tags=[magnet]),
)

if __name__ == "__main__":
    driver = ff.Driver(mesher=ff.WorkerMesher(timeout_s=600))
    print(driver.evaluate(config).run_id)
```

Every length is in metres. gmsh converts the STEP to metres on import, and build123d writes its STEP in millimetres, so a 10 mm magnet meshed at `mesh_size=0.0005` gets 0.5 mm elements.

## MeshConfig

| Field | Default | Unit | Meaning |
| --- | --- | --- | --- |
| `step` | `"data/geometry.step"` | | The STEP the mesher imports. A config with `geometry` set points it at the built STEP. |
| `global_mesh_size` | `0.001` | m | The domain-wide target element size. |
| `gmsh_options` | `GmshOptions()` | | The typed gmsh options below. |
| `deterministic` | `False` | | Pin meshing to one thread, so the same STEP and sizes always give the same mesh. |

## GmshOptions

[GmshOptions][femforge.meshing.GmshOptions] holds one field per gmsh option. A field set to `None` leaves gmsh's own default in place.

| Field | gmsh option | Default | Unit | Meaning |
| --- | --- | --- | --- | --- |
| `import_labels` | `Geometry.OCCImportLabels` | `True` | | Import the STEP entity names. The mesher reads the tags from them, so keep it on. |
| `terminal_output` | `General.Terminal` | `False` | | Print gmsh progress to the terminal. Never part of an identity. |
| `algorithm_2d` | `Mesh.Algorithm` | `FRONTAL_DELAUNAY` | | The 2D algorithm. It meshes the surfaces of a 3D model and the bodies of a 2D one. |
| `algorithm_3d` | `Mesh.Algorithm3D` | `HXT` | | The 3D algorithm. HXT runs on `num_threads`. |
| `recombine_all` | `Mesh.RecombineAll` | `False` | | Recombine triangles and tetrahedra into quads and hexes. Keep it off. |
| `mesh_size_min` | `Mesh.MeshSizeMin` | `None` | m | The lower clamp on element size. |
| `mesh_size_max` | `Mesh.MeshSizeMax` | `None` | m | The upper clamp on element size. |
| `optimize` | `Mesh.Optimize` | `True` | | Optimize element quality after meshing. |
| `smoothing` | `Mesh.Smoothing` | `None` | | The number of smoothing steps. |
| `num_threads` | `General.NumThreads` | physical core count | | Meshing threads; `None` gives gmsh's default of one. Never part of an identity. |
| `target_units` | `Geometry.OCCTargetUnit` | `"M"` | | The unit OpenCASCADE converts the STEP to. `None` keeps the STEP's own unit. |

The algorithms are `IntEnum` members of `ff.meshing.MeshAlgorithm2D` (`MESH_ADAPT`, `AUTOMATIC`, `DELAUNAY`, `FRONTAL_DELAUNAY`) and `ff.meshing.MeshAlgorithm3D` (`DELAUNAY`, `FRONTAL`, `MMG3D`, `HXT`).

## Size regions by tag

An [EntityTag][femforge.physical.EntityTag] with `mesh_size` sets the target size on the bodies its selector matches. The mesher applies the global size first, then each region size at the points of its bodies, so the size grades into the neighbouring bodies from their shared points. A body that several sized tags match takes the finest size. `mesh_size_min` and `mesh_size_max` clamp every size.

Refine where the field changes fast: an air gap, a thin conductor, the surface of a body whose force you measure. Tag the region in the CAD (see [Geometry and tags](geometry.md#select-bodies-with-tags)) and size it here.

## Mesh backends

| Backend | Where gmsh runs | Choose it when |
| --- | --- | --- |
| [GmshMesher][femforge.meshing.GmshMesher] | In this process. gmsh loads on the first job and is finalized after every one. | You mesh in a script or a notebook. It is the default. |
| [WorkerMesher][femforge.meshing.WorkerMesher] | In a child Python process, `python -m femforge.meshing.worker`. | gmsh must stay out of your process: to keep its license (GPL-2.0-or-later, see `NOTICE`) apart from your code, to survive a gmsh crash, or to stop a mesh after `timeout_s` seconds. |

Pass the backend to the driver: `ff.Driver(mesher=ff.WorkerMesher())`. The worker relays its log records into your log, and a failed or timed-out job raises [MeshError][femforge.meshing.MeshError]. `WorkerMesher(python=...)` runs the worker under another interpreter. To prove an entry point never loads gmsh, call `ff.meshing.block_gmsh()` before anything imports it.

## Mesh caching

The mesh is an artifact keyed by its [MeshSpec][femforge.meshing.MeshSpec]: the geometry identity, the body dimension, the global size, the gmsh options without `num_threads` and `terminal_output`, the `deterministic` flag, every tag's selector and mesh size, and the sorted material tags. Material properties, conditions, physics and run metadata stay out. With an artifact store on the driver, a run whose spec matches an earlier mesh reuses it instead of meshing again. See [Artifacts and retention](artifacts.md).

A tag's selector stays in the spec even without a `mesh_size`, because a matched tag can change a body's group name.

## Conformal meshes

build123d exports each solid on its own, and solids may touch or overlap. gmsh would mesh such a STEP with duplicate coincident faces and no shared nodes, so the field could not cross an interface and the solve would be wrong. Before meshing, the mesher fragments every body against every other, so touching bodies share their interface faces and nodes.

Fragmenting drops the STEP names. The mesher restores each piece's name from the smallest body that claims it, so an overlap belongs to the smaller body. Draw the air as a plain box around everything: the magnet inside it keeps its own volume, and the air keeps the rest.

The mesh stays simplicial: tetrahedra in 3D, triangles in 2D. `recombine_all` stays `False` because WhitneyAVSolver needs tetrahedra, HXT needs an all-triangle surface mesh, and recombining makes 3D meshing fail. femforge always sets `algorithm_2d` and `recombine_all` explicitly, because gmsh loads a saved user options file at start that can switch either one.

## Body and boundary groups

After meshing, each body group gets a name, and every boundary face lands in a boundary group: an interface between two bodies, a body's outer shell, or a symmetry axis. The partition covers every face because gmsh writes only the entities of a physical group into the mesh, and a face left out would vanish from the Elmer mesh. See [Geometry and tags](geometry.md#body-and-boundary-names) for the names.

## Deterministic meshing

HXT spreads its work over `num_threads`, every physical core by default, and a multithreaded mesh can differ between two runs of the same input. `deterministic=True` pins meshing to one thread, so the same STEP and sizes always give the identical mesh. It is slower, and it is part of the mesh and run identity. `num_threads` itself never enters an identity.

## Check convergence

A mesh is fine enough when refining it no longer changes the quantity you report.

1. Solve at your working sizes and record the quantity of interest.
2. Halve `global_mesh_size` and the region sizes near that quantity, and solve again.
3. Repeat until the change falls below your tolerance.

Check each quantity on its own. A force lumped over a body depends on the field at the body's surface, so a settled field energy does not show that the force has settled. Refine the body that carries a `ForceComponent` and the gap around it, and keep `deterministic=True` through the study so every change comes from the sizes.

## Reference

- [Meshing API](../reference/meshing.md): `MeshConfig`, `GmshOptions`, the backends and the group records.
