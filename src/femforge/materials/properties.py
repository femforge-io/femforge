"""The material property model: four property groups whose fields map onto Elmer material keywords."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Mapping, Optional, cast

from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo
from rich.tree import Tree

from femforge.common.utils import COLORS, title
from femforge.elmer.variables import ElmerVariable
from femforge.physical.expressions import Backend, Const
from femforge.physical.property_functions import Calibration, ClosedForm, Expression, PropertyFunction, PropertyFunctionField
from femforge.physical.units import (
    DIM_DENSITY,
    DIM_DIMENSIONLESS,
    DIM_ELECTRICAL_CONDUCTIVITY,
    DIM_FIELD_STRENGTH,
    DIM_FLUX_DENSITY,
    DIM_HEAT_TRANSFER_COEFFICIENT,
    DIM_PERMEABILITY,
    DIM_PERMITTIVITY,
    DIM_PRESSURE,
    DIM_RESISTIVITY,
    DIM_SPECIFIC_HEAT,
    DIM_TEMPERATURE,
    DIM_THERMAL_CONDUCTIVITY,
    Quantity,
    U,
    Unit,
)


PERMEABILITY_OF_VACUUM: float = 1.25663706212e-6
"""`μ₀` `[H/m]`: Magnetic permeability of free space."""

PERMITTIVITY_OF_VACUUM: float = 8.8541878128e-12
"""`ε₀` `[F/m]`: Electric permittivity of free space."""

# Property-field types: a PropertyFunction of a declared dimensionality (bare quantities coerce to Static).
# Direct ``Annotated[...]`` expressions so each name is a real type alias usable in field position.
_DensityPF = Annotated[PropertyFunction, PropertyFunctionField(DIM_DENSITY)]
_PressurePF = Annotated[PropertyFunction, PropertyFunctionField(DIM_PRESSURE)]
_ThermalConductivityPF = Annotated[PropertyFunction, PropertyFunctionField(DIM_THERMAL_CONDUCTIVITY)]
_SpecificHeatPF = Annotated[PropertyFunction, PropertyFunctionField(DIM_SPECIFIC_HEAT)]
_HeatTransferCoefficientPF = Annotated[PropertyFunction, PropertyFunctionField(DIM_HEAT_TRANSFER_COEFFICIENT)]
_TemperaturePF = Annotated[PropertyFunction, PropertyFunctionField(DIM_TEMPERATURE)]
_DimensionlessPF = Annotated[PropertyFunction, PropertyFunctionField(DIM_DIMENSIONLESS)]
_FluxDensityPF = Annotated[PropertyFunction, PropertyFunctionField(DIM_FLUX_DENSITY)]
_FieldStrengthPF = Annotated[PropertyFunction, PropertyFunctionField(DIM_FIELD_STRENGTH)]
_ElectricalConductivityPF = Annotated[PropertyFunction, PropertyFunctionField(DIM_ELECTRICAL_CONDUCTIVITY)]
_ResistivityPF = Annotated[PropertyFunction, PropertyFunctionField(DIM_RESISTIVITY)]
_PermittivityPF = Annotated[PropertyFunction, PropertyFunctionField(DIM_PERMITTIVITY)]
_PermeabilityPF = Annotated[PropertyFunction, PropertyFunctionField(DIM_PERMEABILITY)]


def _si(prop: Optional[PropertyFunction], unit: Any, at: Mapping[str, Quantity]) -> Optional[float]:  # pyright: ignore[reportExplicitAny, reportAny]  # unit is any pint unit expression (registry is under-typed)
    """Evaluate a property function at operating point ``at`` and strip to a bare SI float in ``unit``; None passes through."""
    if prop is None:
        return None
    return float(prop(**at).to(unit).magnitude)  # pyright: ignore[reportAny]  # unit arg is Any (registry under-typed)


def _unit_factor(from_units: str, to_unit: Any) -> float:  # pyright: ignore[reportExplicitAny, reportAny]  # to_unit is any pint unit expression (registry under-typed)
    """The multiplicative factor converting a magnitude in ``from_units`` to one in ``to_unit`` (both must be multiplicative units)."""
    one: Quantity = cast("Quantity", U.Quantity(1.0, from_units))
    return float(one.to(to_unit).magnitude)  # pyright: ignore[reportAny]  # pint arithmetic under-typed


def _runtime_value(fn: PropertyFunction, spec: "ElmerProperty", backend: Backend) -> str:
    """
    The variable-dependent sif value: ``Expression`` renders to MATC/Lua (unit conversion folded in as a constant factor), 1-D ``Calibration`` becomes an interpolation table.

    ``ClosedForm`` (opaque Python) and post-scaled properties refuse runtime emission — an arbitrary callable cannot be folded into a sif expression.
    """
    if spec.scale is not None:
        raise ValueError(f"property for {spec.key!r} has a post-scale; runtime (variable-dependent) emission cannot fold an arbitrary scale callable.")

    params: list[str] = list(fn.parameters)
    variables: str = ", ".join(_field_variable(p) for p in params)

    if isinstance(fn, Expression):
        factor: float = _unit_factor(fn.result_units, spec.unit)
        body = fn.body if factor == 1.0 else Const(value=factor) * fn.body
        refs: dict[str, str] = {p: (f"tx({i})" if backend == "matc" else f"tx[{i}]") for i, p in enumerate(params)}
        source: str = body.render(refs)
        keyword: str = "MATC" if backend == "matc" else "LUA"
        return f'Variable {variables}\n    Real {keyword} "{source}"'

    if isinstance(fn, Calibration):
        ((name, _dim),) = fn.param_dims.items()  # Calibration is 1-D by construction
        rows: list[tuple[float, float]] = sorted((float(p.inputs[name].to_base_units().magnitude), float(p.output.to(spec.unit).magnitude)) for p in fn.points)
        table: str = "\n".join(f"      {x} {y}" for x, y in rows)
        return f"Variable {variables}\n    Real\n{table}\n    End"

    if isinstance(fn, ClosedForm):
        raise ValueError(f"property for {spec.key!r} is a ClosedForm (opaque Python callable); use an Expression to emit a runtime {spec.key!r}.")

    raise ValueError(f"property for {spec.key!r} ({type(fn).__name__}) does not support runtime emission.")


class ElmerFieldVariable(StrEnum):
    """The Elmer solver variables a runtime material property may reference, keyed by the property-function parameter name."""

    TEMPERATURE = ElmerVariable.TEMPERATURE.value


def _field_variable(param: str) -> ElmerFieldVariable:
    """The Elmer variable a property parameter binds to at runtime; unknown parameters fail loudly."""
    try:
        return ElmerFieldVariable[param.upper()]
    except KeyError:
        raise ValueError(f"property parameter {param!r} has no Elmer field variable; known: {[m.name.lower() for m in ElmerFieldVariable]}") from None


class ElmerMaterialKeyword(StrEnum):
    """The Elmer material-section keywords this project emits, shared by the [`ElmerProperty`][] emit site and each solver's ``required_material_keys``."""

    DENSITY = "Density"
    YOUNGS_MODULUS = "Youngs Modulus"
    HEAT_CONDUCTIVITY = "Heat Conductivity"
    HEAT_CAPACITY = "Heat Capacity"
    RELATIVE_PERMEABILITY = "Relative Permeability"
    ELECTRIC_CONDUCTIVITY = "Electric Conductivity"
    RELATIVE_PERMITTIVITY = "Relative Permittivity"
    H_B_CURVE = "H-B Curve"


