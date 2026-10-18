"""Vec3.magnitude() and normalized(): the vector ops sim.py relies on."""

import math

import pytest

from femforge.common.vector import Vec3


def test_magnitude() -> None:
    assert Vec3(3.0, 4.0, 0.0).magnitude() == pytest.approx(5.0)
    assert abs(Vec3(0.0, 0.0, 0.0)) == 0.0


def test_normalized_returns_unit_vector() -> None:
    n: Vec3 = Vec3(0.0, 2.0, 0.0).normalized()
    assert (n.x, n.y, n.z) == pytest.approx((0.0, 1.0, 0.0))
    assert n.magnitude() == pytest.approx(1.0)
    diag: Vec3 = Vec3(1.0, 1.0, 1.0).normalized()
    assert diag.magnitude() == pytest.approx(1.0)
    assert diag.x == pytest.approx(1 / math.sqrt(3))


def test_normalized_zero_raises() -> None:
    with pytest.raises(ValueError):
        Vec3(0.0, 0.0, 0.0).normalized()


def test_dot_is_the_scalar_product() -> None:
    assert Vec3(1.0, 2.0, 3.0).dot(Vec3(4.0, -5.0, 6.0)) == pytest.approx(12.0)
    assert Vec3(1.0, 0.0, 0.0).dot(Vec3(0.0, 1.0, 0.0)) == 0.0


def test_cross_is_the_right_handed_vector_product() -> None:
    assert Vec3(1.0, 0.0, 0.0).cross(Vec3(0.0, 1.0, 0.0)) == Vec3(0.0, 0.0, 1.0)
    assert Vec3(0.0, 0.0, 1.0).cross(Vec3(1.0, 0.0, 0.0)) == Vec3(0.0, 1.0, 0.0)
    assert Vec3(1.0, 2.0, 3.0).cross(Vec3(4.0, 5.0, 6.0)) == Vec3(-3.0, 6.0, -3.0)
