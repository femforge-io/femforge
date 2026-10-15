# Thermal

A copper puck dissipates 1 W on a square FR4 board, and still air cools every exposed face. The example solves the steady temperature in 3D, logs the peak and its rise over the air, and shows the temperature field in a browser workbench.

## Run it

You need Docker running and a clone of this repository. The [install guide](https://docs.femforge.io/overview/install/) covers Docker and uv. From the repository root, run:

```
uv run examples/thermal/main.py
```

The solve takes seconds. The terminal logs the peak temperature, and your browser opens the workbench at http://localhost:8080. Press `Ctrl+C` to stop.

## What to change

- `PuckOnBoard` holds the dimensions, in millimetres.
- `config(power)` sets the heat source in watts, and `FILM` and `AMBIENT` set the cooling.

[Conditions](https://docs.femforge.io/user-guide/conditions/) explains `HeatSource`, `Convection` and the other thermal conditions, and where each one lands.
