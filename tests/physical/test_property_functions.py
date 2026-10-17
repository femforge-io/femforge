"""Property functions: Static / Calibration / ClosedForm."""

import json
from typing import cast

import pytest

from femforge.physical.expressions import Param
from femforge.physical.property_functions import (
    Calibration,
    CalibrationPoint,
    ClosedForm,
    Expression,
    PropertyDimensionError,
    PropertyFunction,
    PropertyParameterError,
    PropertyRangeError,
    Static,
)
from femforge.physical.units import CANONICAL_UNITS, Quantity, U, quantity
from tests.physical._sif import sif_comment, sif_value


# --- Static ----------------------------------------------------------------


def test_static_returns_its_value():
    k = Static(value=8.7 * U.W / (U.m * U.K))
    assert k().to(U.W / (U.m * U.K)).magnitude == pytest.approx(8.7)
    assert k.parameters == {}
    # Static satisfies the PropertyFunction protocol structurally.
    assert isinstance(k, PropertyFunction)


def test_static_ignores_operating_point_kwargs():
    # Static takes 0 parameters, so an operating point (extra kwargs) is ignored, letting to_elmer(at=...) call it uniformly.
    k = Static(value=8.7 * U.W / (U.m * U.K))
    assert k(temperature=300 * U.K).magnitude == pytest.approx(8.7)


def test_static_accepts_unit_string() -> None:
    # The before-validator coerces a unit string into a Quantity; cast quiets the declared Quantity type.
    k = Static(value=cast(Quantity, "8.7 W/(m*K)"))
    assert k().to(U.W / (U.m * U.K)).magnitude == pytest.approx(8.7)


def test_static_rejects_non_scalar_value() -> None:
    with pytest.raises(TypeError, match="expected a Quantity"):
        Static(value=cast(Quantity, None))


# --- Calibration ------------------------------------------------------------


def _two_point_line() -> Calibration:
    # A straight line y = 1 + 0.05*(T - 300) [W/(m*K)] sampled at 300 K (-> 1.0) and 400 K (-> 6.0); midpoint is exact.
    return Calibration(
        param_dims={"temperature": "[temperature]"},
        points=[
            CalibrationPoint(inputs={"temperature": 300 * U.K}, output=1.0 * U.W / (U.m * U.K)),
            CalibrationPoint(inputs={"temperature": 400 * U.K}, output=6.0 * U.W / (U.m * U.K)),
        ],
    )


def test_calibration_interpolates_endpoints_and_midpoint_exactly():
    cal = _two_point_line()
    unit = U.W / (U.m * U.K)
    assert cal(temperature=300 * U.K).to(unit).magnitude == pytest.approx(1.0)
    assert cal(temperature=400 * U.K).to(unit).magnitude == pytest.approx(6.0)
    assert cal(temperature=350 * U.K).to(unit).magnitude == pytest.approx(3.5)
    assert cal.parameters == {"temperature": "[temperature]"}


def test_calibration_converts_input_units_before_interpolating():
    cal = _two_point_line()
    unit = U.W / (U.m * U.K)
    # 350 K expressed in Celsius (76.85 degC) gives the same midpoint value.
    assert cal(temperature=76.85 * U.degC).to(unit).magnitude == pytest.approx(3.5, rel=1e-3)


def test_calibration_rejects_unit_mismatched_point():
    with pytest.raises(PropertyDimensionError):
        Calibration(
            param_dims={"temperature": "[temperature]"},
            points=[CalibrationPoint(inputs={"temperature": 5 * U.kg}, output=1.0 * U.W / (U.m * U.K))],
        )


def test_calibration_out_of_range_raises_by_default():
    cal = _two_point_line()
    with pytest.raises(PropertyRangeError):
        cal(temperature=500 * U.K)


def test_calibration_extrapolation_is_opt_in():
    # With extrapolate=True, np.interp clamps to the endpoint rather than raising.
    cal = Calibration(
        param_dims={"temperature": "[temperature]"},
        points=[
            CalibrationPoint(inputs={"temperature": 300 * U.K}, output=1.0 * U.W / (U.m * U.K)),
            CalibrationPoint(inputs={"temperature": 400 * U.K}, output=6.0 * U.W / (U.m * U.K)),
        ],
        extrapolate=True,
    )
    assert cal(temperature=500 * U.K).to(U.W / (U.m * U.K)).magnitude == pytest.approx(6.0)


