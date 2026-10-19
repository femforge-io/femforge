# Changelog

This file lists all notable changes to femforge and femforge-workbench. The two packages release together under one version. The format follows [Keep a Changelog 1.1](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Each condition names the sif section it lands in, `Condition.section`, from the new `SifSection` enum, and `shipped_conditions()` lists the condition classes `ConditionUnion` admits.
- `Condition.on_boundary(site)` emits a boundary condition for one boundary group, described by a `BoundarySite`; a boundary group carries its own `tags`, `measure` and `named` flag, and the tags' boundary conditions land after the bordering bodies' ones.
- Frame hooks `Frame.solve_share`, the inverse of `Frame.total`, `Frame.revolves` and `Frame.plane_stress`, and solver hooks `SolverBase.fit_frame` and `SolverBase.check_frame`.
- Named faces: a `TaggedFace` on a face of a 3D body becomes a boundary group of its own, such as `TIP`, carved out of the shell or interface it lies on, with its area recorded. The boundary conditions of the tags it matches land there, `Boundaries.face(name)` finds it in a run, and `import_tagged_step` reads named faces back. `TaggedFace` is now exported from `femforge`.
- `GmshOptions.element_order` sets the element order (`Mesh.ElementOrder`), such as 2 for quadratic elements.
- `Physics.LINEAR_ELASTICITY` solves: the `FixedDisplacement` and `SurfaceForce` boundary conditions, a Poisson ratio on the shipped solid materials, and `StressSolver` writing strains and principal stresses on an undeformed output mesh.
- `Run.strain_energy()` and `femforge.run` `strain_energy`, `clapeyron_energy` and `strain_energy_density` read the elastic strain energy back; `FieldQuery.where_boundary` and `surface_mean` select a boundary and average a field over its area.
- The `Cartesian2D` frame solves the planar section of a prismatic model per metre of depth and scales the typed forces by its `thickness`: magnetostatics, heat conduction, and linear elasticity in plane stress or plane strain (`PlaneState`). `FieldQuery.integrate_extruded(depth)` integrates a planar section over a prism, and `Vec3.cross` is the vector product.
- The Cartesian 2D guide states that the strain energy readers work per metre of depth, and how to balance `clapeyron_energy` against them.
- A 2D frame sections each named face into a named edge, a `TaggedEdge` that now takes `name=` and is exported from `femforge`, so the conditions on a named face land on its edge of the section.
- The field viewer shows elasticity runs: it opens on the displacement, offers Von Mises, Tresca, Strain Energy Density and the principal stresses and strains with the Max, Mid and Min Principal modes, and leaves the stress and strain tensors and their `stress_xx` columns out. `FieldKind` and `line_vectors` join `femforge.viz`.
- `Run.clapeyron_energy(face, force)` balances the strain energy against the work of a face's authored force and takes the solve frame's share itself, which `Frame.authored_share` gives.
- The field viewer's Deformation section draws the bodies displaced by the solved displacement, at a scale that starts where the largest displacement moves a tenth of the model's size. `DisplaySpec.warp` and `DisplaySpec.warp_scale` hold the choice.
- `default_display(run)` picks the display a run opens on: its solver stack's primary field (`PhysicsConfig.primary_variable`), the component its surface forces share, and field lines with opacity mapping only for a flux, field or current (`ElmerVariable.lines_by_default`).
- The driver stops a linear solve whose residual stops improving, marks the run `stalled`, and records the reason and the last residuals in the manifest; a linear solve logs a progress line every 30 s.
- `Axisymmetric` and `Cartesian2D` check the authored model before sectioning it. `Frame.check_geometry` raises `FrameGeometryError`, listing every body or named face that is not a solid of revolution about the axis, or not a prism through the thickness, with its mismatch; the solids or faces that share a name are measured as one. `boundary_cylinder` bounds an axisymmetric model with a cylinder of air, and the run manifest records the check as `geometry_check`, on a pass and on a failure.
- `ff.workbench.show(..., tasks=)` runs a script's tasks in background child processes, one at a time, while the workbench serves, with a task strip under the dock that shows each task's stage, progress and failure and offers Open, Cancel and Retry; `ff.workbench.Task` labels a task, by its function name in Title Case when no label is given. The strip also shows a task waiting on another process's claim, a stalled solve with its log, and a confirmed Cancel at once. A save lists the tasks again, and Ctrl+C or closing the window stops the running one and releases its run.
- Driver and solve records bind the keys of the new `LogKey` enum: the run directory, the stage, the solver, the share of convergence covered, the linear iteration, a stall's reason and the holder of a claim the driver waits on. The terminal leaves out the keys its message already says.

### Changed

- `ForceComponent` is now `CalculateMagneticForce`, with the kind `calculate_magnetic_force`, after the Elmer keyword it sets. Rename it in your scripts and saved configs.
- A customized solver stack must use the frame's coordinate system exactly, not only one that integrates the same way.
- A boundary group bordered twice by one tag gets that tag's conditions once.
- Field lines follow the first vector field when the run has no flux density, such as the displacement or the volume current, while a scalar is shown. A `FieldScene` whose field-line options name no vectors picks them the same way, and records the vectors it picked.
- Cached meshes and run bundles from 0.1.0 are rebuilt on first use.
- The conditions guide is now a section with one page per physics, listing the conditions each physics reads by sif section with their fields, units and sif keywords, generated from the condition classes; `/user-guide/conditions/` stays its index.
- `SolvePlane` names its directions `x_dir` and `y_dir`, for the old `radial` and `axis`, and gains `normal`.
- The field viewer opens each run on its `default_display` unless the given `DisplaySpec` names a field. A quantity, mode, field-line or opacity-mapping choice you make carries over only to a run that has the quantity shown.
- The project template and the coil-current and bearing-seat examples bound their models with a cylinder of air, which the axisymmetric check accepts. Every STEP artifact rebuilds once.
- Half and quarter axisymmetric models are rejected: author the whole revolution.
- `StressSolver` solves with CG and ILU1 preconditioning to a linear tolerance of `1e-7`, for BiCGStab, ILU0 and `1e-8`, which stalled on large quadratic meshes.
- `FixedDisplacement` maps each authored axis onto the solve coordinate it runs along, signed. A Cartesian 2D section normal to X or Y and an axisymmetric section now take clamps and rollers, an axis off the plane is dropped, and an axis oblique to a free one raises. `FixedDisplacement.to_elmer()` raises, since its keywords depend on the frame. `Frame.solve_share` names its argument `share_of`.
- The curves of a 2D section take their names from its named edges only, so a colored body no longer names its own boundary curves and its `__EXT` and `__AXIS` groups come back.
- Cached sections and the runs built on them are rebuilt on first use.
- `RunStatus` is a `StrEnum` with `STALLED`; the manifest's `running` and `abandoned` are `LifecycleStatus`.
- The `femforge new` template and the examples solve through `show(tasks=)`; bearing-seat no longer solves on a daemon thread.
- The launcher's script runs are `ScriptRun`, `ScriptStatus` and `ScriptField` in `femforge_workbench.runner`, for `Task`, `TaskStatus` and `TaskField`.

### Fixed

- `SurfaceForce` takes the solve share of its solve components, so an axial load under an axisymmetric frame no longer raises.
- The field viewer recolors the bodies when another field is picked. It used to keep drawing the values of the field it opened on, so a switch from the displacement to Von Mises left the bodies at the bottom of the colormap.
- Exporting an authored STEP again keeps its run identity, so the driver reuses the run. The digest now hashes the names, tags, colors, sizes, mass properties, faces and vertices of its bodies, `TaggedCompound.fingerprint`, instead of the bytes, which changed on every export; runs on an authored STEP are solved once more on first use.
- `Result.residual` reports how far the last nonlinear solver stopped from converging, instead of 2.0 for elasticity and 0.0 for magnetostatics; one-value linear residual lines and `ComputeChange` lines without their prefix are parsed.

## [0.1.0] - 2026-10-17

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
- The field viewer's Symmetry section expands a run solved on an axisymmetric section: as solved, mirrored into the full r–z plane, or revolved around its axis into a solid in the authored frame, with a sweep angle for a cutaway. Revolve is the default, so the first view of the magnet-plate project is 3D. Field lines of a revolved run are seeded as in a solid run, integrated in the section and swept to their seeds.
- A Legend setting, Hidden, Vertical or Horizontal, for the color bar; the field lines share the field's bar while the field shows their magnitude, and get one beside it otherwise.
- A guided tour of the field viewer on the first start, replayed from the help button in the app bar.
- The femforge logo: the docs site shows it in place of the book icon, and the workbench shows it with the femforge name at the left of the app bar and as the browser tab's icon.
- The color bars are titled with the field's unit, which `ElmerVariable.unit` and `variable_unit` name for every solver output; the potential's unit follows the physics solved.
- Examples: a magnet holding a bearing in its seat in 2D and 3D, a heated copper puck on an FR4 board, and a solenoid pulling a steel plunger. The docs show each one with a screenshot of its workbench.
- Type information for the whole package, including the lazy `import femforge as ff` namespace.
- Support for Python 3.12, 3.13 and 3.14.

### Changed

- The field viewer starts with the Turbo colormap, no outline, opacity mapping from 0.15 to 1.0, and field lines on from 18 × 18 seeds on the center plane.
- The range slider wears the colormap between its thumbs, and moving a thumb pins the range.
- The run picker lists each run by its description and id.
- The controls' sections are spaced apart, the run picker is wider, and the bundle path beside it is a read-only readout of the open run.
- The field viewer draws every run in its one view; the plotly 2D tab and the `FieldScene.figure` path are gone.
- Solve frames moved to the `femforge.frames` package. `Frame` is now an abstract base class that every frame subclasses, and each pipeline stage calls a hook on it instead of naming `Solid` or `Axisymmetric`; `FrameUnion` types the serialized frame fields, and `ff.Frame` joins `ff.Solid`, `ff.Axisymmetric` and `ff.Axis`. `femforge.common.frame` is gone.
- `Axisymmetric.plane` gives the section's plane in the authored model as a `SolvePlane`, and `axisymmetric_cross_section` takes that plane instead of an axis and a radial direction.
- `geometry.frame.apply_frame` is replaced by `Frame.prepare_geometry`, and `import_tagged_step` moved to `femforge.geometry.step`. `Physics.in_frame` is replaced by `Frame.solves`, and `run.magnetic.revolve` by `Frame.total`.
- A `MeshJob` carries the solve frame, so the mesher no longer rebuilds one from the mesh dimension.
- The Frames guide is a section: a page on the concept and one page per frame.

### Fixed

- An axisymmetric run was shown as 3D, with Volume seeds offered, because Elmer writes its flat mesh with a Z extent of a few 1e-18 rather than zero.
- The run picker listed an opened bundle beside its indexed run; it now picks the indexed run.

## [0.1.0rc1] - 2026-10-17

The first public release candidate, superseded by 0.1.0 the same day.

[Unreleased]: https://github.com/femforge-io/femforge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/femforge-io/femforge/compare/v0.1.0rc1...v0.1.0
[0.1.0rc1]: https://github.com/femforge-io/femforge/releases/tag/v0.1.0rc1
