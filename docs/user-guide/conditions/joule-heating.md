# Joule heating

`JOULE_HEATING` couples current conduction to heat conduction: it solves the steady temperature the Joule losses of the solved current produce. It reads the conditions of [Current conduction](current-conduction.md) and [Thermal](thermal.md) as well as its own, so the same tags drive the standalone and the coupled solves. See [Conditions and physics](index.md) for how conditions attach and where they land.

```python exec="true" session="joule-heating"
from femforge.elmer import Physics, SifSection
from femforge.physical import JouleHeat
from femforge_docs.conditions import block_table, condition_details, solves_table

print(solves_table(Physics.JOULE_HEATING))
```

## Boundary conditions

```python exec="true" session="joule-heating"
print(block_table(Physics.JOULE_HEATING, SifSection.BOUNDARY_CONDITION))
```

## Body forces

```python exec="true" session="joule-heating"
print(block_table(Physics.JOULE_HEATING, SifSection.BODY_FORCE))
```

### JouleHeat

```python exec="true" session="joule-heating"
print(condition_details(JouleHeat))
```

Put `JouleHeat` on the conductor bodies whose losses heat the model. A conductor without it carries current but stays a passive heat path.
