from femforge.common.vector import Vec2


def test_vec2_rotation():
    v = Vec2(1, 0)
    rotated_90 = v.rotate(90, Vec2(0, 0))
    assert abs(rotated_90.x) < 1e-6 and abs(rotated_90.y - 1) < 1e-6

    rotated_180 = v.rotate(180, Vec2(0, 0))
    assert abs(rotated_180.x + 1) < 1e-6 and abs(rotated_180.y) < 1e-6

    rotated_270 = v.rotate(270, Vec2(0, 0))
    assert abs(rotated_270.x) < 1e-6 and abs(rotated_270.y + 1) < 1e-6
