"""Material properties as pint-aware callables (Static, Calibration, ClosedForm, Expression) behind the [`PropertyFunction`][] protocol."""

from __future__ import annotations

from bisect import bisect_right
from typing import Annotated, Callable, Literal, Mapping, Protocol, cast, runtime_checkable

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_serializer, field_validator, model_validator
from pydantic_core import core_schema

from femforge.physical.expressions import Expr
from femforge.physical.units import (
    Quantity,
    U,
    dimensionality_matches,
)


__all__ = [
    "PropertyFunction",
    "BasePropertyFunction",
    "Static",
    "CalibrationPoint",
    "Calibration",
    "ClosedForm",
    "Expression",
    "PropertyError",
    "PropertyParameterError",
    "PropertyDimensionError",
    "PropertyRangeError",
    "PropertyFunctionField",
    "property_function_type",
]


# ---------------------------------------------------------------------------
# Typed errors: dedicated types, not bare ValueError, so callers can catch precisely.
# ---------------------------------------------------------------------------


class PropertyError(Exception):
    """Base class for every property-function error."""


class PropertyParameterError(PropertyError):
    """A required parameter was not supplied when evaluating a property."""

    def __init__(self, fn: "PropertyFunction", missing: set[str]) -> None:
        """Name the parameters of ``fn`` the call left out."""
        self.fn: PropertyFunction = fn
        self.missing: set[str] = set(missing)
        wanted: str = ", ".join(f"{name}=<{dim or 'dimensionless'}>" for name, dim in fn.parameters.items())
        names: str = ", ".join(sorted(self.missing))
        super().__init__(f"{type(fn).__name__} requires [{wanted}]; not supplied: {names}.")


class PropertyDimensionError(PropertyError):
    """A supplied parameter had the wrong dimensionality."""

    def __init__(self, fn: "PropertyFunction", name: str, expected: str, got: object) -> None:
        """Record which parameter of ``fn`` had the wrong dimensionality.

        Args:
            fn: The property that rejected the call.
            name: The offending parameter, or ``<result>`` for a closed form's return value.
            expected: The dimensionality the parameter needs.
            got: The dimensionality, or the type, that was supplied.
        """
        self.fn: PropertyFunction = fn
        self.name: str = name
        self.expected: str = expected
        self.got: object = got
        super().__init__(f"{type(fn).__name__} parameter {name!r} expected {expected or 'dimensionless'}, got {got}.")


class PropertyRangeError(PropertyError):
    """A parameter fell outside the calibrated range and extrapolation is off."""

    def __init__(self, fn: "PropertyFunction", name: str, value: object, low: object, high: object) -> None:
        """Record a parameter of ``fn`` that fell outside the calibrated range.

        Args:
            fn: The calibration that rejected the call.
            name: The out-of-range parameter.
            value: The supplied value.
            low: The lowest calibrated value.
            high: The highest calibrated value.
        """
        self.fn: PropertyFunction = fn
        self.name: str = name
        super().__init__(f"{type(fn).__name__} parameter {name!r}={value} is outside the calibrated range [{low}, {high}]; set extrapolate=True to allow.")


# ---------------------------------------------------------------------------
# The callable contract.
# ---------------------------------------------------------------------------


@runtime_checkable
class PropertyFunction(Protocol):
    """A material property as a function of zero or more named pint quantities, returning a pint Quantity."""

    @property
    def parameters(self) -> Mapping[str, str]:
        """Required parameter name -> expected dimensionality (pint string); this IS the call schema."""
        ...

    @property
    def result_dimensionality(self) -> str:
        """Dimensionality of the returned quantity, e.g. ``'[power]/[length]/[temperature]'``."""
        ...

    def __call__(self, **kwargs: Quantity) -> Quantity:
        """Evaluate. Raises a typed error if a required parameter is missing or has the wrong dimensionality."""
        ...