# A requirement for a key is satisfied by any of its equivalents (a nonlinear material presents an H-B curve instead of a permeability).
MATERIAL_KEY_EQUIVALENTS: dict[ElmerMaterialKeyword, frozenset[ElmerMaterialKeyword]] = {
    ElmerMaterialKeyword.RELATIVE_PERMEABILITY: frozenset({ElmerMaterialKeyword.H_B_CURVE}),
}


def _hb_curve_value(fn: PropertyFunction) -> str:
    """Render a 1-D H(B) Calibration as Elmer's ``H-B Curve`` table; the ``Variable "dummy"`` binding is Elmer's convention (the solver evaluates the table at local |B| internally)."""
    if not isinstance(fn, Calibration) or len(fn.param_dims) != 1:
        raise ValueError(f"H-B Curve requires a 1-parameter tabulated Calibration; got {type(fn).__name__} (only a tabulated Calibration can become an Elmer table).")
    if fn.method == "cubic":
        real = "Real Monotone Cubic"
    elif fn.method == "linear":
        real = "Real"
    else:
        raise ValueError(f"H-B Curve interpolation must be 'cubic' or 'linear'; got {fn.method!r}.")
    ((name, _dim),) = fn.param_dims.items()
    rows: list[tuple[float, float]] = sorted((float(p.inputs[name].to(U.T).magnitude), float(p.output.to(U.A / U.m).magnitude)) for p in fn.points)
    table: str = "\n".join(f"      {b} {h}" for b, h in rows)
    return f'Variable "dummy"\n    {real}\n{table}\n    End'


