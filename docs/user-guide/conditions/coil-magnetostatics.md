# Coil magnetostatics

`COIL_MAGNETOSTATICS` chains current conduction into magnetostatics: it solves a coil's current distribution, then the magnetic field that current drives. It also reads the [Magnetostatics](magnetostatics.md) conditions, so magnets and force Components work as they do there. See [Conditions and physics](index.md) for how conditions attach and where they land.

```python exec="true" session="coil-magnetostatics"
from femforge.elmer import Physics, SifSection
from femforge.physical import CoilDrive, CoilTerminal
from femforge_docs.conditions import block_table, condition_details, solves_table

print(solves_table(Physics.COIL_MAGNETOSTATICS))
```

## Material-wired conditions

```python exec="true" session="coil-magnetostatics"
print(block_table(Physics.COIL_MAGNETOSTATICS, SifSection.MATERIAL))
```

## Boundary conditions

```python exec="true" session="coil-magnetostatics"
print(block_table(Physics.COIL_MAGNETOSTATICS, SifSection.BOUNDARY_CONDITION))
```

### CoilTerminal

```python exec="true" session="coil-magnetostatics"
print(condition_details(CoilTerminal))
```

The electrode's end face must lie on the domain boundary (`GeometryConfig.terminate_electrodes`). Positive coil current flows from the start electrode to the end electrode.

## Components

```python exec="true" session="coil-magnetostatics"
print(block_table(Physics.COIL_MAGNETOSTATICS, SifSection.COMPONENT))
```

### CoilDrive

```python exec="true" session="coil-magnetostatics"
print(condition_details(CoilDrive))
```

Bodies that share one `CoilDrive` instance form one coil. Reverse a coil by swapping its terminal roles, and keep `current` positive. Without a `CoilDrive`, the sif writer warns that the coil has no source. `COIL_MAGNETOSTATICS` leaves `FixedPotential` inert.
