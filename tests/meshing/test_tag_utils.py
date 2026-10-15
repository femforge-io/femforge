"""conditions_for resolves which conditions of a given (physics, target) apply to a region's tags."""

from femforge.common.tags import TagKey
from femforge.common.vector import Direction
from femforge.elmer.physics import Physics
from femforge.physical.domain import EntityTag, conditions_for
from femforge.physical.conditions import ConditionTarget, FixedTemperature, Magnetization
from femforge.physical.units import U


def test_conditions_for_filters_by_physics_and_target() -> None:
    tags: list[EntityTag] = [
        EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=Direction(0, 1, 0))]),
        EntityTag(selector={"Region": "Hot"}, conditions=[FixedTemperature(value=350 * U.K)]),
    ]
    body_mag = conditions_for(tags, Physics.MAGNETOSTATICS, ConditionTarget.BODY)
    assert len(body_mag) == 1 and isinstance(body_mag[0], Magnetization)

    bnd_thermal = conditions_for(tags, Physics.THERMAL, ConditionTarget.BOUNDARY)
    assert len(bnd_thermal) == 1 and isinstance(bnd_thermal[0], FixedTemperature)

    # No magnetostatics boundary conditions exist in this set.
    assert conditions_for(tags, Physics.MAGNETOSTATICS, ConditionTarget.BOUNDARY) == []


def test_conditions_for_aggregates_across_tags() -> None:
    # Multiple tags on a region each contributing a matching condition.
    tags = [
        EntityTag(selector={"Region": "a"}, conditions=[Magnetization(direction=Direction(1, 0, 0))]),
        EntityTag(selector={"Region": "b"}, conditions=[Magnetization(direction=Direction(0, 1, 0))]),
    ]
    found = conditions_for(tags, Physics.MAGNETOSTATICS, ConditionTarget.BODY)
    assert len(found) == 2


def test_conditions_for_empty() -> None:
    assert conditions_for([], Physics.MAGNETOSTATICS, ConditionTarget.BODY) == []
    assert conditions_for([EntityTag(selector={"Region": "x"})], Physics.THERMAL, ConditionTarget.BOUNDARY) == []
