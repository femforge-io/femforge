# Install

femforge is a Python package plus an Elmer solver that runs beside it. The package meshes with gmsh in Python; Elmer runs either from a native install or inside the femforge Docker image. This page installs both and checks the result with `femforge doctor`.

## Requirements

- Python 3.12, 3.13 or 3.14.
- Docker, or a native Elmer install.
- About 5 GB of free disk space. `femforge doctor` warns below 5 GB and fails below 2 GB.

## Install the package

| | Command | Installed size | Pick it for |
| --- | --- | --- | --- |
| **Recommended** | `uv add "femforge[workbench]"` | about 1.2&nbsp;GB | solves, field plots and the browser [workbench](../user-guide/workbench.md) |
| Headless | `uv add femforge` | about 0.8&nbsp;GB | servers, CI and pipelines that need solves and typed results only |

With pip, `pip install "femforge[workbench]"` or `pip install femforge`. The `workbench` extra installs `femforge-workbench` at the same version, which brings the `viz` extra, trame and plotly. The `viz` extra alone adds pyvista, vtk, matplotlib, meshio and magpylib for field rendering ([`femforge.viz`](../reference/viz.md)) and the analytic cross-check ([`femforge.validation`](../reference/validation.md)) without the browser workbench, which costs only 20 MB more.

The `femforge-workbench` package has extras of its own:

| Extra | Adds | Needed for |
| --- | --- | --- |
| `app` | pywebview | the `--webview` window |
| `ocp` | ocp_vscode | sending CAD to the OCP CAD Viewer |
| `pty` | pywinpty, on Windows only | an interactive terminal panel on Windows |

## Linux system libraries

The gmsh wheel loads OpenGL and X11 libraries that a minimal Linux install lacks. On Debian or Ubuntu, install them with:

```console
sudo apt-get install -y --no-install-recommends libgl1 libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1
```

`femforge doctor` names this command when the gmsh check fails on a missing library.

## Choose how Elmer runs

| Backend | You install | femforge finds it | Parallel solves |
| --- | --- | --- | --- |
| [DockerElmer][femforge.elmer.DockerElmer] | [Docker](https://docs.docker.com/get-started/get-docker/) | pulls `ghcr.io/femforge-io/femforge-elmer:0.2.0` on the first solve | yes, through Open MPI and MUMPS |
| [NativeElmer][femforge.elmer.NativeElmer] | [Elmer](https://www.elmerfem.org/blog/binaries/) | `ElmerGrid` and `ElmerSolver` on `PATH`, else under `C:\Program Files\Elmer *\bin` on Windows | no |

Pass the backend to the driver:

```python
import femforge as ff

docker_driver = ff.Driver(backend=ff.elmer.DockerElmer())
native_driver = ff.Driver(backend=ff.elmer.NativeElmer())
```

Without a `backend`, the driver runs native Elmer when `ElmerSolver` and `ElmerGrid` are installed and the solve is serial, else Docker.

Before its first command, the Docker backend checks for the image. It reuses a local image built from the current recipe, else pulls the released image from GHCR. When the pull fails, or the pulled image was built from a different recipe, it builds the image locally from the recipe in [`femforge.elmer.runtime.docker_image`](../reference/execution.md). The build takes a few minutes, once. To fetch the image ahead of time, run:

```console
femforge image pull
```

femforge also records `ElmerSolver_mpi` and an MPI launcher such as MS-MPI's `mpiexec` when a native install has them. A native build lacks MUMPS, so partitioned solves run only in Docker. [Elmer: native and Docker](../user-guide/elmer.md) covers both backends in depth.

## Check the install

```console
femforge doctor
```

The doctor prints one row per check and the fix for each failure. It exits with code 1 when a check fails. Pass `--json` for a machine-readable report and `--runs-dir` to check the disk of a runs directory other than `runs`.

| Check | What it looks at |
| --- | --- |
| Python | the interpreter version and path |
| gmsh | that gmsh imports in a child process |
| CAD kernel | that build123d and its OCP kernel import |
| Docker | the Docker daemon, and whether it emulates amd64 |
| Elmer image | whether Docker holds the image, or the registry has it |
| Native Elmer | `ElmerSolver`, `ElmerGrid` and the MPI pair |
| Runs disk | free space under the runs directory |
| Artifacts disk | free space under `FEMFORGE_ARTIFACTS`, when set |
| Docker disk | free space under the Docker root directory |
| Solve path | that Elmer runs natively, in Docker or both |

## First-run cost

The first run downloads about 1.2 GB of packages and, without a native Elmer, pulls a 1.3 GB image. Later runs reuse both.