def test_calibration_nearest_method():
    cal = Calibration(
        param_dims={"temperature": "[temperature]"},
        points=[
            CalibrationPoint(inputs={"temperature": 300 * U.K}, output=1.0 * U.W / (U.m * U.K)),
            CalibrationPoint(inputs={"temperature": 400 * U.K}, output=6.0 * U.W / (U.m * U.K)),
        ],
        method="nearest",
    )
    assert cal(temperature=310 * U.K).to(U.W / (U.m * U.K)).magnitude == pytest.approx(1.0)
    assert cal(temperature=390 * U.K).to(U.W / (U.m * U.K)).magnitude == pytest.approx(6.0)


def test_calibration_rejects_nd_for_now():
    with pytest.raises(ValueError):
        Calibration(
            param_dims={"temperature": "[temperature]", "pressure": "[pressure]"},
            points=[
                CalibrationPoint(
                    inputs={"temperature": 300 * U.K, "pressure": 1 * U.bar},
                    output=1.0 * U.W / (U.m * U.K),
                )
            ],
        )


# --- ClosedForm -------------------------------------------------------------


def _br_of_t() -> ClosedForm:
    # Linear remanence falloff: Br(T) = Br0 * (1 + alpha*(T - T0)).
    br0 = 1.48 * U.T
    alpha = -0.0012 / U.K  # ~ -0.12 %/K, typical NdFeB
    t0 = 293.15 * U.K

    def expression(*, temperature: Quantity) -> Quantity:
        return cast(Quantity, br0 * (1 + alpha * (temperature - t0)))

    return ClosedForm(param_dims={"temperature": "[temperature]"}, result_dim="[magnetic_field]", expression=expression)


def test_closed_form_evaluates_and_round_trips_units():
    br = _br_of_t()
    # At the reference temperature it returns Br0 exactly.
    assert br(temperature=293.15 * U.K).to(U.T).magnitude == pytest.approx(1.48)
    # 100 K hotter -> 1.48 * (1 - 0.12) = 1.3024 T.
    assert br(temperature=393.15 * U.K).to(U.T).magnitude == pytest.approx(1.48 * (1 - 0.12), rel=1e-9)
    assert br.result_dimensionality == "[magnetic_field]"


def test_closed_form_result_is_a_quantity_in_expected_dimension():
    br = _br_of_t()
    result = br(temperature=300 * U.K)
    assert isinstance(result, U.Quantity)
    assert result.check("[magnetic_field]")


# --- Typed errors (shared base) --------------------------------------------


def test_missing_parameter_raises_typed_error():
    cal = _two_point_line()
    with pytest.raises(PropertyParameterError):
        cal()  # temperature not supplied


def test_wrong_dimension_parameter_raises_typed_error():
    cal = _two_point_line()
    with pytest.raises(PropertyDimensionError):
        cal(temperature=5 * U.kg)


def test_typed_errors_are_not_bare_value_errors():
    # They subclass PropertyError, not ValueError, so callers can catch the property-function family precisely.
    assert not issubclass(PropertyParameterError, ValueError)
    assert not issubclass(PropertyDimensionError, ValueError)


# --- to_elmer(at=...) for each kind -----------------------------------------


def test_to_elmer_strips_to_si_float_for_each_kind():
    from femforge.materials.properties import MagneticProperties, ThermalProperties

    at = {"temperature": 393.15 * U.K}

    # Static: ignores `at`. Assert the numeric SI value and that the '! unit' annotation is present.
    static_thermal = ThermalProperties(conductivity=Static(value=8.7 * U.W / (U.m * U.K)))
    static_emitted = static_thermal.to_elmer(at=at)["Heat Conductivity"]
    assert sif_value(static_emitted) == pytest.approx(8.7)
    assert sif_comment(static_emitted) is not None

    # Calibration: consumes `at`; 393.15 K on the 2-point line -> 1 + 0.05*93.15.
    cal_thermal = ThermalProperties(conductivity=_two_point_line())
    assert sif_value(cal_thermal.to_elmer(at=at)["Heat Conductivity"]) == pytest.approx(1.0 + 0.05 * (393.15 - 300))

    # ClosedForm: a temperature-dependent remanence feeds |M| = Br/mu0.
    MU0 = 1.25663706212e-6
    magnetic = MagneticProperties(remanence=_br_of_t())
    expected_mag = (1.48 * (1 - 0.12)) / MU0
    assert magnetic.magnetization_magnitude(at=at) == pytest.approx(expected_mag, rel=1e-6)


