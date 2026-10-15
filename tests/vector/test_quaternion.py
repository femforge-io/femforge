import math

import pytest

from femforge.common.vector import Quaternion, Vec3


def test_quaternion_magnitude_identity():
    assert abs(Quaternion(1, 0, 0, 0).magnitude() - 1.0) < 1e-9


def test_quaternion_magnitude():
    # sqrt(1^2 + 1^2 + 1^2 + 1^2) = 2
    assert abs(Quaternion(1, 1, 1, 1).magnitude() - 2.0) < 1e-9


def test_quaternion_conjugate():
    c = Quaternion(1, 2, 3, 4).conjugate()
    assert c.w == 1 and c.x == -2 and c.y == -3 and c.z == -4


def test_quaternion_conjugate_identity():
    c = Quaternion(1, 0, 0, 0).conjugate()
    assert c.w == 1 and c.x == 0 and c.y == 0 and c.z == 0


def test_quaternion_normalized():
    n = Quaternion(0, 3, 0, 0).normalized()
    assert abs(n.x - 1.0) < 1e-9
    assert abs(n.magnitude() - 1.0) < 1e-9


def test_quaternion_normalized_already_unit():
    q = Quaternion(1, 0, 0, 0)
    n = q.normalized()
    assert abs(n.w - 1.0) < 1e-9


def test_quaternion_normalize_zero_raises():
    with pytest.raises(ValueError):
        Quaternion(0, 0, 0, 0).normalized()


def test_quaternion_mul_by_identity():
    identity = Quaternion(1, 0, 0, 0)
    q = Quaternion(0.5, 0.5, 0.5, 0.5)
    result = identity * q
    assert abs(result.w - q.w) < 1e-9
    assert abs(result.x - q.x) < 1e-9
    assert abs(result.y - q.y) < 1e-9
    assert abs(result.z - q.z) < 1e-9


def test_quaternion_mul_conjugate_gives_identity():
    q = Quaternion.from_axis_angle(Vec3(0, 1, 0), 60)
    result = q * q.conjugate()
    assert abs(result.w - 1.0) < 1e-6
    assert abs(result.x) < 1e-6
    assert abs(result.y) < 1e-6
    assert abs(result.z) < 1e-6


def test_quaternion_rotate_vector_identity():
    q = Quaternion(1, 0, 0, 0)
    v = Vec3(1, 2, 3)
    result = q.rotate_vector(v)
    assert abs(result.x - 1) < 1e-9
    assert abs(result.y - 2) < 1e-9
    assert abs(result.z - 3) < 1e-9


def test_quaternion_rotate_vector_90_around_z():
    q = Quaternion.from_axis_angle(Vec3(0, 0, 1), 90)
    result = q.rotate_vector(Vec3(1, 0, 0))
    assert abs(result.x) < 1e-6
    assert abs(result.y - 1) < 1e-6
    assert abs(result.z) < 1e-6


def test_quaternion_rotate_vector_90_around_x():
    q = Quaternion.from_axis_angle(Vec3(1, 0, 0), 90)
    result = q.rotate_vector(Vec3(0, 1, 0))
    assert abs(result.x) < 1e-6
    assert abs(result.y) < 1e-6
    assert abs(result.z - 1) < 1e-6


def test_quaternion_rotate_vector_90_around_y():
    q = Quaternion.from_axis_angle(Vec3(0, 1, 0), 90)
    result = q.rotate_vector(Vec3(0, 0, 1))
    assert abs(result.x - 1) < 1e-6
    assert abs(result.y) < 1e-6
    assert abs(result.z) < 1e-6


def test_quaternion_from_axis_angle_normalized():
    q = Quaternion.from_axis_angle(Vec3(1, 1, 1), 45)
    assert abs(q.magnitude() - 1.0) < 1e-9