class BasePropertyFunction(BaseModel):
    """
    Pydantic base implementing parameter checking once; subclasses declare ``parameters`` / ``result_dimensionality`` and implement ``_evaluate``.

    Extra keyword arguments are ignored, so an operating point may carry more quantities than a property needs.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    @property
    def parameters(self) -> Mapping[str, str]:
        """Required parameter name to expected dimensionality, as a pint string."""
        raise NotImplementedError

    @property
    def result_dimensionality(self) -> str:
        """Dimensionality of the returned quantity."""
        raise NotImplementedError

    def _check(self, kwargs: dict[str, Quantity]) -> None:
        missing: set[str] = set(self.parameters) - kwargs.keys()
        if missing:
            raise PropertyParameterError(self, missing=missing)
        for name, dim in self.parameters.items():
            q: Quantity = kwargs[name]
            if not isinstance(q, U.Quantity) or not q.check(dim):
                got: object = q.dimensionality if isinstance(q, U.Quantity) else f"{type(q).__name__} (not a quantity)"
                raise PropertyDimensionError(self, name, expected=dim, got=got)

    def __call__(self, **kwargs: Quantity) -> Quantity:
        self._check(kwargs)
        return self._evaluate(**kwargs)

    def _evaluate(self, **kwargs: Quantity) -> Quantity:  # subclass fills in
        raise NotImplementedError

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.result_dimensionality or 'dimensionless'})"


def _as_quantity(v: object) -> Quantity:
    """Coerce a value (Quantity / string / number) into a pint Quantity."""
    if isinstance(v, U.Quantity):
        return cast(Quantity, v)

    if isinstance(v, (int, float, str)):
        return cast(Quantity, U.Quantity(v))

    raise TypeError("expected a Quantity, a number or a quantity string")


def _quantity_string(q: Quantity) -> str:
    """Render a Quantity as the parseable ``'<magnitude> <units>'`` string ``_as_quantity`` revives."""
    return f"{q.magnitude} {q.units:~}"


# ---------------------------------------------------------------------------
# 1) Static – the 0-parameter baseline.
# ---------------------------------------------------------------------------


class Static(BasePropertyFunction):
    """
    A constant property: zero parameters, always returns ``value``.

    Authored as ``Static(value="8.7 W/(m*K)")``; ignores any operating-point arguments.
    """

    value: Quantity
    """`[?]`: The constant returned at every operating point."""

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, v: object) -> Quantity:
        return _as_quantity(v)

    @property
    def parameters(self) -> Mapping[str, str]:
        """No parameters: a constant ignores the operating point."""
        return {}

    @property
    def result_dimensionality(self) -> str:
        """The dimensionality of ``value``."""
        return str(self.value.dimensionality)

    def _evaluate(self, **kwargs: Quantity) -> Quantity:
        return self.value

    def __str__(self) -> str:
        return f"{self.value}"


# ---------------------------------------------------------------------------
# 2) Calibration – interpolation from sample points (1-D for now).
# ---------------------------------------------------------------------------


class CalibrationPoint(BaseModel):
    """One measured sample: input quantities by name and the resulting output."""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    inputs: dict[str, Quantity]
    """`[?]`: The input quantities, keyed by parameter name."""

    output: Quantity
    """`[?]`: The property value measured at ``inputs``."""

    @field_validator("inputs", mode="before")
    @classmethod
    def _coerce_inputs(cls, v: object) -> dict[str, Quantity]:
        if not isinstance(v, Mapping):
            raise TypeError("CalibrationPoint inputs must be a mapping of name -> quantity")
        items = cast(Mapping[str, object], v)
        return {name: _as_quantity(value) for name, value in items.items()}

    @field_validator("output", mode="before")
    @classmethod
    def _coerce_output(cls, v: object) -> Quantity:
        return _as_quantity(v)

    @field_serializer("inputs", when_used="json")
    def _serialize_inputs(self, v: dict[str, Quantity]) -> dict[str, str]:
        return {name: _quantity_string(q) for name, q in v.items()}

    @field_serializer("output", when_used="json")
    def _serialize_output(self, v: Quantity) -> str:
        return _quantity_string(v)


def _sign(v: float) -> int:
    return (v > 0.0) - (v < 0.0)


def _pchip_edge_slope(h0: float, h1: float, d0: float, d1: float) -> float:
    """Endpoint derivative for PCHIP: scipy's variant — non-centered three-point formula with the Fritsch-Carlson monotonicity clamp."""
    m: float = ((2.0 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
    if _sign(m) != _sign(d0):
        return 0.0
    if _sign(d0) != _sign(d1) and abs(m) > 3.0 * abs(d0):
        return 3.0 * d0
    return m


def _pchip_slopes(xs: list[float], ys: list[float]) -> list[float]:
    """Fritsch-Carlson monotone node derivatives for the cubic Hermite pieces."""
    h: list[float] = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    d: list[float] = [(ys[i + 1] - ys[i]) / h[i] for i in range(len(h))]
    if len(xs) == 2:
        return [d[0], d[0]]
    m: list[float] = [0.0] * len(xs)
    for k in range(1, len(xs) - 1):
        h0, h1, d0, d1 = h[k - 1], h[k], d[k - 1], d[k]
        # Derivative 0 where secants change sign or vanish (the monotonicity condition), else the weighted harmonic mean.
        if _sign(d0) * _sign(d1) > 0:
            m[k] = 3.0 * (h0 + h1) / ((2.0 * h1 + h0) / d0 + (h1 + 2.0 * h0) / d1)
    m[0] = _pchip_edge_slope(h[0], h[1], d[0], d[1])
    m[-1] = _pchip_edge_slope(h[-1], h[-2], d[-1], d[-2])
    return m


def _pchip_eval(x: float, xs: list[float], ys: list[float]) -> float:
    """Evaluate the monotone cubic Hermite through (xs, ys) at x, clamping outside the table."""
    # Clamp to the endpoint values outside the range, matching the linear branch's np.interp behaviour under extrapolate=True.
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]

    m: list[float] = _pchip_slopes(xs, ys)

    i: int = bisect_right(xs, x) - 1
    h: float = xs[i + 1] - xs[i]
    t: float = (x - xs[i]) / h

    h00: float = (1.0 + 2.0 * t) * (1.0 - t) ** 2
    h10: float = t * (1.0 - t) ** 2
    h01: float = t * t * (3.0 - 2.0 * t)
    h11: float = t * t * (t - 1.0)
    return h00 * ys[i] + h10 * h * m[i] + h01 * ys[i + 1] + h11 * h * m[i + 1]


def _interpolate_1d(x: float, xs: list[float], ys: list[float], method: str, fn: "Calibration", name: str, extrapolate: bool) -> float:
    """Interpolate ``ys`` at ``x`` over sorted-by-``xs`` samples; ``xs`` need not be pre-sorted."""
    order: list[int] = sorted(range(len(xs)), key=lambda i: xs[i])
    xs_sorted: list[float] = [xs[i] for i in order]
    ys_sorted: list[float] = [ys[i] for i in order]

    if not extrapolate and (x < xs_sorted[0] or x > xs_sorted[-1]):
        raise PropertyRangeError(fn, name, value=x, low=xs_sorted[0], high=xs_sorted[-1])

    if method == "linear":
        # np.interp clamps to the endpoints outside the range, the desired behaviour once extrapolate=True has let us through.
        return float(np.interp(x, xs_sorted, ys_sorted))
    if method == "nearest":
        idx: int = min(range(len(xs_sorted)), key=lambda i: abs(xs_sorted[i] - x))
        return ys_sorted[idx]
    if method == "cubic":
        if len(xs_sorted) < 2:
            raise ValueError(f"Calibration method 'cubic' needs at least 2 points; got {len(xs_sorted)}.")
        return _pchip_eval(x, xs_sorted, ys_sorted)
    raise NotImplementedError(f"interpolation method {method!r} is not implemented")


class Calibration(BasePropertyFunction):
    """
    A property defined by sample points and interpolated between them (1-D only for now).

    Point inputs are validated against ``param_dims`` at construction; out-of-range raises unless ``extrapolate=True``.
    """

    kind: Literal["calibration"] = "calibration"
    """Discriminator that names this property kind in serialized JSON."""

    param_dims: dict[str, str]
    """The single parameter name and its expected dimensionality, as a pint string."""

    points: list[CalibrationPoint]
    """The samples to interpolate between."""

    method: Literal["linear", "nearest", "cubic"] = "linear"
    """Interpolation between samples: linear, nearest, or monotone cubic (PCHIP)."""

    extrapolate: bool = False
    """Clamp to the end samples outside the calibrated range instead of raising."""

    @model_validator(mode="after")
    def _validate_points(self) -> "Calibration":
        # N-D calibration is not yet supported; enforce the 1-D limit up front.
        if len(self.param_dims) != 1:
            raise ValueError(f"Calibration is 1-D for now; got {len(self.param_dims)} parameters {sorted(self.param_dims)}.")
        if not self.points:
            raise ValueError("Calibration needs at least one point.")
        output_dim: object = self.points[0].output.dimensionality
        for point in self.points:
            if set(point.inputs) != set(self.param_dims):
                raise ValueError(f"calibration point inputs {sorted(point.inputs)} do not match declared parameters {sorted(self.param_dims)}.")
            for name, dim in self.param_dims.items():
                q: Quantity = point.inputs[name]
                if not q.check(dim):
                    raise PropertyDimensionError(self, name, expected=dim, got=q.dimensionality)
            if point.output.dimensionality != output_dim:
                raise ValueError(f"calibration point outputs have inconsistent dimensionality ({point.output.dimensionality} vs {output_dim}).")
        return self

    @property
    def parameters(self) -> Mapping[str, str]:
        """``param_dims`` as the call schema."""
        return dict(self.param_dims)

    @property
    def result_dimensionality(self) -> str:
        """The dimensionality of the sample outputs."""
        return str(self.points[0].output.dimensionality)

    def _evaluate(self, **kwargs: Quantity) -> Quantity:
        ((name, _dim),) = self.param_dims.items()  # 1-D: exactly one parameter

        sample_x: Quantity = self.points[0].inputs[name]
        sample_y: Quantity = self.points[0].output
        x_unit: object = sample_x.units
        out_unit: object = sample_y.units

        xs: list[float] = [float(p.inputs[name].to(x_unit).magnitude) for p in self.points]
        ys: list[float] = [float(p.output.to(out_unit).magnitude) for p in self.points]
        x: float = float(kwargs[name].to(x_unit).magnitude)

        result: float = _interpolate_1d(x, xs, ys, self.method, self, name, self.extrapolate)
        return cast(Quantity, result * out_unit)


# ---------------------------------------------------------------------------
# 3) ClosedForm – explicit Python callable.
# ---------------------------------------------------------------------------


class ClosedForm(BasePropertyFunction):
    """A property defined by an explicit Python callable, pint-in / pint-out; the callable is code, not JSON-serializable."""

    param_dims: dict[str, str]
    """Parameter name to expected dimensionality, as a pint string."""

    result_dim: str
    """The dimensionality the callable must return, as a pint string."""

    expression: Callable[..., Quantity]
    """The callable, given the declared parameters as keyword quantities."""

    @property
    def parameters(self) -> Mapping[str, str]:
        """``param_dims`` as the call schema."""
        return dict(self.param_dims)

    @property
    def result_dimensionality(self) -> str:
        """The declared ``result_dim``."""
        return self.result_dim

    def _evaluate(self, **kwargs: Quantity) -> Quantity:
        args: dict[str, Quantity] = {name: kwargs[name] for name in self.param_dims}
        result: Quantity = self.expression(**args)
        if not isinstance(result, U.Quantity) or not result.check(self.result_dim):
            got: object = result.dimensionality if isinstance(result, U.Quantity) else f"{type(result).__name__} (not a quantity)"
            raise PropertyDimensionError(self, "<result>", expected=self.result_dim, got=got)
        return cast(Quantity, result)


# ---------------------------------------------------------------------------
# 4) Expression – a structured, serializable law.
# ---------------------------------------------------------------------------


class Expression(BasePropertyFunction):
    """
    A property defined by a serializable typed expression AST (`physical.expressions`) — [`ClosedForm`][] as data instead of code.

    ``body`` computes the result's SI magnitude in ``result_units`` from parameters bound as SI-base magnitudes, matching Elmer's ``tx``.
    """

    kind: Literal["expression"] = "expression"
    """Discriminator that names this property kind in serialized JSON."""

    param_dims: dict[str, str]
    """Parameter name to expected dimensionality, as a pint string."""

    result_units: str
    """The pint units of the magnitude ``body`` computes."""

    body: Expr
    """The law over parameters bound as SI-base magnitudes."""

    @model_validator(mode="after")
    def _validate_body_parameters(self) -> "Expression":
        unknown: frozenset[str] = self.body.parameters() - self.param_dims.keys()
        if unknown:
            raise ValueError(f"expression body references undeclared parameters {sorted(unknown)}; declared: {sorted(self.param_dims)}.")
        return self

    @property
    def parameters(self) -> Mapping[str, str]:
        """``param_dims`` as the call schema."""
        return dict(self.param_dims)

    @property
    def result_dimensionality(self) -> str:
        """The dimensionality of ``result_units``."""
        return str(U.parse_units(self.result_units).dimensionality)

    def _evaluate(self, **kwargs: Quantity) -> Quantity:
        # Bind each declared parameter as its SI-base magnitude — the same convention the Elmer runtime uses.
        env: dict[str, float] = {name: float(kwargs[name].to_base_units().magnitude) for name in self.param_dims}
        return cast(Quantity, self.body.evaluate(env) * U.parse_units(self.result_units))


# ---------------------------------------------------------------------------
# Pydantic field type: a property of a declared dimensionality (mirrors physical.units.quantity_type).
# ---------------------------------------------------------------------------


def _serialize_property_function(fn: PropertyFunction, info: core_schema.SerializationInfo) -> object:
    """
    Serialize a property-function field: Static -> its unit string, Expression/Calibration -> a ``kind``-tagged dump (quantities as strings in JSON mode).

    Code-bearing kinds pass through as-is (python-mode only).
    """
    if isinstance(fn, Static):
        return _quantity_string(fn.value)
    if isinstance(fn, (Expression, Calibration)):
        return fn.model_dump(mode=info.mode)
    return fn


# The dumped forms that can be revived: each carries a ``kind`` literal, so pydantic discriminates without a lookup table.
_Serialized = Annotated[Calibration | Expression, Field(discriminator="kind")]
_SERIALIZED = TypeAdapter[Calibration | Expression](_Serialized)


class PropertyFunctionField:
    """
    Annotated metadata for a [`PropertyFunction`][] constrained to ``dimensionality``.

    Coerces bare quantities/strings into [`Static`][], validates result dimensionality, and exports quantity_type's unit-annotated JSON schema.
    """

    def __init__(self, dimensionality: str) -> None:
        """Constrain the field to properties of ``dimensionality``."""
        self.dimensionality = dimensionality

    def __get_pydantic_core_schema__(self, source: object, handler: object) -> core_schema.CoreSchema:
        dimensionality = self.dimensionality

        def validate(v: object) -> PropertyFunction:
            fn: PropertyFunction
            if isinstance(v, BasePropertyFunction):
                fn = v
            elif isinstance(v, Mapping) and "kind" in cast("Mapping[str, object]", v):
                # The tagged form _serialize_property_function dumps; revive so a law/table round-trips.
                fn = _SERIALIZED.validate_python(dict(cast("Mapping[str, object]", v)))
            else:
                fn = Static(value=_as_quantity(cast(object, v)))
            if not dimensionality_matches(dimensionality, fn.result_dimensionality):
                raise ValueError(f"expected property of dimensionality {dimensionality or 'dimensionless'}, got {fn.result_dimensionality}")
            return fn

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(_serialize_property_function, info_arg=True),
        )

    def __get_pydantic_json_schema__(self, core: object, handler: object) -> dict[str, str]:
        return {
            "type": "string",
            "x-unit-dimensionality": self.dimensionality,
            "description": f"pint quantity with dimensionality {self.dimensionality}",
        }


def property_function_type(dimensionality: str) -> object:
    """A [`PropertyFunction`][] constrained to ``dimensionality``, usable as a Pydantic field."""
    return Annotated[PropertyFunction, PropertyFunctionField(dimensionality)]
