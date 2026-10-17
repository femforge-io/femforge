"""The shared pint unit registry, its typed quantity facades and the dimensionality-constrained quantity field types."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Protocol, cast, overload

import pint
from pint.delegates.formatter import Formatter
from pint.delegates.formatter._spec_helpers import split_format
from pydantic_core import core_schema

from femforge.common.utils import COLORS
from femforge.common.vector import Vec3


__all__ = [
    "U",
    "Unit",
    "quantity",
    "quantity_type",
    "vector_quantity_type",
    "dimensionality_matches",
    "DIM_LENGTH",
    "DIM_TEMPERATURE",
    "DIM_THERMAL_CONDUCTIVITY",
    "DIM_SPECIFIC_HEAT",
    "DIM_DENSITY",
    "DIM_FLUX_DENSITY",
    "DIM_FIELD_STRENGTH",
    "DIM_PRESSURE",
    "DIM_HEAT_FLUX",
    "DIM_HEAT_TRANSFER_COEFFICIENT",
    "DIM_ELECTRICAL_CONDUCTIVITY",
    "DIM_RESISTIVITY",
    "DIM_VOLTAGE",
    "DIM_CURRENT",
    "DIM_CURRENT_DENSITY",
    "DIM_POWER_DENSITY",
    "DIM_PERMITTIVITY",
    "DIM_PERMEABILITY",
    "DIM_FORCE",
    "DIM_TORQUE",
    "DIM_DIMENSIONLESS",
    "Length",
    "Temperature",
    "Conductivity",
    "ThermalConductivity",
    "SpecificHeat",
    "Density",
    "FluxDensity",
    "FieldStrength",
    "Pressure",
    "HeatFlux",
    "HeatTransferCoefficient",
    "ElectricalConductivity",
    "Resistivity",
    "Voltage",
    "Current",
    "CurrentDensity",
    "PowerDensity",
    "Permittivity",
    "Permeability",
    "Force",
    "Torque",
    "Dimensionless",
    "Quantity",
    "VectorQuantity",
]


class _SubFormatter(Protocol):
    """The slice of pint's per-spec formatter this class calls into (``get_formatter`` is loosely typed)."""

    def format_magnitude(self, magnitude: object, mspec: str = "", **babel_kwds: object) -> str: ...

    def format_unit(self, unit: object, uspec: str = "", sort_func: object = None, **babel_kwds: object) -> str: ...


