# Status

This page compares what femforge implements today with what its pipeline interfaces and its first solver backend, Elmer, offer. A row shows :material-check-circle:{ .ff-done } Implemented when the public config reaches it and the user guide documents it, :material-circle-half-full:{ .ff-partial } Partial when a module or interface exists that the config cannot reach or the docs do not cover, and :material-circle-outline:{ .ff-todo } Not started when femforge has no model for it.

## Pipeline interfaces

Each stage runs behind an interface, and one backend fills each interface today. [Concepts](concepts.md) walks through the pipeline that connects them.

| Stage | Today | Interface | What a new implementation needs |
| --- | --- | --- | --- |
| Geometry | A tagged STEP file from any CAD, or a [Build123dGeometry][femforge.geometry.Build123dGeometry] subclass | [GeometryArea][femforge.geometry.GeometryArea] | A `build_step(path)` that writes a STEP whose body names carry the tags. |
| Mesh backend | [GmshMesher][femforge.meshing.GmshMesher] in process, [WorkerMesher][femforge.meshing.WorkerMesher] in a child process | [MeshBackend][femforge.meshing.MeshBackend] | A `mesh(job, out_dir)` that turns a `MeshJob` into Elmer mesh files and a `MeshReport`. |
| Solver backend | Elmer, through the typed solver stack in `femforge.elmer.config` and the sif writer | None yet | Conditions and materials emit Elmer keywords directly, so a second solver needs a solver-neutral interface first; see [Solver objects](../contributing/architecture/solver-objects.md). |
| Solver execution | [NativeElmer][femforge.elmer.NativeElmer] for serial solves, [DockerElmer][femforge.elmer.DockerElmer] for serial and MPI solves | [ExecutionBackend][femforge.elmer.ExecutionBackend] | The `grid_convert`, `grid_partition` and `solve` methods and the `partitioned` flag of `src/femforge/elmer/runtime/backend.py`. |
| Optimization | [GridSearch][femforge.optimize.GridSearch], `find_max_current`, and the `convergence_ladder` and `richardson` mesh-convergence helpers in `femforge.run` | [Parameterization][femforge.optimize.Parameterization], [Objective][femforge.optimize.Objective], [DriverLike][femforge.optimize.DriverLike] | A search that evaluates configs from a `Parameterization` through a `DriverLike` and records them in a [Study][femforge.optimize.Study]. |
| Workbench | The trame workbench in `packages/femforge-workbench` | The `femforge.workbenches` entry point group | A function that returns a `Split`, declared as a [WorkbenchSpec][femforge_workbench.registry.WorkbenchSpec]; see [Workbench](../user-guide/workbench.md). |

## Frames

Elmer names eight coordinate systems. femforge maps two of them to a frame; see [Frames](../user-guide/frames/index.md).

| Elmer coordinate system | femforge | Status |
| --- | --- | --- |
| Cartesian 3D | [Solid][femforge.frames.Solid] | :material-check-circle:{ .ff-done title="Implemented" } |
| Axi Symmetric | [Axisymmetric][femforge.frames.Axisymmetric] | :material-check-circle:{ .ff-done title="Implemented" } |
| Cartesian 1D | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Cartesian 2D | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Polar 2D | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Polar 3D | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Cylindric | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Cylindric Symmetric | | :material-circle-outline:{ .ff-todo title="Not started" } |