def test_property_field_rejects_wrong_dimensionality_property() -> None:
    from femforge.materials.properties import ThermalProperties

    # A wrong-dimensionality Static on a thermal-conductivity field fails at construction, not at solve time.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ThermalProperties(conductivity=Static(value=5 * U.kg))


# --- __str__ representations -----------------------------------------------


def test_static_str_is_its_value() -> None:
    # Static overrides __str__ to render the value itself.
    k = Static(value=8.7 * U.W / (U.m * U.K))
    assert "8.7" in str(k)


def test_base_property_function_str_uses_result_dimensionality() -> None:
    # The shared BasePropertyFunction.__str__, not overridden by Calibration.
    cal = _two_point_line()
    text: str = str(cal)
    assert text.startswith("Calibration(")
    assert cal.result_dimensionality in text


def test_closed_form_str_uses_result_dimensionality() -> None:
    br = _br_of_t()
    text: str = str(br)
    assert text == f"ClosedForm({br.result_dimensionality})"


def test_base_property_function_str_handles_dimensionless() -> None:
    # The 'dimensionless' fallback when result_dimensionality is empty.
    cal = Calibration(
        param_dims={"ratio": ""},
        points=[CalibrationPoint(inputs={"ratio": 1.0 * U.dimensionless}, output=2.0 * U.dimensionless)],
    )
    assert "dimensionless" in str(cal)


# --- CalibrationPoint input coercion ---------------------------------------


def test_calibration_point_rejects_non_mapping_inputs() -> None:
    # The before-validator raises TypeError for a non-mapping inputs value.
    with pytest.raises(TypeError):
        CalibrationPoint(inputs=cast("dict[str, Quantity]", [1, 2, 3]), output=1.0 * U.W / (U.m * U.K))


# --- Calibration model validation edge cases -------------------------------


def test_calibration_rejects_empty_points() -> None:
    with pytest.raises(ValueError):
        Calibration(param_dims={"temperature": "[temperature]"}, points=[])


def test_calibration_rejects_point_inputs_not_matching_parameters() -> None:
    # A point whose input names differ from the declared parameters is rejected.
    with pytest.raises(ValueError):
        Calibration(
            param_dims={"temperature": "[temperature]"},
            points=[CalibrationPoint(inputs={"pressure": 1 * U.bar}, output=1.0 * U.W / (U.m * U.K))],
        )


def test_calibration_rejects_inconsistent_output_dimensionality() -> None:
    # Two points whose outputs have different dimensionality are rejected.
    with pytest.raises(ValueError):
        Calibration(
            param_dims={"temperature": "[temperature]"},
            points=[
                CalibrationPoint(inputs={"temperature": 300 * U.K}, output=1.0 * U.W / (U.m * U.K)),
                CalibrationPoint(inputs={"temperature": 400 * U.K}, output=5.0 * U.kg),
            ],
        )


# --- ClosedForm result-dimensionality check --------------------------------


def test_closed_form_rejects_wrong_result_dimensionality() -> None:
    # The expression returns the wrong dimensionality -> PropertyDimensionError at call time.
    def bad_expression(*, temperature: Quantity) -> Quantity:
        return cast(Quantity, temperature)  # a temperature, not a flux density

    cf = ClosedForm(
        param_dims={"temperature": "[temperature]"},
        result_dim="[magnetic_field]",
        expression=bad_expression,
    )
    with pytest.raises(PropertyDimensionError):
        cf(temperature=300 * U.K)


def test_closed_form_rejects_non_quantity_result() -> None:
    # The expression returns a non-quantity -> PropertyDimensionError.
    def bad_expression(*, temperature: Quantity) -> Quantity:
        _ = temperature
        return cast(Quantity, 42.0)

    cf = ClosedForm(
        param_dims={"temperature": "[temperature]"},
        result_dim="[temperature]",
        expression=bad_expression,
    )
    with pytest.raises(PropertyDimensionError):
        cf(temperature=300 * U.K)