@dataclass(frozen=True)
class ElmerProperty:
    """``Annotated`` metadata tying one material-property field to an Elmer material keyword, its SI emission unit, and an optional post-scale and sif comment."""

    key: ElmerMaterialKeyword
    unit: Unit
    scale: Callable[[float], float] | None = None
    comment: str | None = None


def _elmer_property(field: FieldInfo) -> ElmerProperty | None:
    """The ``ElmerProperty`` metadata on a field, or None (so ``to_elmer`` skips it)."""
    for meta in field.metadata:  # pyright: ignore[reportAny]  # FieldInfo.metadata is list[Any]
        if isinstance(meta, ElmerProperty):
            return meta
    return None


class _PropertyModel(BaseModel):
    """Base for per-domain property models: pint quantities validated on assignment; the shared [`to_elmer`][] reflects over each field's [`ElmerProperty`][] metadata."""

    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True, use_attribute_docstrings=True)

    def to_elmer(self, *, at: Mapping[str, Quantity], runtime: frozenset[str] = frozenset(), backend: Backend = "matc") -> dict[str, float | str]:
        """The sif keyword/value pairs of this group's Elmer-mapped fields.

        Args:
            at: The operating point the other properties are evaluated at, each stripped to a bare SI float.
            runtime: The parameters Elmer supplies at solve time; a property of only these emits as a MATC/Lua expression or table.
            backend: The sif expression language for runtime properties.
        """
        out: dict[str, float | str] = {}
        for name, field in type(self).model_fields.items():
            spec: ElmerProperty | None = _elmer_property(field)
            if spec is None:
                continue

            fn: PropertyFunction | None = getattr(self, name)  # pyright: ignore[reportAny]  # getattr is Any
            if fn is None:
                continue

            live: set[str] = set(fn.parameters) & runtime
            if live:
                if live != set(fn.parameters):
                    raise ValueError(
                        f"property for {spec.key!r} mixes runtime parameters {sorted(live)} with operating-point parameters "
                        f"{sorted(set(fn.parameters) - live)}; partial runtime binding is not supported."
                    )
                out[spec.key] = _runtime_value(fn, spec, backend)
                continue

            si_value: float | None = _si(fn, spec.unit, at)
            assert si_value is not None  # fn was checked non-None above; _si returns None only for a None property
            value: float = si_value

            v: float = spec.scale(value) if spec.scale is not None else value
            comment: str = spec.comment if spec.comment is not None else f"{spec.unit:.5}"
            out[spec.key] = f"{v}  ! {comment}"
        return out


class MechanicalProperties(_PropertyModel):
    """The mechanical properties of a material."""

    density: Annotated[Optional[_DensityPF], ElmerProperty(ElmerMaterialKeyword.DENSITY, U.kg / U.m**3)] = None
    """`ρ` `[kg/m³]`: Mass density."""

    elastic_modulus: Annotated[Optional[_PressurePF], ElmerProperty(ElmerMaterialKeyword.YOUNGS_MODULUS, U.Pa)] = None
    """`E` `[Pa]`: Elastic (Young's) modulus."""

    yield_strength: Optional[_PressurePF] = None
    """`σ_y` `[Pa]`: Yield strength."""

    ultimate_tensile_strength: Optional[_PressurePF] = None
    """`σ_u` `[Pa]`: Ultimate tensile strength."""