def test_quaternion_from_axis_angle_zero_rotation():
    q = Quaternion.from_axis_angle(Vec3(0, 0, 1), 0)
    assert abs(q.w - 1.0) < 1e-9
    assert abs(q.z) < 1e-9


def test_quaternion_to_rotation_matrix_identity():
    m = Quaternion(1, 0, 0, 0).to_rotation_matrix()
    expected = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    for i in range(3):
        for j in range(3):
            assert abs(m[i][j] - expected[i][j]) < 1e-9


def test_quaternion_to_rotation_matrix_90_around_z():
    q = Quaternion.from_axis_angle(Vec3(0, 0, 1), 90)
    m = q.to_rotation_matrix()
    # Rotates (1,0,0) to (0,1,0): first column should be (0,1,0)
    assert abs(m[0][0]) < 1e-6
    assert abs(m[1][0] - 1) < 1e-6
    assert abs(m[2][0]) < 1e-6


def test_quaternion_from_euler_to_euler_roundtrip_yaw():
    q = Quaternion.from_euler_angles(45, 0, 0)
    yaw, pitch, roll = q.to_euler_angles()
    assert abs(yaw - 45) < 1e-6
    assert abs(pitch) < 1e-6
    assert abs(roll) < 1e-6


def test_quaternion_from_euler_to_euler_roundtrip_roll():
    q = Quaternion.from_euler_angles(0, 0, 30)
    yaw, pitch, roll = q.to_euler_angles()
    assert abs(yaw) < 1e-6
    assert abs(pitch) < 1e-6
    assert abs(roll - 30) < 1e-6


def test_quaternion_from_euler_normalized():
    q = Quaternion.from_euler_angles(30, 45, 60)
    assert abs(q.magnitude() - 1.0) < 1e-9


def test_quaternion_slerp_t0():
    q1 = Quaternion(1, 0, 0, 0)
    q2 = Quaternion.from_axis_angle(Vec3(0, 0, 1), 90)
    result = Quaternion.slerp(q1, q2, 0)
    assert abs(result.w - q1.w) < 1e-6
    assert abs(result.x - q1.x) < 1e-6
    assert abs(result.y - q1.y) < 1e-6
    assert abs(result.z - q1.z) < 1e-6


def test_quaternion_slerp_t1():
    q1 = Quaternion(1, 0, 0, 0)
    q2 = Quaternion.from_axis_angle(Vec3(0, 0, 1), 90)
    result = Quaternion.slerp(q1, q2, 1)
    assert abs(result.w - q2.w) < 1e-6
    assert abs(result.x - q2.x) < 1e-6
    assert abs(result.y - q2.y) < 1e-6
    assert abs(result.z - q2.z) < 1e-6


def test_quaternion_slerp_midpoint_rotates_halfway():
    q1 = Quaternion(1, 0, 0, 0)
    q2 = Quaternion.from_axis_angle(Vec3(0, 0, 1), 90)
    mid = Quaternion.slerp(q1, q2, 0.5)
    # Rotating (1,0,0) by 45° around Z gives (cos45°, sin45°, 0)
    result = mid.rotate_vector(Vec3(1, 0, 0))
    assert abs(result.x - math.cos(math.radians(45))) < 1e-6
    assert abs(result.y - math.sin(math.radians(45))) < 1e-6
    assert abs(result.z) < 1e-6


def test_quaternion_slerp_result_normalized():
    q1 = Quaternion.from_axis_angle(Vec3(1, 0, 0), 30)
    q2 = Quaternion.from_axis_angle(Vec3(0, 1, 0), 90)
    result = Quaternion.slerp(q1, q2, 0.5)
    assert abs(result.magnitude() - 1.0) < 1e-9


def test_quaternion_repr():
    r = repr(Quaternion(1.0, 0.0, 0.0, 0.0))
    assert "Quaternion" in r
    assert "w=1.0" in r


def test_quaternion_str():
    s = str(Quaternion(1.0, 0.0, 0.0, 0.0))
    assert "1.000" in s