# --- serialization of property-function fields -----------------------------


def test_static_property_function_serializes_via_unit_string() -> None:
    # A Static field serializes to its unit-annotated string in both dump modes.
    from femforge.materials.properties import ThermalProperties

    thermal = ThermalProperties(conductivity=Static(value=8.7 * U.W / (U.m * U.K)))
    for mode in ("python", "json"):
        dump: dict[str, object] = thermal.model_dump(mode=mode)
        serialized = dump["conductivity"]
        assert isinstance(serialized, str)
        assert "8.7" in serialized


def test_calibration_serializes_to_a_kind_tagged_dump_and_revives() -> None:
    # A Calibration dumps to a 'kind'-tagged mapping, which the field validator discriminates back into a Calibration.
    from femforge.materials.properties import ThermalProperties
    from femforge.physical.property_functions import PropertyFunctionField

    cal = _two_point_line()
    dump: dict[str, object] = ThermalProperties(conductivity=cal).model_dump()
    dumped = dump["conductivity"]
    assert isinstance(dumped, dict) and cast("dict[str, object]", dumped)["kind"] == "calibration"

    restored = ThermalProperties.model_validate({"conductivity": dumped}).conductivity
    assert isinstance(restored, Calibration)
    assert restored(temperature=350 * U.K) == cal(temperature=350 * U.K)
    assert isinstance(PropertyFunctionField("[temperature]"), PropertyFunctionField)


def test_closed_form_property_function_serializes_as_self() -> None:
    # A code-bearing kind has no serializable form, so the serializer keeps the object itself (python-mode only).
    from types import SimpleNamespace

    from pydantic_core import core_schema

    from femforge.physical.property_functions import _serialize_property_function

    cf = _br_of_t()
    info = cast(core_schema.SerializationInfo, SimpleNamespace(mode="python"))
    assert _serialize_property_function(cf, info) is cf


# --- property_function_type factory + field json schema --------------------


def test_property_function_type_factory_returns_annotated_metadata() -> None:
    from femforge.physical.property_functions import PropertyFunctionField, property_function_type

    field_type: object = property_function_type("[temperature]")
    metadata = cast("tuple[object, ...]", getattr(field_type, "__metadata__"))
    assert isinstance(metadata[0], PropertyFunctionField)
    assert metadata[0].dimensionality == "[temperature]"


def test_property_function_field_json_schema() -> None:
    from femforge.physical.property_functions import PropertyFunctionField

    field = PropertyFunctionField("[temperature]")
    schema: dict[str, str] = field.__get_pydantic_json_schema__(core=None, handler=None)
    assert schema["type"] == "string"
    assert schema["x-unit-dimensionality"] == "[temperature]"


# --- Calibration cubic (Fritsch-Carlson monotone PCHIP) ---------------------


def _cubic_cal(xs: list[float], ys: list[float], extrapolate: bool = False) -> Calibration:
    return Calibration(
        param_dims={"ratio": ""},
        points=[CalibrationPoint(inputs={"ratio": x * U.dimensionless}, output=y * U.dimensionless) for x, y in zip(xs, ys)],
        method="cubic",
        extrapolate=extrapolate,
    )


def _eval_ratio(cal: Calibration, x: float) -> float:
    return cal(ratio=x * U.dimensionless).to(U.dimensionless).magnitude


def test_cubic_interpolates_exactly_at_every_node() -> None:
    xs, ys = [0.0, 1.0, 2.0, 4.0], [0.0, 1.0, 4.0, 5.0]
    cal = _cubic_cal(xs, ys)
    for x, y in zip(xs, ys):
        assert _eval_ratio(cal, x) == pytest.approx(y)


def test_cubic_end_to_end_with_units() -> None:
    # A cubic thermal-conductivity table called with pint quantities, including a Celsius-expressed input.
    unit = U.W / (U.m * U.K)
    cal = Calibration(
        param_dims={"temperature": "[temperature]"},
        points=[
            CalibrationPoint(inputs={"temperature": 300 * U.K}, output=1.0 * unit),
            CalibrationPoint(inputs={"temperature": 350 * U.K}, output=2.0 * unit),
            CalibrationPoint(inputs={"temperature": 400 * U.K}, output=4.0 * unit),
            CalibrationPoint(inputs={"temperature": 450 * U.K}, output=8.0 * unit),
        ],
        method="cubic",
    )
    assert cal(temperature=350 * U.K).to(unit).magnitude == pytest.approx(2.0)
    assert cal(temperature=76.85 * U.degC).to(unit).magnitude == pytest.approx(2.0, rel=1e-3)
    mid = cal(temperature=375 * U.K).to(unit).magnitude
    assert 2.0 < mid < 4.0