class ThermalProperties(_PropertyModel):
    """The thermal properties of a material."""

    conductivity: Annotated[Optional[_ThermalConductivityPF], ElmerProperty(ElmerMaterialKeyword.HEAT_CONDUCTIVITY, U.W / (U.m * U.K))] = None
    """`k` `[W/(m·K)]`: Thermal conductivity."""

    specific_heat_capacity: Annotated[Optional[_SpecificHeatPF], ElmerProperty(ElmerMaterialKeyword.HEAT_CAPACITY, U.J / (U.kg * U.K))] = None
    """`c_p` `[J/(kg·K)]`: Specific heat capacity."""

    convective_heat_transfer_coefficient: Optional[_HeatTransferCoefficientPF] = None
    """`h` `[W/(m²·K)]`: Convective heat transfer coefficient."""

    glass_transition_temperature: Optional[_TemperaturePF] = None
    """`T_g` `[K]`: Glass transition temperature."""


class MagneticProperties(_PropertyModel):
    """The magnetic properties of a material."""

    rel_permeability: Annotated[Optional[_DimensionlessPF], ElmerProperty(ElmerMaterialKeyword.RELATIVE_PERMEABILITY, U.dimensionless, comment="-")] = None
    """`μ_r` `[-]`: Relative permeability."""

    remanence: Optional[_FluxDensityPF] = None
    """`B_r` `[T]`: Remanent flux density."""

    coercivity: Optional[_FieldStrengthPF] = None
    """`H_c` `[A/m]`: Coercivity."""

    bh_curve: Optional[_FieldStrengthPF] = None
    """`H(B)` `[A/m]`: Magnetization curve, a 1-D Calibration over ``flux_density`` emitted as Elmer's ``H-B Curve`` table."""

    def to_elmer(self, *, at: Mapping[str, Quantity], runtime: frozenset[str] = frozenset(), backend: Backend = "matc") -> dict[str, float | str]:
        """The sif keyword/value pairs of the magnetic fields, plus the ``H-B Curve`` table when ``bh_curve`` is set.

        Args:
            at: The operating point the other properties are evaluated at, each stripped to a bare SI float.
            runtime: The parameters Elmer supplies at solve time; a property of only these emits as a MATC/Lua expression or table.
            backend: The sif expression language for runtime properties.
        """
        out: dict[str, float | str] = super().to_elmer(at=at, runtime=runtime, backend=backend)
        if self.bh_curve is not None:
            out[ElmerMaterialKeyword.H_B_CURVE] = _hb_curve_value(self.bh_curve)
        return out

    def magnetization_magnitude(self, *, at: Mapping[str, Quantity]) -> Optional[float]:
        """|M| = Bᵣ/μ₀ in A/m, evaluating the remanence at operating point ``at``; None for a non-magnet (no remanence)."""
        br: Optional[float] = _si(self.remanence, U.T, at)
        if br is None:
            return None
        return br / PERMEABILITY_OF_VACUUM


def _relative_permittivity(eps: float) -> float:
    """Absolute permittivity (F/m) -> relative permittivity (εᵣ = ε/ε₀), the dimensionless value Elmer's ``Relative Permittivity`` keyword expects."""
    return eps / PERMITTIVITY_OF_VACUUM


