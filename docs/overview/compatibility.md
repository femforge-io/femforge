# Compatibility

femforge depends on a Python interpreter, an Elmer build and, for the workbench, a VTK client stack whose versions must agree. This page lists the versions femforge supports and tests, and where to report a problem.

## Python

| Version | Supported | Tested in CI |
| --- | --- | --- |
| 3.12 | yes | yes |
| 3.13 | yes | yes |
| 3.14 | yes | yes |

Both `femforge` and `femforge-workbench` require Python 3.12 or later. The Quality workflow runs type checks, lint and the test suite on each of the three versions.

## Operating systems

| System | Status |
| --- | --- |
| Linux | Every CI workflow runs on Ubuntu 24.04 runners. gmsh needs the [system libraries](install.md#linux-system-libraries) listed on the install page. |
| Windows | Native Elmer installs under `C:\Program Files\Elmer *\bin` are found without `PATH` changes, and MS-MPI's `mpiexec` is found at its default location. Docker Desktop runs the Elmer image. CI does not run on Windows. |
| macOS | Docker Desktop runs the Elmer image; on Apple silicon it runs under emulation. CI does not run on macOS. |

## Elmer

| Source | What you get |
| --- | --- |
| Docker image | `ghcr.io/femforge-io/femforge-elmer:0.2.0`: Ubuntu 24.04, pinned by digest, with the `elmerfem-csc` package from CSC's Elmer PPA, which ships ElmerGrid and ElmerSolver with Open MPI and MUMPS |
| Native install | any Elmer that provides `ElmerGrid` and `ElmerSolver`, such as the [Elmer binaries](https://www.elmerfem.org/blog/binaries/) for Windows; serial solves only |

The image tag pins the recipe, and the PPA decides the Elmer version inside it. `femforge doctor` prints the `elmerfem-csc` version that the local image holds. The CI `docker-e2e` job solves through the image on a runner without a native Elmer.

## Docker

The image is built for `linux/amd64` only, because the CSC PPA publishes no arm64 packages. femforge passes `--platform linux/amd64` when it pulls, builds and runs the image, so an arm64 host such as an Apple-silicon Mac emulates it without extra settings. Solves run slower under emulation; `femforge doctor` warns when the Docker daemon is arm64, and Docker Desktop's Rosetta setting speeds emulation up.

## Pinned versions

The workbench pins the minor versions of its 3D client stack, because it works around defects that are specific to them:

| Package | Range | Why it is pinned |
| --- | --- | --- |
| `vtk` | `>=9.7.1,<9.8` | `mirror_friendly` and `refresh_composites` in `panels/scene.py` work around VTK.wasm 9.7 serialization defects |
| `trame-vtklocal` | `>=1.7.1,<1.8` | `tolerate_dropped_objects` in `panels/scene.py` patches a status request that raises on 1.7.1 |
| `pyvista` | `>=0.49.0,<0.50` | `mirror_friendly` moves the 64-bit cell arrays that 0.49 filters such as `clip_box` leave to 32-bit storage |

femforge itself pins `build123d` to the 0.13 minor and `polars` to the 2.x major. The workbench needs trame 4 and its React client (`trame>=4.0.0`, `trame-client>=4.1`). The workbench `ocp` extra pins `ocp_vscode` to the 4.1 and 4.2 minors.

## Report a problem

Open an issue at [github.com/femforge-io/femforge/issues](https://github.com/femforge-io/femforge/issues). Include the output of `femforge doctor --json`.