def test_cubic_monotone_table_gives_monotone_evaluation() -> None:
    # A steep step in otherwise-flat monotone data: a natural spline overshoots here; PCHIP must not.
    cal = _cubic_cal([0.0, 1.0, 2.0, 3.0], [0.0, 0.05, 0.95, 1.0])
    values = [_eval_ratio(cal, i * 3.0 / 300) for i in range(301)]
    assert all(b >= a - 1e-12 for a, b in zip(values, values[1:]))
    assert min(values) >= -1e-12 and max(values) <= 1.0 + 1e-12


def test_cubic_extremum_node_gets_zero_derivative_no_overshoot() -> None:
    # The secants change sign at the middle node, so its derivative is 0 and the peak value is never exceeded.
    cal = _cubic_cal([0.0, 1.0, 2.0], [0.0, 1.0, 0.0])
    for x in (0.9, 0.99, 1.01, 1.1):
        assert _eval_ratio(cal, x) <= 1.0 + 1e-12
    values = [_eval_ratio(cal, i * 2.0 / 200) for i in range(201)]
    assert max(values) <= 1.0 + 1e-12


def test_cubic_differs_from_linear_and_matches_fritsch_carlson_value() -> None:
    xs, ys = [0.0, 1.0, 2.0, 3.0], [0.0, 1.0, 4.0, 9.0]
    cubic = _eval_ratio(_cubic_cal(xs, ys), 1.5)
    linear = 2.5  # midpoint of the [1, 4] chord
    # Hand-derived: m1 = 1.5, m2 = 3.75 (weighted harmonic means), Hermite at t = 0.5.
    assert cubic == pytest.approx(2.21875)
    assert cubic != pytest.approx(linear)


def test_cubic_out_of_range_raises_without_extrapolate() -> None:
    cal = _cubic_cal([0.0, 1.0, 2.0], [0.0, 1.0, 4.0])
    with pytest.raises(PropertyRangeError):
        _ = _eval_ratio(cal, -0.5)
    with pytest.raises(PropertyRangeError):
        _ = _eval_ratio(cal, 2.5)


def test_cubic_extrapolate_clamps_to_endpoint_values() -> None:
    cal = _cubic_cal([0.0, 1.0, 2.0], [0.0, 1.0, 4.0], extrapolate=True)
    assert _eval_ratio(cal, -0.5) == pytest.approx(0.0)
    assert _eval_ratio(cal, 2.5) == pytest.approx(4.0)


def test_cubic_two_points_degenerates_to_the_straight_line() -> None:
    cal = _cubic_cal([0.0, 1.0], [1.0, 3.0])
    assert _eval_ratio(cal, 0.5) == pytest.approx(2.0)
    assert _eval_ratio(cal, 0.25) == pytest.approx(1.5)


def test_cubic_fewer_than_two_points_raises_value_error() -> None:
    cal = _cubic_cal([1.0], [2.0])
    with pytest.raises(ValueError, match="cubic"):
        _ = _eval_ratio(cal, 1.0)


def test_cubic_edge_slope_sign_mismatch_clamps_to_zero() -> None:
    # The three-point endpoint formula gives a negative slope for this increasing data; the clamp zeroes it.
    cal = _cubic_cal([0.0, 1.0, 2.0], [0.0, 1.0, 5.0])
    values = [_eval_ratio(cal, i * 0.5 / 50) for i in range(51)]
    assert min(values) >= -1e-12


def test_cubic_edge_slope_overshoot_clamps_to_three_secants() -> None:
    # The endpoint formula overshoots (|m| > 3|d0|) across a sign change of the secants; the clamp caps it at 3*d0.
    cal = _cubic_cal([0.0, 1.0, 2.0], [0.0, 0.1, -9.9])
    values = [_eval_ratio(cal, i * 1.0 / 100) for i in range(101)]
    assert min(values) >= -1e-9 and max(values) <= 0.1 + 1e-9


