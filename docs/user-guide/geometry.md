# Geometry and tags

A femforge model is a STEP file whose bodies carry tags in their names: `key=value` pairs separated by commas, such as `Material=N52, Name=MAGNET`. The mesher reads the tags back from the STEP, and every later stage selects bodies by them. One vocabulary connects the CAD to the materials, conditions and mesh sizes, so you never number a volume or a surface by hand.

The STEP file comes from one of two routes:

- Any CAD that writes body names into the STEP, such as Fusion 360, SolidWorks or FreeCAD. Name each body with its tags, and make sure the names reach the STEP file.
- A build123d script that uses the femforge tagged containers: [TaggedPart][femforge.geometry.tags.TaggedPart], [TaggedCompound][femforge.geometry.tags.TaggedCompound] and [Build123dGeometry][femforge.geometry.Build123dGeometry]. They write the names for you.

The next two sections cover the build123d route. The tag keys, selectors and names apply to both.

## Build a tagged model

```python
import build123d as b3d

import femforge as ff


class MagnetInAir(ff.Build123dGeometry):
    """A cube magnet at the centre of an air box."""

    magnet_size: float = 10.0
    """Edge length of the magnet, in millimetres."""

    air_size: float = 40.0
    """Edge length of the air box, in millimetres."""

    def model(self) -> ff.TaggedCompound:
        air = ff.TaggedPart(
            b3d.Box(self.air_size, self.air_size, self.air_size),
            tags={ff.TagKey.MATERIAL: ff.materials.Air().tag},
            name="Air",
        )
        magnet = ff.TaggedPart(
            b3d.Box(self.magnet_size, self.magnet_size, self.magnet_size),
            tags={ff.TagKey.MATERIAL: ff.materials.N52().tag, ff.TagKey.MAGNETIZATION: "N"},
            name="Magnet",
        )

        return ff.TaggedCompound(children=[air, magnet])


config = ff.SimulationConfig(
    geometry=MagnetInAir(magnet_size=8.0),
    mesh=ff.MeshConfig(global_mesh_size=4 * ff.U.mm),
    domain=ff.DomainConfig(
        materials=[ff.materials.Air(), ff.materials.N52()],
        tags=[
            ff.EntityTag(
                selector={ff.TagKey.MAGNETIZATION: "N"},
                conditions=[ff.Magnetization(direction=ff.Direction(0, 0, 1))],
            ),
        ],
    ),
)
```