`CoordinateSystem` in `femforge.elmer.config` already names `Cylindric`, `Cylindric Symmetric` and `Polar` as keyword values, but no frame maps a model onto them. A new frame is one [Frame][femforge.frames.Frame] subclass under `src/femforge/frames`; [Config objects](../contributing/architecture/config-objects.md#the-frame-area) lists the hooks it implements.

## Physics modules

The rows follow the chapters of the Elmer Models Manual. The femforge column names the [Physics][femforge.elmer.Physics] preset or the module that wires the Elmer module. A `Not started` module can still run through [GenericSolver][femforge.elmer.config.GenericSolver], which writes a hand-built solver block whose keywords femforge does not check. [Conditions and physics](../user-guide/conditions.md) documents each preset.

### Heat and thermal

| Elmer module | Solves | femforge | Status |
| --- | --- | --- | --- |
| `HeatSolve`, `HeatSolveVec` | Heat conduction, with optional convection, radiation and temperature-dependent properties. | `Physics.THERMAL`, `Physics.JOULE_HEATING` | :material-check-circle:{ .ff-done title="Implemented" } |
| `PhaseChangeSolve` (transient) | The moving front between a liquid and a solid phase over time. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `PhaseChangeSolve` (steady state) | The steady position of a liquid-solid phase boundary. | | :material-circle-outline:{ .ff-todo title="Not started" } |

### Electromagnetics

| Elmer module | Solves | femforge | Status |
| --- | --- | --- | --- |
| `StatCurrentSolve` | The potential and current density in a conductor, and the Joule losses they produce. | `Physics.CURRENT_CONDUCTION`, `Physics.JOULE_HEATING` | :material-check-circle:{ .ff-done title="Implemented" } |
| `MagnetoDynamics` | The 3D magnetic field in A-V form on edge elements, static, time-harmonic or transient. | `Physics.MAGNETOSTATICS`, `Physics.COIL_MAGNETOSTATICS`; static only | :material-check-circle:{ .ff-done title="Implemented" } |
| `MagnetoDynamics2D` | The magnetic field of planar or axisymmetric problems from one component of the vector potential. | `Physics.MAGNETOSTATICS_2D`; axisymmetric static only | :material-check-circle:{ .ff-done title="Implemented" } |
| `CoilSolver` | A consistent current distribution through a coil, as a source for the magnetic solvers. | `Physics.COIL_MAGNETOSTATICS` | :material-check-circle:{ .ff-done title="Implemented" } |
| `StatElecSolve` | The electrostatic potential and field in dielectrics and conductors. | `Physics.ELECTROSTATICS` | :material-circle-half-full:{ .ff-partial title="Partial" } |
| `CircuitsAndDynamics` | Circuit equations coupled to a magnetoquasistatic model. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `VectorHelmholtz` | Time-harmonic electromagnetic waves at high frequency. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `EMWaveSolver` | Time-domain electromagnetic waves on edge elements. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `MagneticSolve` | The magnetic induction equation in a conducting fluid. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `StatElecBoundary` | Electrostatics between nearly parallel surfaces, reduced to one dimension. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `PoissonBoltzmannSolve` | The electric potential with free charges that follow a Boltzmann distribution. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `FourierLoss` | Electromagnetic losses from an on-the-fly Fourier series, in the Steinmetz style. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ElectricForce` | The electrostatic force on a surface from a solved potential. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `StatMagSolve` | Axisymmetric magnetoquasistatics; Elmer marks it obsolete. | | :material-circle-outline:{ .ff-todo title="Not started" } |

`Physics.ELECTROSTATICS` has a preset and documentation. `FixedPotential` feeds it only when its `physics` field is set to `ELECTROSTATICS`, and no guide page, example or test exercises that path.

### Structural mechanics

| Elmer module | Solves | femforge | Status |
| --- | --- | --- | --- |
| `StressSolve` | Small displacements and stresses of a linear elastic solid. | `Physics.LINEAR_ELASTICITY` | :material-circle-half-full:{ .ff-partial title="Partial" } |
| `ElasticSolve` | Large deformations of an elastic solid. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ShellSolver` | Deformations of thin curved shells. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `Smitc` | Bending of linear elastic plates. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `BeamSolver3D` | Forces and moments in elastic beams. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `SpringAssembly` | Point springs and masses added to a structural model. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `RigidBodyReduction` | A displacement problem reduced by treating some blocks as rigid. | | :material-circle-outline:{ .ff-todo title="Not started" } |

`Physics.LINEAR_ELASTICITY` is a scaffold: no condition targets it, and the material model has no Poisson ratio.

### Fluids

| Elmer module | Solves | femforge | Status |
| --- | --- | --- | --- |
| `FlowSolve` | Incompressible or compressible viscous flow with the Navier-Stokes equations. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `AdvectionDiffusion` | Transport of a species or scalar by convection and diffusion. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `AdvectionReaction` | Transport of a passive scalar by a flow, with a reaction rate. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ReynoldsSolver` | Pressure in a thin fluid film. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `Electrokinetics` | Flow of an electrolyte in small channels driven by electric surface charges. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ParStokes` | Steady incompressible flow with block preconditioning for large systems. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `Stokes` | Incompressible flow in the rotational form of the Navier-Stokes equations. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `LevelSet` | The position of a free surface tracked with a level-set function. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `FreeSurfaceSolver` | A kinematic free surface, with limiters. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `FreeSurfaceReduced` | A free surface found from a constant flux condition. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `StructureFlowLine` (free surface) | A steady free surface in drawing and pulling processes, found along streamlines. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ArtificialCompressibility` | A stabilizing compressibility term for loosely coupled fluid-structure interaction. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `FluidicForce` | The pressure and viscous force a flow exerts on a surface. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `RichardsSolver` | Flow through variably saturated porous media. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ArteryOutlet` | An outlet boundary condition for blood flow in elastic arteries. | | :material-circle-outline:{ .ff-todo title="Not started" } |

### Acoustics and waves

| Elmer module | Solves | femforge | Status |
| --- | --- | --- | --- |
| `HelmholtzSolve` | Time-harmonic acoustic pressure with the Helmholtz equation. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `Acoustics` | Linearized Navier-Stokes acoustics with viscous and thermal losses, in the frequency domain. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `WaveSolver` | A generalized scalar wave equation in the time domain. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `CompressibleNS` | Large-amplitude, nonlinear waves in air. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `HelmholtzBEM` | The Helmholtz equation with the boundary element method. | | :material-circle-outline:{ .ff-todo title="Not started" } |

### Particles and other

| Elmer module | Solves | femforge | Status |
| --- | --- | --- | --- |
| `ParticleDynamics` | Trajectories of particles under forces and fields. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ParticleAdvector` | Diffusion-free advection of fields by tracking particles backward in time. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `StructureFlowLine` (moving mesh) | Ordinary differential equations along an extruded mesh that moves with a draw velocity. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ModelMixedPoisson` | The Poisson equation with the flux as a second unknown. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `PoissonBEM` | The Laplace equation with the boundary element method. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `BatterySolver` | Charge and species transport in a lithium-ion cell. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `DFTSolver` | Electronic structure with density functional theory; experimental in Elmer. | | :material-circle-outline:{ .ff-todo title="Not started" } |

### Utilities

| Elmer module | Solves | femforge | Status |
| --- | --- | --- | --- |
| `SaveScalars` | Reductions and integrals of fields, written as scalar values to a file. | `src/femforge/elmer/config/output/save_scalars.py` | :material-check-circle:{ .ff-done title="Implemented" } |
| `ResultOutputSolve` | Field output in VTU and other formats. | `src/femforge/elmer/config/output/result_output.py` | :material-check-circle:{ .ff-done title="Implemented" } |
| `MeshSolve` | Mesh deformation that follows moving boundaries. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `NonphysicalMeshSolve` | Mesh deformation where the mesh velocity does not matter. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `RigidMeshMapper` | Rigid rotation and translation of mesh bodies. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `StructuredMeshMapper` | A structured mesh fitted between given top and bottom surfaces. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ElementStats` | Quality statistics of the mesh elements. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `StreamSolver` | Streamlines of a flow field. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `FluxSolver` | The flux of a Poisson-type field, such as heat flux. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `VorticitySolver` | The curl of a vector field. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `DivergenceSolver` | The divergence of a vector field. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ScalarPotentialSolver` | The scalar potential whose gradient matches a given flux. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `FilterTimeSeries` | Averages and Fourier filters over time-series results. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `DataToFieldSolver` | A smooth field fitted to scattered data. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ProjectToPlane` | A 3D field projected onto a plane. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `StructureProjectToPlane` | A field on a structured mesh projected onto its base surface. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `FindOptimum` | New design parameters from a cost function, inside one Elmer run. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `SaveLine` | Field values along lines through the mesh, written to a file. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `SaveMaterials`, `SaveBoundaryValues` | Material and boundary parameters stored as fields. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `SaveGridData` | Field values on a uniform Cartesian grid. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `Isosurface` | Isosurfaces or isolines extracted to reduce output. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `Elmer2OpenFoamIO`, `OpenFOAM2ElmerIO` | Field exchange with OpenFOAM through files. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `Probes` | Integrals of expressions over bodies. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `GmshOutputReader` | Fields read from a gmsh result file. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ReloadData` | Earlier results loaded back during a run. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `ReloadInput` | The solver input reloaded while a run is in progress. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `GridDataReader` | NetCDF grid data interpolated onto the mesh. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Linear constraints (`SolverUtils`) | Linear constraints on a solve, enforced with Lagrange multipliers. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| `XdmfWriter` | Parallel results written as Xdmf and HDF5. | | :material-circle-outline:{ .ff-todo title="Not started" } |

femforge runs its sweeps and optimizations outside Elmer, so it uses none of the Elmer optimization utilities.

## Solver capabilities

The rows follow the chapters of the ElmerSolver Manual that describe a capability a user switches on. The chapters on fundamentals, the input file format, compilation and programming, and the appendices on file formats and element definitions are background and have no row.

| Elmer capability | Solves | femforge | Status |
| --- | --- | --- | --- |
| Linear system methods | Direct, Krylov and multilevel solvers with their preconditioners. | [LinearSystem][femforge.elmer.config.LinearSystem] fields on each solver | :material-check-circle:{ .ff-done title="Implemented" } |
| Nonlinear system options | Picard and Newton iteration, relaxation and convergence criteria. | [NonlinearSystem][femforge.elmer.config.NonlinearSystem] fields on each solver | :material-check-circle:{ .ff-done title="Implemented" } |
| Parallel runs | Mesh partitioning and MPI solves. | [DockerElmer][femforge.elmer.DockerElmer] with `mpi_processes`; see [Elmer: Native and Docker](../user-guide/elmer.md) | :material-check-circle:{ .ff-done title="Implemented" } |
| Restart | A run that starts from saved solutions. | `SimulationSettings.output_file` writes a restart file; nothing reads one back | :material-circle-half-full:{ .ff-partial title="Partial" } |
| Time integration | Transient solves with BDF, Newmark and other schemes. | `SimulationType.TRANSIENT` and `TimesteppingMethod`; every preset is steady state | :material-circle-half-full:{ .ff-partial title="Partial" } |
| Dirichlet conditions and limiters | Fixed values on boundaries, and soft limits on a solution. | Fixed values through `FixedTemperature` and `FixedPotential`; no limiters | :material-circle-half-full:{ .ff-partial title="Partial" } |
| Generic solver utilities | Solver activation, variable naming, exported variables and passive elements. | `exec_solver` and `variable` fields on [SolverBase][femforge.elmer.config.SolverBase] | :material-circle-half-full:{ .ff-partial title="Partial" } |
| Eigenvalue problems | Modes and eigenfrequencies of a system. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Shared boundary conditions | Periodic, mortar and conforming conditions between boundaries. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Linear constraints | Extra linear equations that couple degrees of freedom. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Constraint modes | The response to unit loads on chosen boundaries, as in a lumped model. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Nodal loads | Point loads, and the reaction loads of a solved system. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Block-matrix coupling | Tightly coupled multiphysics solved as one block system. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Meshing utilities | Coordinate transformation, mesh multiplication, extrusion and discontinuous interfaces inside Elmer. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Adaptive solution | Mesh refinement driven by an error estimate. | | :material-circle-outline:{ .ff-todo title="Not started" } |
| Higher-order elements | p-elements of higher polynomial order. | | :material-circle-outline:{ .ff-todo title="Not started" } |

## Conditions and materials

Every condition and material below is implemented. [Conditions and physics](../user-guide/conditions.md) and [Materials](../user-guide/materials.md) document them.

| Condition | Feeds | Target |
| --- | --- | --- |
| [Magnetization][femforge.physical.Magnetization] | `MAGNETOSTATICS`, `MAGNETOSTATICS_2D`, `COIL_MAGNETOSTATICS` | body |
| [CalculateMagneticForce][femforge.physical.CalculateMagneticForce] | `MAGNETOSTATICS`, `MAGNETOSTATICS_2D`, `COIL_MAGNETOSTATICS` | body |
| [CurrentDensity][femforge.physical.CurrentDensity] | `MAGNETOSTATICS_2D` | body |
| [CoilDrive][femforge.physical.CoilDrive] | `COIL_MAGNETOSTATICS` | body |
| [CoilTerminal][femforge.physical.CoilTerminal] | `COIL_MAGNETOSTATICS` | outer shell |
| [FixedTemperature][femforge.physical.FixedTemperature] | `THERMAL`, `JOULE_HEATING` | boundary |
| [HeatFlux][femforge.physical.HeatFlux] | `THERMAL`, `JOULE_HEATING` | boundary |
| [Convection][femforge.physical.Convection] | `THERMAL`, `JOULE_HEATING` | outer shell |
| [HeatSource][femforge.physical.HeatSource] | `THERMAL`, `JOULE_HEATING` | body |
| [FixedPotential][femforge.physical.FixedPotential] | `CURRENT_CONDUCTION`, `JOULE_HEATING` | boundary |
| [JouleHeat][femforge.physical.JouleHeat] | `JOULE_HEATING` | body |

| Material | Tag | Notable properties |
| --- | --- | --- |
| [Air][femforge.materials.Air] | `Air` | μr = 1, near-zero conductivity |
| [Copper][femforge.materials.Copper] | `Copper` | Temperature-dependent conductivity |
| [N52][femforge.materials.N52], [N42][femforge.materials.N42] | `N52`, `N42` | Temperature-dependent remanence and coercivity |
| [FR4][femforge.materials.FR4] | `FR4` | εr = 4.5, glass transition temperature |
| [Steel_1080][femforge.materials.Steel_1080] | `Steel1080` | Nonlinear B-H curve, or a linear μr |
| [Steel_316][femforge.materials.Steel_316] | `Steel316` | μr = 1.008, effectively non-magnetic |

## Picking a task

A row marked :material-circle-outline:{ .ff-todo } or :material-circle-half-full:{ .ff-partial } is a candidate task. [Contributing](../contributing/index.md) sets up a development checkout, and [Solver objects](../contributing/architecture/solver-objects.md) shows how a solver model, a preset and a condition slot into the sif writer. Open or claim a task on the [GitHub issues page](https://github.com/femforge-io/femforge/issues).
