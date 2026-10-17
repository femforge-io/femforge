"""The pint <-> Pydantic keystone (`quantity_type`) and its aliases."""

from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from femforge.common.vector import Vec3
from femforge.physical.units import (
    CANONICAL_UNITS,
    DIM_FORCE,
    DIM_TEMPERATURE,
    DIM_THERMAL_CONDUCTIVITY,
    U,
    Conductivity,
    Density,
    Force,
    Quantity,
    Temperature,
    VectorQuantity,
    dimensionality_matches,
    quantity,
    quantity_type,
    vector_quantity_type,
)


class _Sample(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    density: Density
    conductivity: Conductivity
    temperature: Temperature


def _sample(**over: Quantity) -> _Sample:
    base: dict[str, Quantity] = {
        "density": 7500 * U.kg / U.m**3,
        "conductivity": 8.7 * U.W / (U.m * U.K),
        "temperature": 300 * U.K,
    }
    base.update(over)
    return _Sample(**base)


def test_valid_quantity_is_stored_as_pint() -> None:
    s = _sample()
    assert isinstance(s.density, U.Quantity)
    assert s.density.check("[mass]/[length]**3")
    assert pytest.approx(s.conductivity.to(U.W / (U.m * U.K)).magnitude) == 8.7


def test_wrong_dimensionality_raises_at_construction() -> None:
    # A mass given where a density belongs fails at construction, not later.
    with pytest.raises(ValidationError):
        _sample(density=5 * U.kg)
    with pytest.raises(ValidationError):
        _sample(conductivity=5 * U.kg)


def test_scalar_field_rejects_non_quantity_non_scalar() -> None:
    with pytest.raises(ValidationError, match="expected a scalar Quantity"):
        _sample(density=cast(Quantity, None))


def test_a_bare_number_names_the_si_units_to_write() -> None:
    with pytest.raises(ValidationError, match='bare number 300; give it units, as in "300 K"'):
        _sample(temperature=cast(Quantity, 300))


def test_json_schema_includes_unit_annotation() -> None:
    schema: dict[str, object] = _Sample.model_json_schema()
    dens = cast(dict[str, object], schema["properties"])
    density_field = cast(dict[str, object], dens["density"])
    assert density_field["x-unit-dimensionality"] == "[mass]/[length]**3"
    assert density_field["type"] == "string"


def test_round_trip_preserves_quantity() -> None:
    s = _sample()
    restored = _Sample.model_validate(s.model_dump())
    assert restored.density.to_base_units() == s.density.to_base_units()
    assert restored.conductivity.to_base_units() == s.conductivity.to_base_units()


def test_serialized_form_is_ascii_parseable() -> None:
    # The serializer emits something pint can read back (no unicode superscripts, no Rich markup) so model_dump JSON round-trips.
    dumped: dict[str, object] = _sample().model_dump()
    density_str: str = cast(str, dumped["density"])
    reparsed = cast(Quantity, U.Quantity(density_str))
    assert reparsed.check("[mass]/[length]**3")
    assert "³" not in density_str
    assert "[" not in density_str


def test_quantity_type_accepts_already_constructed_quantity() -> None:
    s = _sample(density=7500 * U.kg / U.m**3)
    assert s.density.check("[mass]/[length]**3")


# --- quantity_type / vector_quantity_type factories ------------------------


def test_quantity_type_factory_returns_annotated_metadata() -> None:
    # quantity_type returns Annotated[Quantity, _ScalarQuantity(dim)] carrying the dimensionality in its metadata.
    field_type: object = quantity_type(DIM_TEMPERATURE)
    assert field_type is not None
    metadata = cast(tuple[object, ...], getattr(field_type, "__metadata__"))
    assert getattr(metadata[0], "dimensionality") == DIM_TEMPERATURE


def test_vector_quantity_type_factory_returns_annotated_metadata() -> None:
    field_type: object = vector_quantity_type(DIM_FORCE)
    assert field_type is not None
    metadata = cast(tuple[object, ...], getattr(field_type, "__metadata__"))
    assert getattr(metadata[0], "dimensionality") == DIM_FORCE


# --- the vector-quantity field (_VectorQuantity) via the Force alias --------


class _ForceModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    force: Force


def test_vector_field_validates_and_round_trips() -> None:
    vec_q: VectorQuantity = quantity(Vec3(3.0, 4.0, 0.0), U.N)
    m = _ForceModel(force=vec_q)
    assert m.force.check("[force]")

    # Round-trip through the {x, y, z, units} dict form.
    dumped: dict[str, object] = m.model_dump()
    force_dict = cast(dict[str, object], dumped["force"])
    assert set(force_dict) == {"x", "y", "z", "units"}
    restored = _ForceModel.model_validate({"force": force_dict})
    assert cast(Vec3, restored.force.magnitude).x == pytest.approx(3.0)
    assert cast(Vec3, restored.force.magnitude).y == pytest.approx(4.0)


def test_vector_field_rejects_scalar_magnitude() -> None:
    # A scalar quantity (no Vec3 magnitude) is rejected by _VectorQuantity._validate.
    with pytest.raises(ValidationError):
        _ForceModel(force=cast(VectorQuantity, 5 * U.N))


def test_vector_field_rejects_non_quantity_non_dict() -> None:
    with pytest.raises(ValidationError):
        _ForceModel(force=cast(VectorQuantity, 42))


# --- dimensionality_matches / _dimensionality -----------------------------


def test_dimensionality_matches_equivalent_spellings() -> None:
    # A quantity's .dimensionality str() and the canonical bracket spelling compare equal (parsed, not textual).
    cond_q: Quantity = 8.7 * U.W / (U.m * U.K)
    assert dimensionality_matches(DIM_THERMAL_CONDUCTIVITY, str(cond_q.dimensionality))


def test_dimensionality_matches_distinct_dimensions_are_unequal() -> None:
    assert not dimensionality_matches(DIM_TEMPERATURE, DIM_FORCE)


def test_dimensionality_matches_dimensionless_spellings() -> None:
    # Both "" and "dimensionless" resolve to the no-dimensions value, which pint's get_dimensionality cannot parse directly.
    assert dimensionality_matches("", "dimensionless")
    assert dimensionality_matches("dimensionless", "")
    assert dimensionality_matches("  ", "")  # whitespace-only strips to dimensionless


def test_dimensionality_matches_non_dimensionless_string() -> None:
    # A non-empty spec goes through U.get_dimensionality.
    assert dimensionality_matches("[temperature]", "[temperature]")
    assert not dimensionality_matches("[temperature]", "")


def test_vector_field_json_schema() -> None:
    schema: dict[str, object] = _ForceModel.model_json_schema()
    props = cast(dict[str, object], schema["properties"])
    force_schema = cast(dict[str, object], props["force"])
    assert force_schema["type"] == "object"
    assert force_schema["required"] == ["x", "y", "z", "units"]
    assert force_schema["x-unit-dimensionality"] == DIM_FORCE


def test_scalar_field_dumps_base_units_under_the_canonical_context() -> None:
    sample = _sample(temperature=quantity(26.85, "degC"), density=7.5 * U.g / U.cm**3)
    canonical: dict[str, object] = sample.model_dump(mode="json", context={CANONICAL_UNITS: True})

    assert canonical["temperature"] == "300.0 K"
    assert canonical["density"] == "7500.0 kg / m ** 3"
    assert sample.model_dump(mode="json", context={"other": True})["density"] == "7.5 g / cm ** 3"


def test_vector_field_dumps_base_units_under_the_canonical_context() -> None:
    context: dict[str, object] = {CANONICAL_UNITS: True}
    in_millinewtons = _ForceModel(force=quantity(Vec3(2.5, 0.0, -1.0), "mN"))
    in_newtons = _ForceModel(force=quantity(Vec3(0.0025, 0.0, -0.001), "N"))
    canonical = cast(dict[str, object], in_millinewtons.model_dump(mode="json", context=context)["force"])
    plain = cast(dict[str, object], in_millinewtons.model_dump(mode="json")["force"])

    assert canonical == in_newtons.model_dump(mode="json", context=context)["force"]
    assert canonical["x"] == 0.0025
    assert canonical["units"] == str((1 * U.N).to_base_units().units)
    assert plain["x"] == 2.5
    assert plain["units"] == "mN"
