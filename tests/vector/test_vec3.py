import itertools
from dataclasses import FrozenInstanceError

import pytest

from femforge.common.vector import Direction, Vec3


def test_vec3_add():
    result = Vec3(1, 2, 3) + Vec3(4, 5, 6)
    assert result.x == 5 and result.y == 7 and result.z == 9


def test_vec3_sub():
    result = Vec3(5, 7, 9) - Vec3(1, 2, 3)
    assert result.x == 4 and result.y == 5 and result.z == 6


def test_vec3_mul():
    result = Vec3(1, 2, 3) * 3
    assert result.x == 3 and result.y == 6 and result.z == 9


def test_vec3_truediv():
    result = Vec3(6, 9, 12) / 3
    assert result.x == 2 and result.y == 3 and result.z == 4


def test_vec3_magnitude():
    # 1^2 + 2^2 + 2^2 = 9, sqrt = 3
    assert abs(Vec3(1, 2, 2).magnitude() - 3.0) < 1e-9


def test_vec3_magnitude_unit():
    assert abs(Vec3(1, 0, 0).magnitude() - 1.0) < 1e-9


def test_vec3_magnitude_zero():
    assert Vec3(0, 0, 0).magnitude() == 0.0


def test_vec3_magnitude_3_4_0():
    assert abs(Vec3(3, 4, 0).magnitude() - 5.0) < 1e-9


def test_vec3_to_list():
    assert Vec3(1.5, -2.0, 3.25).to_list() == [1.5, -2.0, 3.25]


def test_vec3_equality_and_hash_are_exact() -> None:
    assert Vec3(1, 2, 3) == Vec3(1.0, 2.0, 3.0)
    assert hash(Vec3(1, 2, 3)) == hash(Vec3(1.0, 2.0, 3.0))
    assert Vec3(1, 2, 3) != Vec3(1 + 1e-12, 2, 3)
    assert Vec3(1, 1, 0) != Vec3(7, 7, 0)
    assert Vec3(1, 2, 3) != [1, 2, 3]
    assert len({Vec3(0, 0, 1), Vec3(0, 0, 1.0)}) == 1


def test_vec3_isclose_compares_within_a_tolerance() -> None:
    assert Vec3(1, 2, 3).isclose(Vec3(1 + 1e-12, 2, 3))
    assert Vec3(0, 0, 1e-12).isclose(Vec3(0, 0, 0))
    assert not Vec3(1, 2, 3).isclose(Vec3(1.001, 2, 3))
    assert Vec3(1, 2, 3).isclose(Vec3(1.001, 2, 3), tolerance=1e-2)


def test_vec3_is_immutable() -> None:
    vector = Vec3(1, 2, 3)

    with pytest.raises(FrozenInstanceError):
        vector.x = 5  # pyright: ignore[reportAttributeAccessIssue]  # exercising the frozen guard


def test_direction_is_normalized_and_scaled_copies_compare_and_hash_equal() -> None:
    assert Direction(0, 0, 2) == Direction(0, 0, 1) == Vec3(0, 0, 1)
    assert Direction(1, 1, 0) == Direction(7, 7, 0)
    assert hash(Direction(1, 1, 0)) == hash(Direction(7, 7, 0))
    assert Direction(3, 4, 0).to_list() == [0.6, 0.8, 0.0]
    assert Direction(-0.0, 0, -1).to_list() == [0.0, 0.0, -1.0]


def test_direction_survives_a_rebuild_from_its_components() -> None:
    for scale in (1, 3, 7, 0.1, 25.4, 1e3):
        for components in itertools.product(range(-3, 4), repeat=3):
            if components == (0, 0, 0):
                continue

            direction = Direction(*(component * scale for component in components))

            assert Direction(*direction.to_tuple()) == direction
            assert direction == Direction(*components)


def test_a_zero_direction_raises() -> None:
    with pytest.raises(ValueError, match="non-zero vector"):
        _ = Direction(0, 0, 0)


def test_arithmetic_on_a_direction_is_a_plain_vec3() -> None:
    scaled = Direction(0, 0, 1) * 5

    assert type(scaled) is Vec3
    assert scaled == Vec3(0, 0, 5)
