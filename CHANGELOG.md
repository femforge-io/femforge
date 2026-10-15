# Changelog

This file lists all notable changes to femforge and femforge-workbench. The two packages release together under one version. The format follows [Keep a Changelog 1.1](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-14

The first public release.

### Added

- `femforge new NAME` writes a project folder with one runnable script, its README and a `.gitignore`. The script solves a disc magnet pulling on a steel plate and opens the field in a browser, and `uv run NAME/main.py` installs everything it needs.
- `SimulationConfig` describes a simulation in one typed, validated model: the geometry, the mesh, the materials, the conditions of each region, the physics and the solve frame, 3D (`Solid`) or an axisymmetric section (`Axisymmetric`). Quantities carry their units through pint.
- Parametric geometry: a `Build123dGeometry` subclass builds a build123d model whose `TaggedPart` and `TaggedCompound` parts carry their material and name into the STEP file.
- Meshing with gmsh: conformal multi-body meshes, element sizes per region, typed `GmshOptions`, and `WorkerMesher` to mesh in a separate process.
- Elmer solves of magnetostatics in 3D and on the axisymmetric section, steady heat conduction, electrostatics, current conduction, Joule heating and coil magnetostatics. Elmer runs natively or in the femforge Docker image, `ghcr.io/femforge-io/femforge-elmer`, which also runs partitioned MPI solves.
- Conditions for those physics: `Magnetization`, `CurrentDensity`, `ForceComponent`, `FixedTemperature`, `HeatFlux`, `Convection`, `HeatSource`, `FixedPotential`, `JouleHeat`, `CoilDrive` and `CoilTerminal`.
- Materials: N42 and N52 magnets, 1080 steel with a nonlinear B-H curve, 316 steel, copper, FR4 and air, and a registry for your own.
- `Driver.evaluate` runs a config into a run bundle named after the content hash of its inputs, reuses a finished bundle with the same inputs, shares STEP files, meshes and bundles through an artifact store, and trims bundles by a retention policy.
- `Result` holds the forces, torques, peak flux density and peak temperature as pint quantities, and `Run` reads a bundle's result, manifest, bodies and solved fields.
- Telemetry: parquet indexes of runs, artifacts and log records, with queries, live log journals and sweep progress.
- Optimization: `Parameterization`, `Objective`, `GridSearch` and a search for the largest current under a temperature limit.
- Validation of a magnetostatic solve against magpylib's analytical field.
- The `femforge` command: `new`, `doctor` to check gmsh, Docker, the solver image and disk space and name the fix for each failure, `artifacts`, `purge`, `index`, `runs`, `logs`, `sweeps`, `telemetry` and `image`.
- femforge-workbench, installed with `femforge[workbench]`: a browser workbench with a launcher, run and artifact browsers, run, sweep and log monitors, and a field viewer with field lines. `femforge.workbench.show` serves a workbench from a script, and the `femforge.workbenches` entry point adds your own to the launcher.
- Examples: a magnet holding a bearing in its seat in 2D and 3D, a heated copper puck on an FR4 board, and a solenoid pulling a steel plunger.
- Type information for the whole package, including the lazy `import femforge as ff` namespace.
- Support for Python 3.12, 3.13 and 3.14.

[Unreleased]: https://github.com/femforge-io/femforge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/femforge-io/femforge/releases/tag/v0.1.0
