# Materials

A material is a [MaterialProperties][femforge.materials.MaterialProperties] model: a display name, a registry `tag`, and four groups of properties. Each property is a function of the operating point that returns a pint quantity, so a value carries its unit from the material definition to the sif file, and a temperature-dependent law stays one object. The mesher matches each body to a material by its `Material` tag, and the sif writer turns the material into an Elmer Material block.

## Assign materials by tag

```python
import femforge as ff
from femforge.physical import Expression, Param, Static


class Ferrite(ff.materials.MaterialProperties):
    """A soft ferrite whose thermal conductivity falls with temperature."""

    def __init__(self) -> None:
        super().__init__()
        self.name = "Soft Ferrite"
        self.tag = "Ferrite"
        self.magnetic.rel_permeability = Static(value=2000 * ff.U.dimensionless)
        self.electrical.conductivity = Static(value=0.1 * ff.U.S / ff.U.m)
        self.thermal.conductivity = Expression(
            param_dims={"temperature": "[temperature]"},
            result_units="W/(m*K)",
            body=4.0 * (1.0 - 0.002 * (Param(name="temperature") - 293.15)),
        )


ff.materials.register_material("Ferrite", Ferrite)

domain = ff.DomainConfig(
    materials=[
        ff.materials.Air(),
        ff.materials.N52(),
        ff.materials.Steel_1080(),
        ff.materials.material("Ferrite"),
    ],
)
```

