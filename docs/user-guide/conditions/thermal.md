# Thermal

`THERMAL` solves steady-state heat conduction, in 3D under a solid frame, on the r-z half-plane under an axisymmetric frame and on a planar section under a Cartesian 2D frame. See [Conditions and physics](index.md) for how conditions attach and where they land.

```python exec="true" session="thermal"
from femforge.elmer import Physics, SifSection
from femforge.physical import Convection, FixedTemperature, HeatFlux, HeatSource
from femforge_docs.conditions import block_table, condition_details, solves_table

print(solves_table(Physics.THERMAL))
```

## Boundary conditions

```python exec="true" session="thermal"
print(block_table(Physics.THERMAL, SifSection.BOUNDARY_CONDITION))
```

A thermal solve needs at least one boundary condition, or the sif writer warns that the solve is likely ill-posed.

### FixedTemperature

```python exec="true" session="thermal"
print(condition_details(FixedTemperature))
```

### HeatFlux

```python exec="true" session="thermal"
print(condition_details(HeatFlux))
```

### Convection

```python exec="true" session="thermal"
print(condition_details(Convection))
```

## Body forces

```python exec="true" session="thermal"
print(block_table(Physics.THERMAL, SifSection.BODY_FORCE))
```

### HeatSource

```python exec="true" session="thermal"
print(condition_details(HeatSource))
```
