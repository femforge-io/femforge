from femforge.common.vector import CoordinateFrame, Quaternion, Vec3


def test_transform_position_identity_frame():
    frame = CoordinateFrame(Vec3(0, 0, 0), Quaternion(1, 0, 0, 0))
    result = frame.transform_position(Vec3(1, 2, 3))
    assert abs(result.x - 1) < 1e-9
    assert abs(result.y - 2) < 1e-9
    assert abs(result.z - 3) < 1e-9


def test_transform_position_with_translation():
    frame = CoordinateFrame(Vec3(1, 2, 3), Quaternion(1, 0, 0, 0))
    result = frame.transform_position(Vec3(1, 0, 0))
    assert abs(result.x - 2) < 1e-9
    assert abs(result.y - 2) < 1e-9
    assert abs(result.z - 3) < 1e-9


def test_transform_position_with_rotation():
    # 90° around Z: local (1,0,0) → global (0,1,0)
    q = Quaternion.from_axis_angle(Vec3(0, 0, 1), 90)
    frame = CoordinateFrame(Vec3(0, 0, 0), q)
    result = frame.transform_position(Vec3(1, 0, 0))
    assert abs(result.x) < 1e-6
    assert abs(result.y - 1) < 1e-6
    assert abs(result.z) < 1e-6


def test_transform_position_translation_and_rotation():
    q = Quaternion.from_axis_angle(Vec3(0, 0, 1), 90)
    frame = CoordinateFrame(Vec3(1, 0, 0), q)
    # local (1,0,0) rotated by 90° → (0,1,0), then translated by (1,0,0) → (1,1,0)
    result = frame.transform_position(Vec3(1, 0, 0))
    assert abs(result.x - 1) < 1e-6
    assert abs(result.y - 1) < 1e-6
    assert abs(result.z) < 1e-6


def test_inverse_transform_position_identity():
    frame = CoordinateFrame(Vec3(1, 2, 3), Quaternion(1, 0, 0, 0))
    result = frame.inverse_transform_position(Vec3(2, 2, 3))
    assert abs(result.x - 1) < 1e-9
    assert abs(result.y - 0) < 1e-9
    assert abs(result.z - 0) < 1e-9


def test_transform_inverse_roundtrip():
    q = Quaternion.from_axis_angle(Vec3(1, 0, 0), 45)
    frame = CoordinateFrame(Vec3(3, 4, 5), q)
    original = Vec3(1, 2, 3)
    recovered = frame.inverse_transform_position(frame.transform_position(original))
    assert abs(recovered.x - original.x) < 1e-6
    assert abs(recovered.y - original.y) < 1e-6
    assert abs(recovered.z - original.z) < 1e-6


def test_transform_direction_ignores_translation():
    q = Quaternion.from_axis_angle(Vec3(0, 0, 1), 90)
    frame = CoordinateFrame(Vec3(99, 99, 99), q)
    result = frame.transform_direction(Vec3(1, 0, 0))
    assert abs(result.x) < 1e-6
    assert abs(result.y - 1) < 1e-6
    assert abs(result.z) < 1e-6


def test_inverse_transform_direction():
    q = Quaternion.from_axis_angle(Vec3(0, 0, 1), 90)
    frame = CoordinateFrame(Vec3(0, 0, 0), q)
    # inverse of 90° Z rotation applied to (0,1,0) → (1,0,0)
    result = frame.inverse_transform_direction(Vec3(0, 1, 0))
    assert abs(result.x - 1) < 1e-6
    assert abs(result.y) < 1e-6
    assert abs(result.z) < 1e-6


def test_transform_direction_roundtrip():
    q = Quaternion.from_axis_angle(Vec3(1, 1, 0), 60)
    frame = CoordinateFrame(Vec3(0, 0, 0), q)
    original = Vec3(1, 0, 0)
    recovered = frame.inverse_transform_direction(frame.transform_direction(original))
    assert abs(recovered.x - original.x) < 1e-6
    assert abs(recovered.y - original.y) < 1e-6
    assert abs(recovered.z - original.z) < 1e-6


def test_mul_identity_frame():
    identity = CoordinateFrame(Vec3(0, 0, 0), Quaternion(1, 0, 0, 0))
    frame = CoordinateFrame(Vec3(1, 2, 3), Quaternion.from_axis_angle(Vec3(0, 1, 0), 30))
    combined = identity * frame
    assert abs(combined.origin.x - 1) < 1e-6
    assert abs(combined.origin.y - 2) < 1e-6
    assert abs(combined.origin.z - 3) < 1e-6


def test_mul_combines_translations():
    identity_q = Quaternion(1, 0, 0, 0)
    a = CoordinateFrame(Vec3(1, 0, 0), identity_q)
    b = CoordinateFrame(Vec3(2, 0, 0), identity_q)
    combined = a * b
    assert abs(combined.origin.x - 3) < 1e-9
    assert abs(combined.origin.y) < 1e-9
    assert abs(combined.origin.z) < 1e-9


def test_imul_updates_in_place():
    identity_q = Quaternion(1, 0, 0, 0)
    a = CoordinateFrame(Vec3(0, 0, 0), identity_q)
    b = CoordinateFrame(Vec3(1, 2, 3), identity_q)
    a *= b
    assert abs(a.origin.x - 1) < 1e-9
    assert abs(a.origin.y - 2) < 1e-9
    assert abs(a.origin.z - 3) < 1e-9


def test_repr():
    frame = CoordinateFrame(Vec3(0, 0, 0), Quaternion(1, 0, 0, 0))
    assert "CoordinateFrame" in repr(frame)


def test_str():
    frame = CoordinateFrame(Vec3(0, 0, 0), Quaternion(1, 0, 0, 0))
    assert "CoordinateFrame" in str(frame)