class ElectricalProperties(_PropertyModel):
    """The electrical properties of a material."""

    conductivity: Annotated[Optional[_ElectricalConductivityPF], ElmerProperty(ElmerMaterialKeyword.ELECTRIC_CONDUCTIVITY, U.S / U.m)] = None
    """`σ` `[S/m]`: Electrical conductivity."""

    resistivity: Optional[_ResistivityPF] = None
    """`ρ_e` `[Ω·m]`: Electrical resistivity."""

    permittivity: Annotated[Optional[_PermittivityPF], ElmerProperty(ElmerMaterialKeyword.RELATIVE_PERMITTIVITY, U.F / U.m, scale=_relative_permittivity)] = None
    """`ε` `[F/m]`: Absolute permittivity, emitted to Elmer as the relative ε/ε₀."""

    permeability: Optional[_PermeabilityPF] = None
    """`μ` `[H/m]`: Absolute magnetic permeability."""


CONDUCTOR_CONDUCTIVITY_FLOOR: float = 1.0
"""`σ_min` `[S/m]`: The conductivity at or above which a material counts as a conductor, decades above the leakage of FR4 and air and decades below any metal."""


class MaterialProperties(_PropertyModel):
    """A named material: its registry tag and its mechanical, thermal, magnetic and electrical properties."""

    name: str = ""
    """The display name."""

    tag: str = ""
    """The registry key that a body's ``material`` tag names."""

    mechanical: MechanicalProperties = Field(default_factory=MechanicalProperties)
    """The mechanical properties."""

    thermal: ThermalProperties = Field(default_factory=ThermalProperties)
    """The thermal properties."""

    magnetic: MagneticProperties = Field(default_factory=MagneticProperties)
    """The magnetic properties."""

    electrical: ElectricalProperties = Field(default_factory=ElectricalProperties)
    """The electrical properties."""

    def to_elmer(self, *, at: Mapping[str, Quantity], runtime: frozenset[str] = frozenset(), backend: Backend = "matc") -> dict[str, float | str]:
        """The sif keyword/value pairs of all four property groups.

        Args:
            at: The operating point the other properties are evaluated at, each stripped to a bare SI float.
            runtime: The parameters Elmer supplies at solve time; a property of only these emits as a MATC/Lua expression or table.
            backend: The sif expression language for runtime properties.
        """
        data: dict[str, float | str] = {}
        data.update(self.mechanical.to_elmer(at=at, runtime=runtime, backend=backend))
        data.update(self.thermal.to_elmer(at=at, runtime=runtime, backend=backend))
        data.update(self.magnetic.to_elmer(at=at, runtime=runtime, backend=backend))
        data.update(self.electrical.to_elmer(at=at, runtime=runtime, backend=backend))
        return data

    @property
    def is_magnet(self) -> bool:
        """Whether the material has a remanence, which marks it as a permanent magnet."""
        return self.magnetic.remanence is not None

    def is_conductor(self, at: Mapping[str, Quantity]) -> bool:
        """Whether this material conducts current for solver purposes: σ(at) ≥ ``CONDUCTOR_CONDUCTIVITY_FLOOR``."""
        sigma = self.electrical.conductivity
        if sigma is None:
            return False
        return float(sigma(**at).to(U.S / U.m).magnitude) >= CONDUCTOR_CONDUCTIVITY_FLOOR

    def print_tree(self, tree: Tree | None = None) -> Tree:
        """A rich tree of this material's properties, added under ``tree`` when one is given."""
        if tree is None:
            tree = Tree(COLORS.H1(self.name))
        else:
            tree = tree.add(COLORS.H1(self.name))
        for key in ("name", "tag", "mechanical", "thermal", "magnetic", "electrical"):
            value = getattr(self, key)  # pyright: ignore[reportAny]  # reflection over model fields
            if isinstance(value, (MechanicalProperties, ThermalProperties, MagneticProperties, ElectricalProperties)):
                subtree = tree.add(COLORS.H2(title(key)))
                for subkey, subvalue in value.__dict__.items():  # pyright: ignore[reportAny]  # __dict__ values are Any
                    prop = COLORS.Prop(title(subkey), f"{subvalue}")
                    subtree.add(prop)
            else:
                tree.add(COLORS.Prop(title(key), f"{value}"))
        return tree

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MaterialProperties):
            return NotImplemented
        return self.name == other.name and self.tag == other.tag

    def __hash__(self) -> int:
        return hash((self.name, self.tag))