class RichEngineeringFormatter(Formatter):
    """Pint formatter that wraps quantity parts in Rich markup."""

    default_format = ".5g~#PS"

    magnitude_style: str = COLORS.MAGNITUDE
    unit_style: str = COLORS.UNITS + " bold"
    slash_style: str = COLORS.UNITS + " "
    x_style: str = "#FC7B7B"
    y_style: str = "#7EF19B"
    z_style: str = "#58C2DA"

    @staticmethod
    def _tag(text: str, style: str) -> str:
        if not style or not text:
            return text
        return "[" + style + "]" + text + "[/" + style + "]"

    @staticmethod
    def _noop_tag(text: str, _style: str) -> str:
        return text

    def _format_vec3_magnitude(
        self,
        vec3: Vec3,
        mspec: str,
        sub_fmt: _SubFormatter,
        *,
        tag: Callable[[str, str], str] | None = None,
        **babel_kwds: object,
    ) -> str:
        _tag = tag if tag is not None else self._tag

        def fmt(val: float) -> str:
            return sub_fmt.format_magnitude(val, mspec, **babel_kwds)

        x = _tag(fmt(vec3.x), self.x_style)
        y = _tag(fmt(vec3.y), self.y_style)
        z = _tag(fmt(vec3.z), self.z_style)
        return f"<{x}, {y}, {z}>"

    def _style_unit(self, unit_str: str, *, tag: Callable[[str, str], str] | None = None) -> str:
        if not unit_str:
            return unit_str
        _tag = tag if tag is not None else self._tag
        slash = _tag("/", self.slash_style)
        segments = unit_str.split("/")
        return slash.join(_tag(seg, self.unit_style) for seg in segments)

    def format_quantity(self, quantity: Quantity, spec: str = "", **babel_kwds: object) -> str:
        """Format ``quantity`` with its parts wrapped in Rich markup when ``spec`` asks for styling.

        Args:
            quantity: The quantity to format, with a scalar or a Vec3 magnitude.
            spec: The pint format spec; ``#`` picks a compact unit prefix and ``S`` adds the Rich styles.
            babel_kwds: Locale options passed through to pint.
        """
        registry = self._registry
        assert registry is not None  # a registry is always bound while this formatter is installed

        spec = spec or self.default_format
        styled = "S" in spec
        if styled:
            spec = spec.replace("S", "")
        tag: Callable[[str, str], str] = self._tag if styled else self._noop_tag

        obj: Quantity = quantity
        if "#" in spec:
            spec = spec.replace("#", "")
            mag = quantity.magnitude
            if hasattr(mag, "x") and hasattr(mag, "y") and hasattr(mag, "z"):
                try:
                    scalar_proxy = cast(Quantity, registry.Quantity(abs(mag), quantity.units))
                    obj = quantity.to(scalar_proxy.to_compact().units)
                except Exception:
                    obj = quantity
            else:
                try:
                    obj = quantity.to_compact()
                except Exception:
                    obj = quantity

        sub_fmt = cast(_SubFormatter, self.get_formatter(spec))
        mspec, uspec = split_format(spec, self.default_format, bool(registry.separate_format_defaults))

        mag = obj.magnitude
        if hasattr(mag, "x") and hasattr(mag, "y") and hasattr(mag, "z"):
            magnitude_str = self._format_vec3_magnitude(cast(Vec3, mag), mspec, sub_fmt, tag=tag, **babel_kwds)
        else:
            magnitude_str = tag(
                sub_fmt.format_magnitude(mag, mspec, **babel_kwds),
                self.magnitude_style,
            )

        raw_unit = sub_fmt.format_unit(obj.unit_items(), uspec, sort_func=self.default_sort_func, **babel_kwds)
        unit_str = self._style_unit(raw_unit, tag=tag)

        return f"{magnitude_str} {unit_str}".strip()


# --- typed pint facades: pint leaks Unknown (unbound registry generic, unannotated dunders), so these
# type-only subclasses re-declare the leaky members. Params stay ``object`` — spelling pint's QuantityOrUnitLike drags Unknown back in.
if TYPE_CHECKING:
    from pint.facets.plain import PlainQuantity, PlainUnit

    class Unit(PlainUnit):
        """A pint unit, typed so that arithmetic with units and numbers keeps its type."""

        @overload
        def __mul__(self, other: Unit) -> Unit: ...
        @overload
        def __mul__(self, other: object) -> Quantity: ...
        def __mul__(self, other: object) -> Unit | Quantity: ...
        def __rmul__(self, other: float) -> Quantity: ...
        @overload
        def __truediv__(self, other: Unit) -> Unit: ...
        @overload
        def __truediv__(self, other: object) -> Quantity: ...
        def __truediv__(self, other: object) -> Unit | Quantity: ...
        def __rtruediv__(self, other: float) -> Quantity: ...
        def __pow__(self, other: float) -> Unit: ...

    class Quantity(PlainQuantity[float]):
        """A pint quantity with a float magnitude."""

        @property
        def magnitude(self) -> float: ...
        def to(self, other: object = None, *contexts: object, **ctx_kwargs: object) -> Quantity:
            """This quantity converted to the units ``other`` names.

            Args:
                other: The target units, as a unit, a quantity or a unit string.
                contexts: The pint contexts, by name or object, that enable the conversion.
                ctx_kwargs: The parameters those contexts take.
            """
            ...

        def to_compact(self, unit: object = None) -> Quantity: ...
        def m_as(self, units: object) -> float: ...
        def check(self, dimension: object) -> bool: ...
        def __mul__(self, other: object) -> Quantity: ...
        def __rmul__(self, other: object) -> Quantity: ...
        def __truediv__(self, other: object) -> Quantity: ...
        def __rtruediv__(self, other: object) -> Quantity: ...
        def __add__(self, other: object) -> Quantity: ...
        def __sub__(self, other: object) -> Quantity: ...
        def __pow__(self, other: object) -> Quantity: ...
        def __neg__(self) -> Quantity: ...
        def __abs__(self) -> Quantity: ...

    class VectorQuantity(PlainQuantity[Vec3]):  # pyright: ignore[reportInvalidTypeArguments]
        """A pint quantity with a [`Vec3`][femforge.common.vector.Vec3] magnitude."""

        @property
        def magnitude(self) -> Vec3: ...
        def to(self, other: object = None, *contexts: object, **ctx_kwargs: object) -> VectorQuantity:
            """This vector quantity converted to the units ``other`` names.

            Args:
                other: The target units, as a unit, a quantity or a unit string.
                contexts: The pint contexts, by name or object, that enable the conversion.
                ctx_kwargs: The parameters those contexts take.
            """
            ...

        def to_compact(self, unit: object = None) -> VectorQuantity: ...
        def m_as(self, units: object) -> Vec3: ...
        def check(self, dimension: object) -> bool: ...

    class _TypedRegistry(pint.UnitRegistry[float]):
        # Override so dynamically resolved units/constants (``U.inch``, ``U.tesla`` …) come back
        # as the typed ``Unit`` rather than the bare ``UnitT``.
        def __getattr__(self, item: str) -> Unit: ...


