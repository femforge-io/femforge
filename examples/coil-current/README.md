# Coil current

A copper coil inside a steel pot frame pulls a steel plunger toward the frame's bottom plate. The example drives the coil with a current density, solves the axisymmetric section at a 2 mm and a 4 mm air gap under the plunger, logs the pull on the plunger, and shows the field in a browser workbench.

## Run it

You need Docker running and a clone of this repository. The [install guide](https://docs.femforge.io/overview/install/) covers Docker and uv. From the repository root, run:

```
uv run examples/coil-current/main.py
```

The two solves take seconds. The terminal logs the force on the plunger for each gap, negative toward the bottom plate, and your browser opens the workbench at http://localhost:8080. Press `Ctrl+C` to stop.

## What to change

- `Solenoid` holds the dimensions, in millimetres.
- `CURRENT_DENSITY` sets the drive, in A/m².
- `GAPS` lists the gaps to solve.

[Conditions](https://docs.femforge.io/user-guide/conditions/) explains `CurrentDensity`, `ForceComponent` and the other magnetic conditions, and where each one lands.
