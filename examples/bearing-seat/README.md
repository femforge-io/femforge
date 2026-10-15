# Bearing seat

A neodymium disc magnet holds a steel bearing ball in a spherical seat. The example compares the pull on the ball with and without a steel shell around the magnet, for 2, 4 and 8 mm magnets, on the axisymmetric 2D section. It then solves the 4 mm magnet with the shell in 3D. A browser workbench lists each run as it finishes.

## Run it

You need Docker running and a clone of this repository. The [install guide](https://docs.femforge.io/overview/install/) covers Docker and uv. From the repository root, run:

```
uv run examples/bearing-seat/main.py
```

uv sets up the environment on the first run. The seven solves then take about two minutes. The terminal logs the pull for each case, and your browser opens the workbench at http://localhost:8080 while they run. Select a run in the **Runs** list to see its field. Press `Ctrl+C` to stop.

`uv run examples/bearing-seat/sweeps.py` lifts the ball off its seat in 1/8 mm steps and writes a ParaView animation to `runs/z-sweep/animation.pvd`.

## Files

- `geometry.py` builds the seat, the ball and the shell in build123d.
- `main.py` configures the solve, runs the shell comparison and the 3D check, and starts the workbench.
- `ui.py` lays out the workbench.
- `sweeps.py` runs the lift sweep.
- `paths.py` names the folders the runs and the run index land in, `runs/` and `runs-index/` next to these files.
