# Magnetostatics

`MAGNETOSTATICS` solves the static magnetic field of magnets, steel and coil drives. A solid frame solves it in 3D, and an axisymmetric or Cartesian 2D frame solves it as `MAGNETOSTATICS_2D` on the r-z half-plane or a planar section. See [Conditions and physics](index.md) for how conditions attach and where they land.

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

`CurrentDensity` drives only the 2D solve, azimuthal under an axisymmetric frame and along the normal under a Cartesian 2D one; a solid frame rejects it. See [Axisymmetric](../frames/axisymmetric.md) and [Cartesian 2D](../frames/cartesian-2d.md).

## Components

```python exec="true" session="magnetostatics"
print(block_table(Physics.MAGNETOSTATICS, SifSection.COMPONENT))
```

### CalculateMagneticForce

```python exec="true" session="magnetostatics"
print(condition_details(CalculateMagneticForce))
```

The result reads the lumped force and torque back by the Component's name, as `result.component_forces[label]` and `result.component_torques[label]`. An axisymmetric or Cartesian 2D frame lumps no torque, so it rejects the torque fields.