`DomainConfig.materials` lists the candidates. A body tagged `Material=Ferrite` gets the material whose `tag` is `Ferrite`, ignoring case. A body whose tag matches no candidate stops the mesh with an error. See [Geometry and tags](geometry.md#select-bodies-with-tags).

## The catalogue

| Class | Tag | What it models |
| --- | --- | --- |
| [Air][femforge.materials.Air] | `Air` | Dry air at room temperature: μr = 1, near-zero conductivity. |
| [Copper][femforge.materials.Copper] | `Copper` | Annealed copper. Conductivity follows σ(T) = σ_ref / (1 + α·(T − 293.15 K)) with σ_ref = 5.96e7 S/m and α = 3.93e-3 1/K. |
| [FR4][femforge.materials.FR4] | `FR4` | The glass-epoxy laminate of printed circuit boards: εr = 4.5, glass transition at 130 °C. |
| [N52][femforge.materials.N52] | `N52` | Sintered NdFeB, B_r = 1.48 T and H_cb = 955 kA/m at 20 °C. |
| [N42][femforge.materials.N42] | `N42` | Sintered NdFeB, B_r = 1.30 T and H_cb = 915 kA/m at 20 °C. |
| [Neodymium][femforge.materials.Neodymium] | yours | The base of the NdFeB grades. Pass `name`, `tag`, `remanence_at_reference` and `coercivity_at_reference` to define another grade. |
| [Steel_1080][femforge.materials.Steel_1080] | `Steel1080` | Annealed high-carbon steel with a nonlinear B-H curve, or a linear μr when you pass `rel_permeability`. |
| [Steel_316][femforge.materials.Steel_316] | `Steel316` | Annealed austenitic stainless steel: μr = 1.008, effectively non-magnetic. |

The NdFeB grades scale remanence and coercivity linearly with temperature, with coefficients of −0.12 %/K and −0.60 %/K from 20 °C. A hot magnet drives a weaker field.

The registry holds every catalogue material by tag:

| Function | Returns |
| --- | --- |
| `material(tag)` | A fresh instance of the registered material. |
| `available_materials()` | The registered tags. |
| `all_materials()` | A fresh instance of every registered material. |
| `register_material(tag, factory)` | Nothing. It adds or replaces the material class under `tag`. |

Each call builds a new instance, so an edit to one config's material never leaks into another.

## Property groups

| Group | Field | Elmer keyword |
| --- | --- | --- |
| `mechanical` | `density` | `Density` |
| | `elastic_modulus` | `Youngs Modulus` |
| | `yield_strength`, `ultimate_tensile_strength` | none |
| `thermal` | `conductivity` | `Heat Conductivity` |
| | `specific_heat_capacity` | `Heat Capacity` |
| | `convective_heat_transfer_coefficient`, `glass_transition_temperature` | none |
| `magnetic` | `rel_permeability` | `Relative Permeability` |
| | `bh_curve` | `H-B Curve` |
| | `remanence`, `coercivity` | none |
| `electrical` | `conductivity` | `Electric Conductivity` |
| | `permittivity` | `Relative Permittivity`, written as ε/ε₀ |
| | `resistivity`, `permeability` | none |

Fields without a keyword stay in the material for your own use and never reach the sif. Two of them change how the solve treats a body:

- A `remanence` marks the material as a permanent magnet. Its magnitude sets |M| = B_r/μ₀, and a [Magnetization][femforge.physical.Magnetization] condition sets the direction.
- A `conductivity` of at least 1 S/m at the operating point marks the material as a conductor. Current-conduction solvers act only on conductors.

## Units

Every quantity comes from one pint registry, `ff.U`.

| Write | Gives |
| --- | --- |
| `5 * ff.U.mm` | 5 mm as a `Quantity` |
| `ff.quantity(1.2, "T")` | 1.2 T as a typed `Quantity` |
| `ff.quantity(300, "kelvin")` | 300 K |
| `q.to("A/m").magnitude` | The magnitude of `q` in A/m, as a float |

Prefer a string for composite units, such as `ff.quantity(2.0, "newton * meter")`. Each property and condition field declares a dimensionality and rejects a quantity of another one: a conductivity given in kelvin raises as soon as you set it, long before Elmer runs. The sif writer converts every value to SI.

## Property functions

A property is a [PropertyFunction][femforge.physical.PropertyFunction]: it declares its `parameters` (name to dimensionality) and returns a quantity when called with them.

| Kind | Parameters | Defined by | Serializes | Emits as a field-dependent sif value |
| --- | --- | --- | --- | --- |
| [Static][femforge.physical.Static] | none | A constant `value` | yes | no, a constant |
| [Calibration][femforge.physical.Calibration] | one | Sample `points`, interpolated `linear`, `nearest` or `cubic` (monotone) | yes | yes, as an interpolation table |
| [Expression][femforge.physical.Expression] | any | An expression tree built from `Param` and arithmetic | yes | yes, as a MATC expression |
| [ClosedForm][femforge.physical.ClosedForm] | any | A Python callable, quantities in and out | no | no |

A calibration outside its sample range raises a `PropertyRangeError` unless you set `extrapolate=True`, which clamps to the end samples. A missing parameter raises `PropertyParameterError`, and a parameter of the wrong dimensionality raises `PropertyDimensionError`.

An `Expression` body binds each parameter as its SI magnitude, so `Param(name="temperature")` is in kelvin, and the body returns a magnitude in `result_units`. Prefer it over `ClosedForm` whenever the law is arithmetic: it saves with the config and it can reach Elmer as a law.

## Field-dependent properties

The sif writer evaluates a property in one of two ways:

| Physics | A property of `temperature` becomes |
| --- | --- |
| `THERMAL`, `JOULE_HEATING` | A sif expression on Elmer's solved `Temperature`, so the property varies across the model. |
| Every other physics | One value at the operating point, by default 300 K. |

Under thermal physics, a property that depends on `temperature` and another parameter raises, and so does a `ClosedForm` of temperature, since Python code cannot become a sif expression.

The operating point belongs to the solver stack. To evaluate the magnets of a magnetostatic solve at 80 °C, set it on the customized stack:

```python
import femforge as ff

config = ff.SimulationConfig(mesh=ff.MeshConfig(step="motor.step"))
config.customize_physics().operating_point.temperature = ff.quantity(353.15, "kelvin")
```

See [Conditions and physics](conditions.md#customize-the-solver-stack) for the solver stack.

## Nonlinear steel

`Steel_1080()` carries its normal magnetization curve as `bh_curve`, a one-parameter `Calibration` of H over `flux_density` with monotone cubic interpolation. The sif receives it as an `H-B Curve` table with `Real Monotone Cubic`, and Elmer evaluates it at the local |B|. The magnetic solve becomes nonlinear, and the magnetostatic presets allow 50 nonlinear iterations.

`Steel_1080(rel_permeability=500.0)` drops the curve and freezes a linear μr. Use the linear mode for a quick solve, or when the steel stays well below its knee near 1.5 T. Use the curve when the steel saturates.

## How a material reaches Elmer

Each distinct material becomes one Elmer Material block that its bodies share. Under the magnetic physics, a magnet body gets its own block, because it carries its `Magnetization` vector. Each solver in the stack names the keywords a body must supply, and the sif writer checks them before writing:

| Physics | Required keywords |
| --- | --- |
| `MAGNETOSTATICS` | `Relative Permeability`, or an `H-B Curve` in its place |
| `THERMAL` | `Heat Conductivity` |
| `ELECTROSTATICS` | `Relative Permittivity` |
| `CURRENT_CONDUCTION` | `Electric Conductivity` on conductors |
| `JOULE_HEATING` | `Heat Conductivity`, plus `Electric Conductivity` on conductors |
| `COIL_MAGNETOSTATICS` | `Relative Permeability`, plus `Electric Conductivity` on conductors |
| `LINEAR_ELASTICITY` | none checked |

A missing keyword raises one error that names the body, the material and the keywords. Under an axisymmetric frame, `MAGNETOSTATICS` solves with the same requirement.

## Reference

- [Materials API](../reference/materials.md): the property model, the catalogue and the registry.
- [Physical API](../reference/physical.md): property functions, expressions and units.
