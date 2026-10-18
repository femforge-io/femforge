# User guide

The user guide covers one task per page, in the order a simulation flows through femforge. Start with [Concepts](../overview/concepts.md) for the whole pipeline on one page.

- [Geometry and tags](geometry.md): tag the bodies of the STEP file, in your CAD or in a build123d script.
- [Materials](materials.md): the material models, their property functions and units.
- [Conditions and physics](conditions.md): body forces, boundary conditions and the physics presets femforge solves.
- [Mesh](mesh.md): element sizes, gmsh options, deterministic meshing and the mesh backends.
- [Frames](frames/index.md): map the authored model onto the solve, whole or as a section.
- [Driver and runs](driver.md): how the driver stages, reuses and records each run.
- [Results](results.md): load results, read forces and fields, and validate them.
- [Artifacts and retention](artifacts.md): the artifact store, reuse across processes and the retention policy.
- [Telemetry](telemetry.md): the logs, run indexes and sweep progress femforge records.
- [Workbench](workbench.md): launch the workbench and inspect runs and fields in its panels.
- [Optimization](optimization.md): parameter sweeps and the root-finding searches over expensive solves.
- [Elmer: native and Docker](elmer.md): run Elmer from a native install or from the femforge image.
- [CLI](cli.md): the `femforge` and `femforge-workbench` commands.