The two boxes overlap. The mesher splits them into a conformal decomposition, and the overlap goes to the magnet, the smaller body. See [Mesh](mesh.md#conformal-meshes).

## From model to STEP

[Build123dGeometry][femforge.geometry.Build123dGeometry] is a Pydantic model. Its fields are the geometry parameters, and `model()` returns the tagged assembly. When `SimulationConfig.geometry` is set, the driver calls `build_step` into the run directory before meshing and points `mesh.step` at the result. The STEP is derived output, and the run identity comes from the parameters, so a new `magnet_size` gives a new run.

`build_step` exports `model()` with every leaf flattened to world placement. A part inherits the tags of the compounds above it, and a deeper tag wins over an ancestor's tag with the same key.

Without `geometry`, the mesher imports the file at [`MeshConfig.step`][femforge.meshing.MeshConfig.step]. An authored STEP, such as a CAD export, works as long as its solid names carry `key=value` pairs, and the run identity then follows the file's content digest.

gmsh converts the STEP from its own unit to metres on import (`GmshOptions.target_units="M"`), and build123d writes millimetres. Author the CAD in millimetres. A mesh size carries its own unit, as in `0.5 * ff.U.mm`. The mesher warns when the meshed model spans more than 2 m, which usually means a unit slip.

The model stays in the frame you author it in. `SimulationConfig.frame` maps it onto the solve, for example to an axisymmetric half-plane. See [Frames](frames.md).

## Tagged shapes

| Class | Wraps | Use it for |
| --- | --- | --- |
| [TaggedPart][femforge.geometry.tags.TaggedPart] | `build123d.Part` | A body of a 3D model: one material, one region. |
| [TaggedCompound][femforge.geometry.tags.TaggedCompound] | `build123d.Compound` | A group of tagged parts. Its tags pass down to every tagged leaf on export. |
| [TaggedFace][femforge.geometry.tags.TaggedFace] | `build123d.Face` | A body of a planar 2D model. |

Each takes build123d shapes and the keyword `tags=`: `TaggedPart(shapes)` the solids it holds, such as `[box]` or a build123d part, `TaggedCompound(children=...)` the tagged shapes below it, and `TaggedFace(face)` one build123d face. `TaggedPart` and `TaggedFace` also take `name=`. The tags set the label, which the STEP file carries as the part name.

## Tag keys

A tag key is any name that starts with a letter and continues with letters, digits or underscores. [TagKey][femforge.common.TagKey] holds the shared keys:

| Member | Key | Meaning |
| --- | --- | --- |
| `MATERIAL` | `Material` | The registry tag of the body's material. Every body needs one. |
| `NAME` | `Name` | The authored body name. Set it through `name=`. |
| `MAGNETIZATION` | `Magnetization` | A magnet's magnetization label, such as `N` or `S`. |
| `PHASE` | `Phase` | The winding phase a conductor belongs to. |
| `CURRENT_DIR` | `CurrentDir` | Whether an electrode is a current `Entry` or `Exit`. |
| `SEGMENT` | `Segment` | The winding segment a conductor belongs to. |
| `LAYER` | `Layer` | The layer a conductor lies on, such as a PCB copper layer. |
| `TYPE` | `Type` | The kind of part, such as `Trace` or `Via`. |
| `SLOT` | `Slot` | The pole or slot position a part occupies. |

Tag values cannot contain `/`, `,`, `_` or whitespace, because the STEP name carries them as `key=value` pairs. femforge removes those characters and logs a warning, so `name="Left Magnet"` becomes `LeftMagnet`. A value that is empty after cleaning raises.

## Select bodies with tags

The mesher gives each body the material whose `tag` equals its `Material` value, ignoring case. A body without a `Material` tag, or with a tag that no material in `DomainConfig.materials` carries, stops the mesh with an error.

An [EntityTag][femforge.physical.EntityTag] selects bodies by a `selector`. A body matches when its tags contain every pair of the selector, and an empty selector matches every body. The tag then carries what applies to the matched bodies:

| Field | What it applies |
| --- | --- |
| `selector` | The `key=value` pairs a body must carry. |
| `conditions` | Body forces and boundary conditions. See [Conditions and physics](conditions.md). |
| `mesh_size` | A target element size, as a length such as `0.5 * ff.U.mm`. See [Mesh](mesh.md#size-regions-by-tag). |

A body can match several tags. It collects the conditions of all of them, and the finest `mesh_size` wins. Select by a shared tag such as `Magnetization=N` to reach many bodies at once, or by `Name` to reach one.

You never tag faces. A boundary condition on a body's tag lands on the surfaces that body borders. See [Conditions and physics](conditions.md#where-conditions-land).

## Body and boundary names

The mesher writes one physical group per body, and Elmer, `entities.json` and the viewer all use the same name.

| Body | Group name | Rule |
| --- | --- | --- |
| `name="Magnet"` | `MAGNET` | The authored name, uppercased. |
| No name, `Material=N52`, matched by `Magnetization=N` | `N52_MAGNETIZATIONN` | The material tag, then each matched tag's name, joined by `_`. |
| No name, `Material=Air`, no matched tags | `AIR` | The material tag alone. |

A matched tag's name is its sorted pairs written as `KeyValue` and joined by `_`, such as `CurrentDirEntry_Phase2`. A tag whose selector is only `Name` uses the uppercased name. Bodies that share a material, an authored name and the same matched tags merge into one group.

Boundary groups take the names of the bodies they border:

| Boundary | Name | Example |
| --- | --- | --- |
| Interface between two bodies | `<A>__<B>`, sorted | `AIR__MAGNET` |
| A body's outer shell, shared with no other body | `<BODY>__EXT` | `AIR__EXT` |
| A body's edge on the symmetry axis of a 2D axisymmetric model | `<BODY>__AXIS` | `AIR__AXIS` |

Keep the names unique and short:

- Two body groups with the same name raise an error, for example one `Name` on parts of two materials.
- Elmer reads names into 128-character buffers, and an interface name joins two body names. The mesher warns when a body name passes 60 characters.

If a body arrives at the mesher without a name, give the build123d solid a `color`. The STEP export can drop the name of an uncoloured solid.

## Reference

- [Geometry API](../reference/geometry.md): `Build123dGeometry`, the tagged shapes, cross-sections.
- [Physical API](../reference/physical.md): `EntityTag` and `DomainConfig`.
- [Common API](../reference/common.md): `TagKey` and the tag helpers.
