# Current conduction

`CURRENT_CONDUCTION` solves the static current density that electrode potentials drive through the conductors. See [Conditions and physics](index.md) for how conditions attach and where they land.

```python exec="true" session="current-conduction"
from femforge.elmer import Physics, SifSection
from femforge.physical import FixedPotential
from femforge_docs.conditions import block_table, condition_details, solves_table

print(solves_table(Physics.CURRENT_CONDUCTION))
```

## Boundary conditions

```python exec="true" session="current-conduction"
print(block_table(Physics.CURRENT_CONDUCTION, SifSection.BOUNDARY_CONDITION))
```

### FixedPotential

```python exec="true" session="current-conduction"
print(condition_details(FixedPotential))
```

A phase needs two electrodes at different potentials. The run integrates the current through each electrode: `Run.electrode_currents` reads it per electrode and `Run.phase_currents` per phase. See [Results](../results.md).