# --- JSON-mode serialization round trips ------------------------------------


def test_calibration_json_round_trip_preserves_evaluation_and_elmer_output() -> None:
    from femforge.materials.properties import ThermalProperties

    unit = U.W / (U.m * U.K)
    cal = Calibration(
        param_dims={"temperature": "[temperature]"},
        points=[
            CalibrationPoint(inputs={"temperature": 300 * U.K}, output=1.0 * unit),
            CalibrationPoint(inputs={"temperature": 350 * U.K}, output=2.0 * unit),
            CalibrationPoint(inputs={"temperature": 400 * U.K}, output=4.0 * unit),
        ],
        method="cubic",
    )
    thermal = ThermalProperties(conductivity=cal)
    dump: dict[str, object] = thermal.model_dump(mode="json")
    text: str = json.dumps(dump, sort_keys=True)  # would raise PydanticSerializationError-era TypeError if a Quantity leaked
    restored = ThermalProperties.model_validate(json.loads(text))
    revived = restored.conductivity
    assert isinstance(revived, Calibration)
    assert revived.method == "cubic"
    at = {"temperature": 325 * U.K}
    assert revived(temperature=325 * U.K) == cal(temperature=325 * U.K)
    assert restored.to_elmer(at=at) == thermal.to_elmer(at=at)


def test_expression_json_round_trip_still_works() -> None:
    from femforge.materials.properties import ThermalProperties

    expr = Expression(param_dims={"temperature": "[temperature]"}, result_units="W/(m*K)", body=Param(name="temperature") * 0.01)
    thermal = ThermalProperties(conductivity=expr)
    dump: dict[str, object] = thermal.model_dump(mode="json")
    restored = ThermalProperties.model_validate(json.loads(json.dumps(dump, sort_keys=True)))
    revived = restored.conductivity
    assert isinstance(revived, Expression)
    assert revived(temperature=300 * U.K) == expr(temperature=300 * U.K)


def test_static_json_round_trip_unchanged() -> None:
    from femforge.materials.properties import ThermalProperties

    thermal = ThermalProperties(conductivity=Static(value=8.7 * U.W / (U.m * U.K)))
    dump: dict[str, object] = thermal.model_dump(mode="json")
    restored = ThermalProperties.model_validate(json.loads(json.dumps(dump)))
    revived = restored.conductivity
    assert isinstance(revived, Static)
    assert revived().to(U.W / (U.m * U.K)).magnitude == pytest.approx(8.7)


def test_calibration_json_dump_is_deterministic_across_constructions() -> None:
    # cache_key hashes json.dumps(..., sort_keys=True): two equal tables must serialize byte-identically.
    first = json.dumps(_two_point_line().model_dump(mode="json"), sort_keys=True)
    second = json.dumps(_two_point_line().model_dump(mode="json"), sort_keys=True)
    assert first == second


def test_a_calibration_dumps_base_units_under_the_canonical_context() -> None:
    from femforge.materials.properties import ThermalProperties

    in_celsius = Calibration(
        param_dims={"temperature": "[temperature]"},
        points=[
            CalibrationPoint(inputs={"temperature": quantity(26.85, "degC")}, output=1000 * U.mW / (U.m * U.K)),
            CalibrationPoint(inputs={"temperature": quantity(126.85, "degC")}, output=6000 * U.mW / (U.m * U.K)),
        ],
    )
    context: dict[str, object] = {CANONICAL_UNITS: True}
    kelvin = ThermalProperties(conductivity=_two_point_line())
    celsius = ThermalProperties(conductivity=in_celsius)

    assert kelvin.model_dump(mode="json", context=context) == celsius.model_dump(mode="json", context=context)
    assert kelvin.model_dump(mode="json") != celsius.model_dump(mode="json")


def test_a_static_property_dumps_base_units_under_the_canonical_context() -> None:
    from femforge.materials.properties import ThermalProperties

    thermal = ThermalProperties(conductivity=Static(value=8700 * U.mW / (U.m * U.K)))

    assert thermal.model_dump(mode="json")["conductivity"] == "8700.0 mW / K / m"
    assert thermal.model_dump(mode="json", context={CANONICAL_UNITS: True})["conductivity"] == "8.7 kg * m / K / s ** 3"