# ``cast`` because ``pint.UnitRegistry(...)`` is the base registry, not assignable to the
# ``_TypedRegistry`` subclass on its own. At runtime this is the ordinary registry; the cast is type-only.
U = cast("_TypedRegistry", pint.UnitRegistry(autoconvert_offset_to_baseunit=True))
"""The shared pint unit registry, as in ``5 * U.mm`` or ``U.Quantity(1.2, "T")``."""
U.formatter = RichEngineeringFormatter(registry=U)

if not TYPE_CHECKING:
    # Runtime bindings (the classes above are type-only). ``Unit`` is pint's; ``Quantity`` and
    # ``VectorQuantity`` are the registry's Quantity class, so Pydantic's isinstance checks work.
    Unit = pint.Unit
    Quantity = VectorQuantity = U.Quantity


@overload
def quantity(value: float, units: Unit | str | None = None) -> Quantity: ...
@overload
def quantity(value: Vec3, units: Unit | str | None = None) -> VectorQuantity: ...
def quantity(value: float | Vec3, units: Unit | str | None = None) -> Quantity | VectorQuantity:
    """
    Statically-typed face of ``U.Quantity(...)``: a ``float`` magnitude yields [`Quantity`][], a Vec3 yields [`VectorQuantity`][].

    Prefer a string for composite units (``"newton * meter"``).
    """
    return cast("Quantity | VectorQuantity", U.Quantity(value, units))  # pyright: ignore[reportCallIssue, reportArgumentType]


class _DimensionedQuantity:
    """
    Base for the Annotated metadata adapting a dimensionality-constrained pint Quantity to a Pydantic field.

    Subclasses supply ``_validate`` / ``_serialize`` / the JSON schema; carried as Annotated metadata so the module aliases below are real type aliases.
    """

    def __init__(self, dimensionality: str) -> None:
        self.dimensionality = dimensionality

    def _validate(self, v: object) -> Quantity:
        raise NotImplementedError

    def _serialize(self, q: Quantity) -> object:
        raise NotImplementedError

    def _require_dimensionality(self, q: Quantity) -> None:
        if not q.check(self.dimensionality):
            raise ValueError(f"expected dimensionality {self.dimensionality}, got {q.dimensionality}")

    def __get_pydantic_core_schema__(self, source: object, handler: object) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            self._validate,
            serialization=core_schema.plain_serializer_function_ser_schema(self._serialize),
        )


