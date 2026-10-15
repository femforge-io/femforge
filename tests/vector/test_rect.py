from femforge.common.vector import Rect, Vec2


def test_rect_center():
    r = Rect(Vec2(0, 0), Vec2(4, 6))
    assert r.center.x == 2 and r.center.y == 3


def test_rect_center_non_zero_origin():
    r = Rect(Vec2(2, 4), Vec2(6, 10))
    assert r.center.x == 4 and r.center.y == 7


def test_rect_size():
    r = Rect(Vec2(1, 1), Vec2(5, 4))
    assert r.size.x == 4 and r.size.y == 3


def test_rect_size_zero():
    r = Rect(Vec2(3, 3), Vec2(3, 3))
    assert r.size.x == 0 and r.size.y == 0


def test_rect_center_setter_moves_bounds():
    r = Rect(Vec2(0, 0), Vec2(4, 4))
    r.center = Vec2(3, 3)
    assert abs(r.min.x - 1) < 1e-9 and abs(r.min.y - 1) < 1e-9
    assert abs(r.max.x - 5) < 1e-9 and abs(r.max.y - 5) < 1e-9


def test_rect_center_setter_preserves_size():
    r = Rect(Vec2(0, 0), Vec2(6, 2))
    original_size = r.size
    r.center = Vec2(10, 10)
    assert abs(r.size.x - original_size.x) < 1e-9
    assert abs(r.size.y - original_size.y) < 1e-9


def test_rect_size_setter_resizes_around_center():
    r = Rect(Vec2(0, 0), Vec2(4, 4))
    # center is (2, 2)
    r.size = Vec2(2, 2)
    assert abs(r.min.x - 1) < 1e-9 and abs(r.min.y - 1) < 1e-9
    assert abs(r.max.x - 3) < 1e-9 and abs(r.max.y - 3) < 1e-9


def test_rect_size_setter_preserves_center():
    r = Rect(Vec2(0, 0), Vec2(4, 4))
    original_center = r.center
    r.size = Vec2(8, 6)
    assert abs(r.center.x - original_center.x) < 1e-9
    assert abs(r.center.y - original_center.y) < 1e-9
