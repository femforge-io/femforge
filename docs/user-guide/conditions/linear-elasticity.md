# Linear elasticity

`LINEAR_ELASTICITY` solves the small displacements, stresses and strains of a linear elastic solid, in 3D under a solid frame and on a planar section, in plane stress or plane strain, under a [Cartesian 2D](../frames/cartesian-2d.md) frame. See [Conditions and physics](index.md) for how conditions attach and where they land.

```python exec="true" session="linear-elasticity"
from femforge.elmer import Physics, SifSection
from femforge.physical import FixedDisplacement, SurfaceForce
from femforge_docs.conditions import block_table, condition_details, solves_table

print(solves_table(Physics.LINEAR_ELASTICITY))
```

`LINEAR_ELASTICITY` reads `Youngs Modulus` and `Poisson Ratio` from every body, so leave air bodies out of an elasticity model. The run writes the displacement, the stress and strain components, the von Mises stress and the principal stresses; `Run.strain_energy()` integrates the strain energy femforge derives from the stress and strain. See [Results](../results.md).

## Clamp one end and load the other

Tag the two end faces of a beam as [named faces](index.md#named-faces), clamp one and load the other:

```python
import build123d as b3d

import femforge as ff

box = b3d.Box(100, 10, 10)
beam = ff.TaggedPart([box], tags={ff.TagKey.MATERIAL: "Steel316"}, name="Beam")
x_faces = box.faces().filter_by(b3d.Axis.X).sort_by(b3d.Axis.X)
model = ff.TaggedCompound(children=[beam, ff.TaggedFace(x_faces[0], name="Root"), ff.TaggedFace(x_faces[-1], name="Tip")])

clamp = ff.EntityTag(selector={ff.TagKey.NAME: "Root"}, conditions=[ff.FixedDisplacement()])
load = ff.EntityTag(selector={ff.TagKey.NAME: "Tip"}, conditions=[ff.SurfaceForce(force=ff.quantity(ff.Vec3(0, 0, -100), "N"))])

domain = ff.DomainConfig(materials=[ff.materials.Steel_316()], tags=[clamp, load])
physics = ff.PhysicsSelection(selected=ff.Physics.LINEAR_ELASTICITY)
```

## Boundary conditions

```python exec="true" session="linear-elasticity"
print(block_table(Physics.LINEAR_ELASTICITY, SifSection.BOUNDARY_CONDITION))
```

### FixedDisplacement

```python exec="true" session="linear-elasticity"
print(condition_details(FixedDisplacement))
```

Set a component to None to leave it free, as in `FixedDisplacement(z=None)` for a roller. A frame rejects a nonzero component along a direction it has no solve coordinate for, and a Cartesian 2D frame drops a zero one along its normal.

### SurfaceForce

```python exec="true" session="linear-elasticity"
print(condition_details(SurfaceForce))
```

The traction is the force over the face's measured area, so `SurfaceForce` needs a named face; on a body's tag it raises.
