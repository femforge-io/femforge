# Magnetostatics

`MAGNETOSTATICS` solves the static magnetic field of magnets, steel and coil drives. A solid frame solves it in 3D, and an axisymmetric frame solves it as `MAGNETOSTATICS_2D` on the r-z half-plane. See [Conditions and physics](index.md) for how conditions attach and where they land.

```python exec="true" session="magnetostatics"
from femforge.elmer import Physics, SifSection
from femforge.physical import CalculateMagneticForce, CurrentDensity, Magnetization
from femforge_docs.conditions import block_table, condition_details, solves_table

print(solves_table(Physics.MAGNETOSTATICS))
```

## Material-wired conditions

```python exec="true" session="magnetostatics"
print(block_table(Physics.MAGNETOSTATICS, SifSection.MATERIAL))
```

### Magnetization

```python exec="true" session="magnetostatics"
print(condition_details(Magnetization))
```

The material's remanence sets the strength, so a magnet body needs both a magnet material, such as `N52`, and a `Magnetization` with a direction.

## Body forces

```python exec="true" session="magnetostatics"
print(block_table(Physics.MAGNETOSTATICS, SifSection.BODY_FORCE))
```

### CurrentDensity

```python exec="true" session="magnetostatics"
print(condition_details(CurrentDensity))
```

`CurrentDensity` drives only the axisymmetric solve; a solid frame rejects it. See [Axisymmetric](../frames/axisymmetric.md).

## Components

```python exec="true" session="magnetostatics"
print(block_table(Physics.MAGNETOSTATICS, SifSection.COMPONENT))
```

### CalculateMagneticForce

```python exec="true" session="magnetostatics"
print(condition_details(CalculateMagneticForce))
```

The result reads the lumped force and torque back by the Component's name, as `result.component_forces[label]` and `result.component_torques[label]`. An axisymmetric frame lumps no torque, so it rejects the torque fields.