class _ScalarQuantity(_DimensionedQuantity):
    """Annotated metadata for a scalar pint Quantity constrained to a dimensionality (material properties)."""

    def _validate(self, v: object) -> Quantity:
        if isinstance(v, U.Quantity):
            q = cast(Quantity, v)
        elif isinstance(v, (int, float, str)):
            q = cast(Quantity, U.Quantity(v))
        else:
            raise ValueError("expected a scalar Quantity, a number or a quantity string")

        bare: bool = not isinstance(v, U.Quantity) and q.dimensionality == _DIMENSIONLESS
        if bare and _dimensionality(self.dimensionality) != _DIMENSIONLESS:
            compatible = next(iter(U.get_compatible_units(_dimensionality(self.dimensionality))))
            si_units = cast(Quantity, U.Quantity(1, compatible)).to_base_units().units
            raise ValueError(f'expected dimensionality {self.dimensionality}, got the bare number {v}; give it units, as in "{v} {si_units:~}"')

        self._require_dimensionality(q)
        return q

    def _serialize(self, q: Quantity) -> str:
        return f"{q.magnitude} {q.units:~}"

    def __get_pydantic_json_schema__(self, core: object, handler: object) -> dict[str, str]:
        return {
            "type": "string",
            "x-unit-dimensionality": self.dimensionality,
            "description": f"pint quantity with dimensionality {self.dimensionality}",
        }


def quantity_type(dimensionality: str) -> object:
    """A pint Quantity constrained to a dimensionality, usable as a Pydantic field (the named scalar aliases below are the common cases)."""
    return Annotated[Quantity, _ScalarQuantity(dimensionality)]


class _VectorQuantity(_DimensionedQuantity):
    """
    Annotated metadata for a pint Quantity with a [`common.vector.Vec3`][femforge.common.vector.Vec3] magnitude, constrained to a dimensionality.

    Accepts a vector Quantity or a ``{"x", "y", "z", "units"}`` dict and round-trips through that JSON form.
    """

    def _validate(self, v: object) -> Quantity:
        if isinstance(v, U.Quantity):
            q = cast(Quantity, v)
        elif isinstance(v, dict):
            q = cast(Quantity, U.Quantity(Vec3(v["x"], v["y"], v["z"]), v["units"]))  # pyright: ignore[reportCallIssue, reportArgumentType, reportUnknownArgumentType]
        else:
            raise ValueError("expected a vector Quantity or a {x, y, z, units} dict")
        mag = q.magnitude
        if not (hasattr(mag, "x") and hasattr(mag, "y") and hasattr(mag, "z")):
            raise ValueError("expected a vector quantity (a Vec3 magnitude)")
        self._require_dimensionality(q)
        return q

    def _serialize(self, q: Quantity) -> dict[str, object]:
        # Full (non-abbreviated) unit name so composite units like torque ("meter * newton") re-parse cleanly on the way back in.
        mag = cast(Vec3, q.magnitude)
        return {"x": mag.x, "y": mag.y, "z": mag.z, "units": str(q.units)}

    def __get_pydantic_json_schema__(self, core: object, handler: object) -> dict[str, object]:
        num = {"type": "number"}
        return {
            "type": "object",
            "properties": {"x": num, "y": num, "z": num, "units": {"type": "string"}},
            "required": ["x", "y", "z", "units"],
            "x-unit-dimensionality": self.dimensionality,
            "description": f"vector pint quantity (Vec3 magnitude) with dimensionality {self.dimensionality}",
        }


def vector_quantity_type(dimensionality: str) -> object:
    """A vector pint Quantity (Vec3 magnitude) constrained to a dimensionality, usable as a Pydantic field."""
    return Annotated[Quantity, _VectorQuantity(dimensionality)]


# --- dimensionality vocabulary: named once so the quantity_type aliases and the property-function
# field types (physical.property_functions) reference the same string rather than re-spelling it.
DIM_LENGTH: str = "[length]"
DIM_TEMPERATURE: str = "[temperature]"
DIM_THERMAL_CONDUCTIVITY: str = "[power]/[length]/[temperature]"
DIM_SPECIFIC_HEAT: str = "[energy]/[mass]/[temperature]"
DIM_DENSITY: str = "[mass]/[length]**3"
DIM_FLUX_DENSITY: str = "[mass]/[current]/[time]**2"
DIM_FIELD_STRENGTH: str = "[current]/[length]"
DIM_PRESSURE: str = "[pressure]"
DIM_HEAT_FLUX: str = "[power]/[length]**2"
DIM_HEAT_TRANSFER_COEFFICIENT: str = "[power]/[length]**2/[temperature]"
DIM_ELECTRICAL_CONDUCTIVITY: str = "[current]**2*[time]**3/[mass]/[length]**3"
DIM_RESISTIVITY: str = "[mass]*[length]**3/[current]**2/[time]**3"
DIM_VOLTAGE: str = "[mass]*[length]**2/[current]/[time]**3"
DIM_CURRENT: str = "[current]"
DIM_CURRENT_DENSITY: str = "[current]/[length]**2"
DIM_POWER_DENSITY: str = "[power]/[length]**3"
DIM_PERMITTIVITY: str = "[current]**2*[time]**4/[mass]/[length]**3"
DIM_PERMEABILITY: str = "[mass]*[length]/[current]**2/[time]**2"
DIM_FORCE: str = "[force]"
DIM_TORQUE: str = "[force]*[length]"
DIM_DIMENSIONLESS: str = ""


