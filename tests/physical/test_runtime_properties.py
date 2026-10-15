"""The Expression property-function kind and runtime (variable-dependent) material emission."""

import pytest
from pydantic import ValidationError

from femforge.materials.copper import Copper
from femforge.materials.properties import ElectricalProperties, MaterialProperties, ThermalProperties, _runtime_value
from femforge.physical.expressions import Const, Param
from femforge.physical.property_functions import Calibration, ClosedForm, Expression, PropertyDimensionError, Static
from femforge.physical.units import DIM_PRESSURE, DIM_TEMPERATURE, Quantity, U


AT: dict[str, Quantity] = {"temperature": 300 * U.K}
RUNTIME_T = frozenset({"temperature"})

T = Param(name="temperature")


def _sigma_expression(result_units: str = "S/m", scale: float = 1.0) -> Expression:
    return Expression(
        param_dims={"temperature": DIM_TEMPERATURE},
        result_units=result_units,
        body=(5.96e7 * scale) / (1.0 + 3.93e-3 * (T - 293.15)),
    )


# --- the Expression property-function kind ----------------------------------


def test_expression_evaluates_with_pint_and_si_base_binding() -> None:
    sigma = _sigma_expression()
    # 20 degC binds as 293.15 K (SI base), matching what Elmer's tx supplies.
    assert float(sigma(temperature=(20 * U.degC).to(U.K)).to(U.S / U.m).magnitude) == pytest.approx(5.96e7)
    assert sigma.result_dimensionality == str((1 * U.S / U.m).dimensionality)
    assert dict(sigma.parameters) == {"temperature": DIM_TEMPERATURE}


def test_expression_rejects_undeclared_body_parameters() -> None:
    with pytest.raises(ValidationError, match="undeclared"):
        _ = Expression(param_dims={"temperature": DIM_TEMPERATURE}, result_units="S/m", body=Param(name="pressure") * 2.0)


def test_expression_wrong_parameter_dimensionality_raises() -> None:
    with pytest.raises(PropertyDimensionError):
        _ = _sigma_expression()(temperature=5 * U.kg)


def test_expression_field_assignment_validates_dimensionality() -> None:
    electrical = ElectricalProperties()
    electrical.conductivity = _sigma_expression()
    with pytest.raises(ValidationError):
        electrical.conductivity = Expression(param_dims={}, result_units="kg", body=Const(value=1.0))


def test_copper_round_trips_through_json_with_its_sigma_law() -> None:
    copper = Copper()
    restored = MaterialProperties.model_validate(copper.model_dump())
    assert isinstance(restored.electrical.conductivity, Expression)
    # The revived law emits identically (both the static evaluation and the runtime MATC form).
    assert restored.to_elmer(at=AT) == copper.to_elmer(at=AT)
    assert restored.to_elmer(at=AT, runtime=RUNTIME_T) == copper.to_elmer(at=AT, runtime=RUNTIME_T)


# --- runtime emission --------------------------------------------------------


def test_runtime_emission_folds_unit_conversion_factor() -> None:
    # Authored in MS/m: the magnitude must be scaled by 1e6 to land in the sif's S/m.
    electrical = ElectricalProperties()
    electrical.conductivity = Expression(param_dims={"temperature": DIM_TEMPERATURE}, result_units="MS/m", body=Const(value=59.6) / (1.0 + 3.93e-3 * (T - 293.15)))
    emitted = electrical.to_elmer(at=AT, runtime=RUNTIME_T)["Electric Conductivity"]
    assert isinstance(emitted, str)
    assert "1000000.0" in emitted  # the folded MS/m -> S/m factor


def test_runtime_calibration_emits_interpolation_table() -> None:
    thermal = ThermalProperties()
    thermal.conductivity = Calibration(
        param_dims={"temperature": DIM_TEMPERATURE},
        points=[
            {"inputs": {"temperature": 100 * U.degC}, "output": 390 * U.W / (U.m * U.K)},  # pyright: ignore[reportArgumentType]  # dict form is coerced by the model validator
            {"inputs": {"temperature": 20 * U.degC}, "output": 401 * U.W / (U.m * U.K)},
        ],
    )
    emitted = thermal.to_elmer(at=AT, runtime=RUNTIME_T)["Heat Conductivity"]
    assert isinstance(emitted, str)
    assert emitted.startswith("Variable Temperature")
    assert "\n    Real\n" in emitted and emitted.endswith("End")
    # Rows are sorted by the SI-base (kelvin) input; outputs stay in the sif unit.
    assert emitted.index("293.15 401") < emitted.index("373.15 390")


def test_runtime_closed_form_is_rejected_with_pointer_to_expression() -> None:
    electrical = ElectricalProperties()
    electrical.conductivity = ClosedForm(
        param_dims={"temperature": DIM_TEMPERATURE},
        result_dim=str((1 * U.S / U.m).dimensionality),
        expression=lambda temperature: 5.96e7 * U.S / U.m,  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]  # test-only opaque callable
    )
    with pytest.raises(ValueError, match="Expression"):
        _ = electrical.to_elmer(at=AT, runtime=RUNTIME_T)


def test_runtime_scaled_property_is_rejected() -> None:
    # Permittivity carries a post-scale (eps -> eps_r); an arbitrary scale callable cannot fold into a runtime expression.
    electrical = ElectricalProperties()
    electrical.permittivity = Expression(param_dims={"temperature": DIM_TEMPERATURE}, result_units="F/m", body=Const(value=8.854e-12) + 0.0 * T)
    with pytest.raises(ValueError, match="post-scale"):
        _ = electrical.to_elmer(at=AT, runtime=RUNTIME_T)


def test_mixed_runtime_and_operating_point_parameters_are_rejected() -> None:
    electrical = ElectricalProperties()
    electrical.conductivity = Expression(
        param_dims={"temperature": DIM_TEMPERATURE, "pressure": DIM_PRESSURE},
        result_units="S/m",
        body=Param(name="temperature") * 0.0 + Param(name="pressure") * 0.0 + 5.96e7,
    )
    with pytest.raises(ValueError, match="partial runtime binding"):
        _ = electrical.to_elmer(at=AT, runtime=RUNTIME_T)


def test_unknown_field_variable_is_rejected() -> None:
    electrical = ElectricalProperties()
    electrical.conductivity = Expression(param_dims={"pressure": DIM_PRESSURE}, result_units="S/m", body=Param(name="pressure") * 0.0 + 5.96e7)
    with pytest.raises(ValueError, match="field variable"):
        _ = electrical.to_elmer(at=AT, runtime=frozenset({"pressure"}))


def test_runtime_value_rejects_kinds_without_a_runtime_form() -> None:
    # Defensive tail of _runtime_value: a kind that is neither Expression/Calibration/ClosedForm.
    from femforge.materials.properties import ElmerMaterialKeyword, ElmerProperty

    spec = ElmerProperty(ElmerMaterialKeyword.ELECTRIC_CONDUCTIVITY, U.S / U.m)
    with pytest.raises(ValueError, match="does not support runtime emission"):
        _ = _runtime_value(Static(value=5.96e7 * U.S / U.m), spec, "matc")
