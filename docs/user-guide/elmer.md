# Elmer: native and Docker

Solving runs through the [ExecutionBackend][femforge.elmer.ExecutionBackend] interface, and Elmer is the solver implemented today. femforge writes the solver input, runs Elmer's two programs, ElmerGrid and ElmerSolver, as separate processes, and reads the results. The backend decides where they run: [NativeElmer][femforge.elmer.NativeElmer] runs an Elmer install on this machine, and [DockerElmer][femforge.elmer.DockerElmer] runs each command in a container of the femforge Elmer image. The backend is a driver setting, never part of the config, so a run solved natively and the same run solved in Docker share one run id.

## Pick a backend

| | [NativeElmer][femforge.elmer.NativeElmer] | [DockerElmer][femforge.elmer.DockerElmer] |
|---|---|---|
| You install | [Elmer](https://www.elmerfem.org/blog/binaries/) | [Docker](https://docs.docker.com/get-started/get-docker/) |
| Elmer version | whatever you installed | the one the image pins |
| Parallel solves | no | yes, with Open MPI and MUMPS |
| First-run cost | none | a 1.3 GB pull, or a build of a few minutes |
| Per-command cost | none | a container start |

Pick Docker for parallel solves and for the same Elmer on every machine. Pick native Elmer when you already run an Elmer install, or where Docker is unavailable.

```python
import femforge as ff

native = ff.Driver(backend=ff.elmer.NativeElmer())
docker = ff.Driver(backend=ff.elmer.DockerElmer(), mpi_processes=4)
```

Without `backend`, the driver runs native Elmer for a serial solve when it is installed, and Docker for a parallel solve or when no native Elmer is found. [Driver and runs](driver.md#execution-backends) lists every combination of `docker`, `docker_image` and `mpi_processes`.

## Native Elmer

`NativeElmer` finds the tools on its first command and keeps them:

| Tool | Required | Looked for |
|---|---|---|
| `ElmerGrid` | yes | `PATH`, then `C:\Program Files\Elmer *\bin` |
| `ElmerSolver` | yes | `PATH`, then `C:\Program Files\Elmer *\bin` |
| `ElmerSolver_mpi` | no | `PATH`, then `C:\Program Files\Elmer *\bin` |
| `mpiexec` | no | `PATH`, then `C:\Program Files\Microsoft MPI\Bin\mpiexec.exe` |

A missing required tool raises `FileNotFoundError` naming it. To run an install outside these places, put its `bin` directory on `PATH`, or pass the paths:

```python
import femforge as ff
from femforge.elmer.runtime.tools import ElmerToolsLocations

tools = ElmerToolsLocations(
    ElmerGrid="/opt/elmer/bin/ElmerGrid",
    ElmerSolver="/opt/elmer/bin/ElmerSolver",
)
driver = ff.Driver(backend=ff.elmer.NativeElmer(tools))
```

**A native backend runs serial solves only.** Native Elmer builds lack MUMPS, the direct solver a partitioned solve needs, so `NativeElmer` reports itself as not partitioned and a driver with `mpi_processes` above 1 raises at construction. femforge still records `ElmerSolver_mpi` and `mpiexec` when it finds them, and `femforge doctor` reports whether the install has MPI.

## Docker

`DockerElmer()` runs in `ghcr.io/femforge-io/femforge-elmer:0.2.0`: Ubuntu 24.04, pinned by digest, with headless Elmer, Open MPI and MUMPS from CSC's `elmerfem-csc` package. [femforge.elmer.runtime.docker_image][] holds the recipe that generates its Dockerfile. Print it with `femforge image dockerfile`.

### How femforge gets the image

Before its first command in a process, the backend makes sure Docker holds the image:

1. It reuses a local image with the tag when the image's `femforge.recipe` label, the SHA-256 of its Dockerfile, matches the current recipe.
2. Else it pulls the released image from GHCR, and keeps it when the label matches.
3. Else, when the pull fails or the pulled image came from another recipe, it logs a warning and builds the image from the recipe. The build takes a few minutes, once.

It checks each tag once per process, and concurrent solves in one process pull or build it once. Run `femforge image pull` or `femforge image build` to fetch it ahead of time.

### Development images

When the recipe in your checkout differs from the released one, the backend uses a development image named `femforge-elmer:<version>-dev.<hash8>`, from the first 8 hex digits of the recipe's hash. A development image is never published, so the backend builds it locally on first use instead of pulling. `femforge image bump` raises the image version after a recipe change, and the release workflow refuses to publish a development tag.

### Platform

The CSC package exists only for amd64, so femforge pulls, builds and runs the image with `--platform linux/amd64` on every host. You do not need to set `DOCKER_DEFAULT_PLATFORM`. On an arm64 host, such as a Mac with Apple silicon, Docker runs the image under emulation and solves run slower; `femforge doctor` warns about it, and Docker Desktop's Rosetta emulation setting speeds them up.

### Containers, mounts and users

Each ElmerGrid or ElmerSolver command runs in a fresh container, removed when it exits and named `femforge_<hex>`. The container binds the host directory it works on at `/case`: the mesh directory for ElmerGrid, the run directory for ElmerSolver.

On Linux with a rootful daemon, each container runs as your `uid:gid`, so the files it writes into the run directory stay yours. With a rootless daemon, and on Windows and macOS, it runs as the image's user.

### Parallel solves

With `mpi_processes` above 1, ElmerGrid partitions the mesh in a container, then the solve runs as `mpirun --oversubscribe -np <n> ElmerSolver_mpi`. `--oversubscribe` lets the rank count exceed the host's physical cores, because it must match the partition count. [Driver and runs](driver.md#parallel-solves) covers what the driver changes in the solver stack before a parallel solve.

### Timeouts

Every backend command takes `timeout_s` and a cancel token, and [solve_case][femforge.elmer.solve_case] passes them through. A timed-out Docker command kills its container by name, since stopping the `docker` client alone leaves the container running. The image is fetched before the clock starts, so a build never counts against a timeout. A pull stops after 30 minutes and logs each finished layer and a heartbeat every 15 s. The driver sets no timeout; its watchdog stops a linear solve that stopped converging ([Stalled solves](driver.md#stalled-solves)).

### Another image

Pass a [DockerImage][femforge.execution.docker.image.DockerImage] to run Elmer in an image of your own:

```python
import femforge as ff
from femforge.execution.docker.image import DockerImage

image = DockerImage("registry.example.com/elmer:9.0", platform="linux/amd64")
driver = ff.Driver(backend=ff.elmer.DockerElmer(image))
```

An image without a recipe is pulled when Docker lacks it and never built, so a failed pull raises `DockerError`. Without `platform`, Docker picks the host's. `Driver(docker_image="registry.example.com/elmer:9.0")` does the same with the host's platform.

## The femforge image commands

| Command | Does |
|---|---|
| `femforge image dockerfile` | Print the generated Dockerfile. |
| `femforge image tag` | Print the tag this checkout's recipe resolves to: the released tag, or a development tag. |
| `femforge image pull` | Pull the released image; fails when the recipe is unreleased. |
| `femforge image build [--no-pull]` | Pull or build the image unless Docker holds it; `--no-pull` builds without trying the registry. |
| `femforge image bump [--part PART]` | Raise the `major`, `minor` or `patch` part of the image version, `patch` by default, when the recipe changed since the last bump; it rewrites femforge's source, so run it from a checkout. |

Each command takes the image name as an optional argument, `femforge-elmer` by default. [CLI](cli.md) lists every option.

## Diagnose with femforge doctor

```console
femforge doctor
```

The doctor prints one row per check with the fix for each problem, and exits with code 1 when a check fails. These checks concern Elmer:

| Check | Reports |
|---|---|
| Docker | the daemon version, system and architecture, rootless or not; a warning when `docker` is missing, the daemon does not answer, or the host is arm64 |
| Elmer image | the tag and its `elmerfem-csc` version when Docker holds it; else whether the registry has it, lacks it or denies anonymous reads |
| Native Elmer | where `ElmerSolver` is and whether MPI is installed; a failure only when Docker is unusable too |
| Docker disk | the free space under Docker's root directory |
| Solve path | whether Elmer runs natively, in Docker or both |

[Install](../overview/install.md#check-the-install) lists the other checks.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Could not pull the image; building it instead.` | The registry package is private, missing, or out of reach. | Wait for the one-time build, or run `femforge image build` ahead of time. `femforge doctor` names the cause. |
| `The pulled image was built from another recipe; building it instead.` | Your checkout's recipe differs from the one the released image was built from. | Wait for the one-time build. |
| ``The Docker backend needs `docker` on PATH.`` | Docker is not installed, or its `bin` directory is not on `PATH`. | Install Docker. |
| `docker version exited with code`, with the daemon error | The Docker daemon is not running. | Start Docker Desktop, or run `sudo systemctl start docker` on Linux. |
| `permission denied` on the Docker socket | Your Linux user cannot reach the daemon. | Run `sudo usermod -aG docker $USER`, then log out and back in. |
| Slow solves on an arm64 host | The amd64 image runs under emulation. | Turn on Docker Desktop's Rosetta emulation setting. |
| `Could not locate ElmerGrid executable` | Native Elmer is not on `PATH` or in its default directory. | Add its `bin` directory to `PATH`, or pass [the paths](#native-elmer). |
| `mpi_processes > 1 needs a partitioned backend` | A parallel solve on `NativeElmer`. | Use `DockerElmer`. |

## Image licenses

The `femforge-elmer` image redistributes Elmer FEM and Ubuntu 24.04 packages, each under its own license. Elmer's ElmerSolver and ElmerGrid are GPL-2.0-or-later, and the ElmerSolver library is LGPL-2.1-or-later; the image's `org.opencontainers.image.licenses` label records `GPL-2.0-or-later AND LGPL-2.1-or-later`. femforge itself is Apache-2.0 and runs Elmer as a separate program.

The license texts ship inside the image. Each Ubuntu package keeps its own in `/usr/share/doc/<package>/copyright`. The `elmerfem-csc` package keeps Elmer's in `/usr/share/elmersolver/license_texts/`: `LICENSES`, `GPL-2.txt`, `LGPL-2.1.txt`, and `fem/LICENSES` for the libraries bundled into the solver. Print Elmer's notice with:

```console
docker run --rm --platform linux/amd64 ghcr.io/femforge-io/femforge-elmer:0.2.0 cat /usr/share/elmersolver/license_texts/LICENSES
```

List every package's copyright file with:

```console
docker run --rm --platform linux/amd64 ghcr.io/femforge-io/femforge-elmer:0.2.0 sh -c "ls /usr/share/doc/*/copyright"
```

| Component | Source |
|---|---|
| Elmer FEM, the exact `elmerfem-csc` build in the image | the `elmerfem-csc-<elmer version>-source.tar.gz` attached to the [`femforge-elmer-v<version>` release](https://github.com/femforge-io/femforge/releases), for example `femforge-elmer-v0.2.0`; the release workflow downloads it from CSC's PPA when it publishes the image |
| Elmer FEM upstream | [ElmerCSC/elmerfem](https://github.com/ElmerCSC/elmerfem) |
| Ubuntu 24.04 packages | the Ubuntu archive, through `apt-get source <package>` |