# An empty UnitsContainer; the canonical "no dimensions" value to compare against.
_DIMENSIONLESS = (1 * U.dimensionless).dimensionality


def _dimensionality(spec: str):
    """
    Parse a pint dimensionality string to a UnitsContainer for comparison.

    Handles the dimensionless spellings ('' and 'dimensionless'), which pint's ``get_dimensionality`` cannot parse directly.
    """
    s: str = spec.strip()
    if s in ("", "dimensionless"):
        return _DIMENSIONLESS
    return U.get_dimensionality(s)


def dimensionality_matches(expected: str, got: str) -> bool:
    """True if two pint dimensionality strings denote the same dimensionality (compares parsed forms, so equivalent spellings match)."""
    return _dimensionality(expected) == _dimensionality(got)


# Field-type aliases. Written as direct ``Annotated[...]`` type expressions (not
# ``quantity_type(...)`` calls) so each name is a real *type alias* a type checker
# accepts in field position; the factories above stay for any dynamic callers.
Length = Annotated[Quantity, _ScalarQuantity(DIM_LENGTH)]
Temperature = Annotated[Quantity, _ScalarQuantity(DIM_TEMPERATURE)]
ThermalConductivity = Annotated[Quantity, _ScalarQuantity(DIM_THERMAL_CONDUCTIVITY)]
Conductivity = ThermalConductivity
SpecificHeat = Annotated[Quantity, _ScalarQuantity(DIM_SPECIFIC_HEAT)]
Density = Annotated[Quantity, _ScalarQuantity(DIM_DENSITY)]
FluxDensity = Annotated[Quantity, _ScalarQuantity(DIM_FLUX_DENSITY)]
FieldStrength = Annotated[Quantity, _ScalarQuantity(DIM_FIELD_STRENGTH)]
Pressure = Annotated[Quantity, _ScalarQuantity(DIM_PRESSURE)]
HeatFlux = Annotated[Quantity, _ScalarQuantity(DIM_HEAT_FLUX)]
HeatTransferCoefficient = Annotated[Quantity, _ScalarQuantity(DIM_HEAT_TRANSFER_COEFFICIENT)]
ElectricalConductivity = Annotated[Quantity, _ScalarQuantity(DIM_ELECTRICAL_CONDUCTIVITY)]
Resistivity = Annotated[Quantity, _ScalarQuantity(DIM_RESISTIVITY)]
Voltage = Annotated[Quantity, _ScalarQuantity(DIM_VOLTAGE)]
Current = Annotated[Quantity, _ScalarQuantity(DIM_CURRENT)]
CurrentDensity = Annotated[Quantity, _ScalarQuantity(DIM_CURRENT_DENSITY)]
PowerDensity = Annotated[Quantity, _ScalarQuantity(DIM_POWER_DENSITY)]
Permittivity = Annotated[Quantity, _ScalarQuantity(DIM_PERMITTIVITY)]
Permeability = Annotated[Quantity, _ScalarQuantity(DIM_PERMEABILITY)]
Force = Annotated[VectorQuantity, _VectorQuantity(DIM_FORCE)]
Torque = Annotated[VectorQuantity, _VectorQuantity(DIM_TORQUE)]
Dimensionless = Annotated[Quantity, _ScalarQuantity(DIM_DIMENSIONLESS)]
