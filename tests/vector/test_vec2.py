from femforge.common.vector import Vec2


def test_vec2_rotation():
    v = Vec2(1, 0)
    rotated_90 = v.rotate(90, Vec2(0, 0))
    assert abs(rotated_90.x) < 1e-6 and abs(rotated_90.y - 1) < 1e-6

    rotated_180 = v.rotate(180, Vec2(0, 0))
    assert abs(rotated_180.x + 1) < 1e-6 and abs(rotated_180.y) < 1e-6

    rotated_270 = v.rotate(270, Vec2(0, 0))
    assert abs(rotated_270.x) < 1e-6 and abs(rotated_270.y + 1) < 1e-6


def test_vec2_rotation_around_point():
    v = Vec2(2, 0)
    center = Vec2(1, 0)
    rotated_90 = v.rotate(90, center)
    assert abs(rotated_90.x - 1) < 1e-6 and abs(rotated_90.y - 1) < 1e-6

    rotated_180 = v.rotate(180, center)
    assert abs(rotated_180.x - 0) < 1e-6 and abs(rotated_180.y - 0) < 1e-6

    rotated_270 = v.rotate(270, center)
    assert abs(rotated_270.x - 1) < 1e-6 and abs(rotated_270.y + 1) < 1e-6


def test_vec2_rotation_non_origin():
    v = Vec2(2, 1)
    center = Vec2(1, 0)
    rotated_90 = v.rotate(90, center)
    assert abs(rotated_90.x - 0) < 1e-6 and abs(rotated_90.y - 1) < 1e-6

    rotated_180 = v.rotate(180, center)
    assert abs(rotated_180.x - 0) < 1e-6 and abs(rotated_180.y + 1) < 1e-6

    rotated_270 = v.rotate(270, center)
    assert abs(rotated_270.x - 2) < 1e-6 and abs(rotated_270.y + 1) < 1e-6


def test_vec2_rotation_full_circle():
    v = Vec2(1, 0)
    rotated_360 = v.rotate(360, Vec2(0, 0))
    assert abs(rotated_360.x - 1) < 1e-6 and abs(rotated_360.y) < 1e-6


def test_vec2_rotation_negative_angle():
    v = Vec2(1, 0)
    rotated_neg_90 = v.rotate(-90, Vec2(0, 0))
    assert abs(rotated_neg_90.x) < 1e-6 and abs(rotated_neg_90.y + 1) < 1e-6


def test_vec2_add():
    result = Vec2(1, 2) + Vec2(3, 4)
    assert result.x == 4 and result.y == 6


def test_vec2_sub():
    result = Vec2(5, 3) - Vec2(2, 1)
    assert result.x == 3 and result.y == 2


def test_vec2_mul():
    result = Vec2(2, 3) * 4
    assert result.x == 8 and result.y == 12


def test_vec2_truediv():
    result = Vec2(6, 4) / 2
    assert result.x == 3 and result.y == 2


def test_vec2_magnitude():
    assert abs(Vec2(3, 4).magnitude() - 5.0) < 1e-9


def test_vec2_magnitude_zero():
    assert Vec2(0, 0).magnitude() == 0.0


def test_vec2_to_list():
    assert Vec2(1.5, -2.0).to_list() == [1.5, -2.0]


def test_vec2_to_tuple():
    assert Vec2(1.5, -2.0).to_tuple() == (1.5, -2.0)
