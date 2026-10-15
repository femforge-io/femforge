# $name

A magnetostatic simulation made with [femforge](https://femforge.io): a disc magnet pulls on a steel plate across an air gap. `main.py` solves the pull at four gaps and shows the magnetic field in a browser workbench.

## Getting started

You need two tools: **Docker** and **uv**. You do not need Python. uv installs it for you.

You also need about 5 GB of free disk space.

### Step 1: Install Docker

Docker runs the solver. If Elmer is already installed on your machine, femforge uses it instead and Docker is not needed.

- **Windows:** install [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/). Accept the WSL 2 option.
- **macOS:** install [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/). Pick the download that matches your Mac's chip.
- **Linux:** install [Docker Engine](https://docs.docker.com/engine/install/), then do the [post-install steps](https://docs.docker.com/engine/install/linux-postinstall/).

Open Docker Desktop and leave it running. Check that Docker works:

```
docker -v
```

You see a version, such as `Docker version 28.4.0`.

### Step 2: Install uv

uv installs Python and the packages this project needs.

**Windows** (PowerShell):

```
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS and Linux:**

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close your terminal and open a new one. Check that uv works:

```
uv --version
```

### Step 3: Run

Open a terminal in this folder and run:

```
uv run main.py
```

The terminal logs the force on the plate for each gap and the run that holds it. Then your browser opens the workbench at http://localhost:8080 on the magnetic field of the last gap. Pick another run in the **Run** list to compare. Press `Ctrl+C` in the terminal to stop.

## The first run is slow

The first run downloads everything once. Later runs start in seconds.

1. uv downloads Python and the packages: about 1.2 GB, a few minutes.
2. When Elmer is not installed natively, femforge downloads the solver's Docker image: about 1.3 GB. The terminal shows the progress.
3. The four solves run, each in seconds.

Results are saved in `runs/` and `runs-index/`. Delete both folders to start fresh.

## Change the model

Everything lives in `main.py`:

- `MagnetAndPlate` holds the dimensions, in millimetres.
- `config()` sets the materials, the magnetization direction and the mesh sizes.
- `GAPS` lists the gaps to solve.
- `viewer()` sets what the workbench shows first.

Run `uv run main.py` again after a change. femforge solves only what changed: a gap it solved before loads from `runs/`.

## Pin the versions

`main.py` asks for the newest femforge 0.1 release. To pin every package at the version you have now, run:

```
uv lock --script main.py
```

uv writes `main.py.lock` next to the script and uses it on every run. Share both files to give someone the exact same setup.

## Troubleshooting

Run `uvx femforge doctor` first. It checks gmsh, Docker, the solver image and your free disk space, and prints the fix for each problem it finds.

<details>
<summary>Common problems</summary>

**`uv` or `docker` is not recognized**\
Open a new terminal. If that does not work, repeat the install step.

**`Cannot connect to the Docker daemon`**\
Start Docker Desktop. On Linux, run `sudo systemctl start docker`.

**The image download fails**\
femforge then builds the image itself, which takes a few minutes longer. If the build fails too, check your internet connection and run the command again.

**The browser does not open**\
Open http://localhost:8080 yourself.

**Port 8080 is already in use**\
Stop the other program that uses port 8080, or an earlier run of this project.

**Solves are slow on a Mac with Apple silicon**\
The solver image exists only for Intel and AMD processors, so Docker emulates it. Solves still work, only slower.

</details>
