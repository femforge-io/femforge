"""Core domain types (`physical.domain`): `EntityTag` carrying a `list[Condition]`, and `DomainConfig` bundling materials + region tags."""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import ValidationError

from femforge.common.tags import TagKey
from femforge.common.vector import Direction
from femforge.materials.properties import MaterialProperties
from femforge.physical.conditions import ConditionUnion, FixedTemperature, Magnetization
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import U


def test_entitytag_holds_conditions() -> None:
    t: EntityTag = EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 1, 0))])
    assert len(t.conditions) == 1
    mag: ConditionUnion = t.conditions[0]
    assert isinstance(mag, Magnetization)
    assert mag.direction == Direction(0, 1, 0)
    # A tag carries no flat scalar override fields.
    assert not hasattr(t, "magnetic_coercivity")
    assert not hasattr(t, "magnetization_direction")
    assert not hasattr(t, "fixed_temperature")


def test_condition_values_are_unit_validated() -> None:
    t: EntityTag = EntityTag(selector={"Region": "hot"}, conditions=[FixedTemperature(value=350 * U.K)])
    ft: ConditionUnion = t.conditions[0]
    assert isinstance(ft, FixedTemperature)
    assert ft.value.check("[temperature]")
    with pytest.raises(ValidationError):
        # A mass where a temperature is required fails validation; this exercises the runtime pint dimensionality check.
        EntityTag(conditions=[FixedTemperature(value="5 kg")])  # pyright: ignore[reportArgumentType]  # exercising runtime validation


def test_entitytag_round_trip() -> None:
    conditions: list[ConditionUnion] = [Magnetization(direction=Direction(1, 0, 0)), FixedTemperature(value=300 * U.K)]
    t: EntityTag = EntityTag(selector={TagKey.MAGNETIZATION: "E"}, conditions=conditions)
    restored: EntityTag = EntityTag.model_validate(t.model_dump())
    assert [type(c) for c in restored.conditions] == [Magnetization, FixedTemperature]
    mag: ConditionUnion = restored.conditions[0]
    ft: ConditionUnion = restored.conditions[1]
    assert isinstance(mag, Magnetization) and isinstance(ft, FixedTemperature)
    assert mag.direction == Direction(1, 0, 0)
    assert ft.value.to(U.K).magnitude == pytest.approx(300)


def _dig(node: object, *keys: str) -> object:
    """Walk a JSON-schema dict by key, asserting each level is a dict."""
    for key in keys:
        assert isinstance(node, dict), f"expected a dict at {key!r}, got {type(node).__name__}"
        node = cast("dict[str, object]", node)[key]
    return node


def test_entitytag_schema_exports() -> None:
    schema: dict[str, object] = EntityTag.model_json_schema()
    # The polymorphic conditions list exports as a discriminated union.
    assert _dig(schema, "properties", "conditions", "type") == "array"
    assert _dig(schema, "properties", "conditions", "items", "discriminator", "propertyName") == "kind"
    # The Magnetization direction is a typed 3-vector or null in the $defs.
    direction = cast("dict[str, list[dict[str, object]]]", _dig(schema, "$defs", "Magnetization", "properties", "direction"))
    vector, unset = direction["anyOf"]
    assert vector["type"] == "object" and unset == {"type": "null"}
    assert set(cast("list[str]", vector["required"])) == {"x", "y", "z"}


def test_domain_config_defaults_and_population() -> None:
    assert DomainConfig().materials == [] and DomainConfig().tags == []
    d: DomainConfig = DomainConfig(materials=[MaterialProperties(name="N52")], tags=[EntityTag(selector={"Region": "gap"}, mesh_size=0.1)])
    assert [m.name for m in d.materials] == ["N52"]
    assert d.tags[0].mesh_size == 0.1


def test_entity_tag_name_is_the_part_name_for_a_lone_name_selector() -> None:
    assert EntityTag(selector={TagKey.NAME: "Bearing"}).name == "BEARING"
    assert EntityTag(selector={TagKey.NAME: "Bearing", TagKey.PHASE: "1"}).name == "NameBearing_Phase1"
    assert EntityTag(selector={TagKey.MAGNETIZATION: "N"}).name == "MagnetizationN"
    assert EntityTag().name == "Any"
