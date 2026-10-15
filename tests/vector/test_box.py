from femforge.common.vector import Box, Vec3


def test_box_center():
    b = Box(Vec3(0, 0, 0), Vec3(4, 6, 2))
    assert b.center.x == 2 and b.center.y == 3 and b.center.z == 1


def test_box_center_non_zero_origin():
    b = Box(Vec3(2, 4, 6), Vec3(6, 10, 12))
    assert b.center.x == 4 and b.center.y == 7 and b.center.z == 9


def test_box_size():
    b = Box(Vec3(1, 2, 3), Vec3(5, 6, 7))
    assert b.size.x == 4 and b.size.y == 4 and b.size.z == 4


def test_box_size_zero():
    b = Box(Vec3(1, 2, 3), Vec3(1, 2, 3))
    assert b.size.x == 0 and b.size.y == 0 and b.size.z == 0


def test_box_renders_its_bounds():
    b = Box(Vec3(0, 0, 0), Vec3(1.23456, 2, 3))
    assert str(b) == f"Box(min={b.min}, max={b.max})"
    assert repr(b) == str(b)
    # __format__ forwards the spec to both corners; __rich__ delegates to the Vec3 markup.
    assert f"{b:.3f}" == f"Box(min={b.min:.3f}, max={b.max:.3f})"
    assert b.__rich__() == f"Box(min={b.min.__rich__()}, max={b.max.__rich__()})"


def test_box_center_setter_moves_bounds():
    b = Box(Vec3(0, 0, 0), Vec3(4, 4, 4))
    b.center = Vec3(3, 3, 3)
    assert abs(b.min.x - 1) < 1e-9 and abs(b.min.y - 1) < 1e-9 and abs(b.min.z - 1) < 1e-9
    assert abs(b.max.x - 5) < 1e-9 and abs(b.max.y - 5) < 1e-9 and abs(b.max.z - 5) < 1e-9


def test_box_center_setter_preserves_size():
    b = Box(Vec3(0, 0, 0), Vec3(6, 4, 2))
    original_size = b.size
    b.center = Vec3(10, 10, 10)
    assert abs(b.size.x - original_size.x) < 1e-9
    assert abs(b.size.y - original_size.y) < 1e-9
    assert abs(b.size.z - original_size.z) < 1e-9


def test_box_size_setter_resizes_around_center():
    b = Box(Vec3(0, 0, 0), Vec3(4, 4, 4))
    # center is (2, 2, 2)
    b.size = Vec3(2, 2, 2)
    assert abs(b.min.x - 1) < 1e-9 and abs(b.min.y - 1) < 1e-9 and abs(b.min.z - 1) < 1e-9
    assert abs(b.max.x - 3) < 1e-9 and abs(b.max.y - 3) < 1e-9 and abs(b.max.z - 3) < 1e-9


def test_box_size_setter_preserves_center():
    b = Box(Vec3(0, 0, 0), Vec3(4, 4, 4))
    original_center = b.center
    b.size = Vec3(8, 6, 2)
    assert abs(b.center.x - original_center.x) < 1e-9
    assert abs(b.center.y - original_center.y) < 1e-9
    assert abs(b.center.z - original_center.z) < 1e-9
