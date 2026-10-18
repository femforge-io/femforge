# Electrostatics

`ELECTROSTATICS` solves the electrostatic potential and field. See [Conditions and physics](index.md) for how conditions attach and where they land.

```python exec="true" session="electrostatics"
from femforge.elmer import Physics
from femforge_docs.conditions import solves_table

print(solves_table(Physics.ELECTROSTATICS))
```

No shipped condition defaults to `ELECTROSTATICS`. To fix a potential, set `physics=ff.Physics.ELECTROSTATICS` on a [FixedPotential](current-conduction.md#fixedpotential):

```python
import femforge as ff

electrode = ff.EntityTag(
    selector={ff.TagKey.NAME: "Electrode"},
    conditions=[ff.FixedPotential(physics=ff.Physics.ELECTROSTATICS, value=ff.quantity(100, "V"))],
)
```

No example or test solves this path yet; see [Status](../../overview/status.md).
