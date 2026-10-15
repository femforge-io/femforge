"""Coverage tests for ``common.vector``: dunders, ``__rmul__`` overloads, slerp short-circuits, the gimbal-lock pitch clamp, and the ``Vec3Field`` schema."""

import math
from typing import cast

import pytest
from pydantic import BaseModel

from femforge.common.vector import (
    CoordinateFrame,
    Direction,
    DirectionField,
    Quaternion,
    Vec3,
    Vec3Field,
)
from femforge.common.vector import (
    Vec2 as Vec2,
)


def test_vec2_rotate_default_center() -> None:
    """``rotate`` with no center defaults to the origin."""
    from femforge.common.vector import Vec2

    v: Vec2 = Vec2(1.0, 0.0)
    rotated: Vec2 = v.rotate(90.0)
    assert abs(rotated.x) < 1e-6
    assert abs(rotated.y - 1.0) < 1e-6


def test_vec2_str() -> None:
    """``Vec2.__str__`` formats to 3 decimals."""
    from femforge.common.vector import Vec2

    assert str(Vec2(1.0, 2.0)) == "(1.000, 2.000)"


def test_vec3_rmul() -> None:
    """Scalar-on-the-left multiplication uses ``Vec3.__rmul__``."""
    result: Vec3 = 3.0 * Vec3(1.0, 2.0, 3.0)
    assert result.x == 3.0
    assert result.y == 6.0
    assert result.z == 9.0


def test_vec3_str() -> None:
    """``Vec3.__str__`` defers to ``repr``."""
    assert str(Vec3(1.0, 2.0, 3.0)) == "Vec3(1.0, 2.0, 3.0)"


def test_vec3_format_tilde_suffix() -> None:
    """A trailing ``~`` is stripped from the format spec."""
    formatted: str = format(Vec3(1.0, 2.0, 3.0), ".2f~")
    assert formatted == "(1.00, 2.00, 3.00)"


def test_vec3_format_plain_spec() -> None:
    """A spec without ``~`` takes the else branch."""
    formatted: str = format(Vec3(1.0, 2.0, 3.0), ".1f")
    assert formatted == "(1.0, 2.0, 3.0)"


def test_vec3_rich() -> None:
    """``__rich__`` embeds per-component color markup."""
    markup: str = Vec3(1.0, 2.0, 3.0).__rich__()
    assert "[red]1.000[/red]" in markup
    assert "[green]2.000[/green]" in markup
    assert "[blue]3.000[/blue]" in markup


def test_quaternion_to_euler_gimbal_lock() -> None:
    """A +90 pitch drives ``abs(sinp) >= 1``."""
    q: Quaternion = Quaternion.from_euler_angles(0.0, 90.0, 0.0)
    _yaw, pitch, _roll = q.to_euler_angles()
    assert abs(pitch - 90.0) < 1e-6


def test_quaternion_slerp_negative_dot() -> None:
    """Opposite-hemisphere quaternions trigger the negate branch."""
    q1: Quaternion = Quaternion(1.0, 0.0, 0.0, 0.0)
    # Same orientation, opposite sign -> dot is negative before correction.
    q2: Quaternion = Quaternion(-1.0, 0.0, 0.0, 0.0)
    result: Quaternion = Quaternion.slerp(q1, q2, 0.5)
    # Both represent the identity rotation; result stays the identity.
    assert abs(abs(result.w) - 1.0) < 1e-6
    assert abs(result.x) < 1e-6
    assert abs(result.y) < 1e-6
    assert abs(result.z) < 1e-6


def test_quaternion_slerp_near_identical_lerp() -> None:
    """Nearly identical quaternions take the linear-interpolation path."""
    q1: Quaternion = Quaternion(1.0, 0.0, 0.0, 0.0)
    q2: Quaternion = Quaternion.from_axis_angle(Vec3(0.0, 0.0, 1.0), 0.01)
    result: Quaternion = Quaternion.slerp(q1, q2, 0.5)
    assert abs(result.magnitude() - 1.0) < 1e-9
    # Halfway between two almost-equal rotations stays almost the identity.
    assert abs(result.w - 1.0) < 1e-4


def test_coordinate_frame_rmul() -> None:
    """``__rmul__`` delegates to ``__mul__``."""
    identity: CoordinateFrame = CoordinateFrame(Vec3(0.0, 0.0, 0.0), Quaternion(1.0, 0.0, 0.0, 0.0))
    shifted: CoordinateFrame = CoordinateFrame(Vec3(1.0, 2.0, 3.0), Quaternion(1.0, 0.0, 0.0, 0.0))
    combined: CoordinateFrame = shifted.__rmul__(identity)
    assert abs(combined.origin.x - 1.0) < 1e-9
    assert abs(combined.origin.y - 2.0) < 1e-9
    assert abs(combined.origin.z - 3.0) < 1e-9


class _Vec3Holder(BaseModel):
    """Minimal Pydantic model exercising the ``Vec3Field`` schema hook."""

    model_config = {"arbitrary_types_allowed": True}

    v: Vec3Field


def test_vec3field_validate_from_vec3() -> None:
    """Validating an existing ``Vec3`` returns it unchanged."""
    src: Vec3 = Vec3(1.0, 2.0, 3.0)
    holder: _Vec3Holder = _Vec3Holder(v=src)
    assert holder.v.x == 1.0
    assert holder.v.y == 2.0
    assert holder.v.z == 3.0


def test_vec3field_validate_from_dict() -> None:
    """A ``{x, y, z}`` mapping is coerced to ``Vec3``."""
    holder: _Vec3Holder = _Vec3Holder.model_validate({"v": {"x": 4.0, "y": 5.0, "z": 6.0}})
    assert holder.v.x == 4.0
    assert holder.v.y == 5.0
    assert holder.v.z == 6.0


def test_vec3field_validate_from_sequence() -> None:
    """A 3-element sequence is coerced to ``Vec3``."""
    holder: _Vec3Holder = _Vec3Holder.model_validate({"v": [7.0, 8.0, 9.0]})
    assert holder.v.x == 7.0
    assert holder.v.y == 8.0
    assert holder.v.z == 9.0


def test_vec3field_validate_bad_length_raises() -> None:
    """A wrong-length sequence falls through to the error."""
    with pytest.raises(ValueError):
        _Vec3Holder.model_validate({"v": [1.0, 2.0]})


def test_vec3field_validate_bad_type_raises() -> None:
    """A non-vector input raises."""
    with pytest.raises(ValueError):
        _Vec3Holder.model_validate({"v": "not a vector"})


def test_vec3field_serialize() -> None:
    """Serialization emits a ``{x, y, z}`` dict."""
    holder: _Vec3Holder = _Vec3Holder(v=Vec3(1.0, 2.0, 3.0))
    dumped: dict[str, object] = holder.model_dump()
    assert dumped["v"] == {"x": 1.0, "y": 2.0, "z": 3.0}


def test_vec3field_json_schema() -> None:
    """The JSON-schema hook describes the object shape."""
    schema: dict[str, object] = _Vec3Holder.model_json_schema()
    properties: object = schema["properties"]
    assert isinstance(properties, dict)
    props: dict[str, object] = cast("dict[str, object]", properties)
    v_entry: object = props["v"]
    assert isinstance(v_entry, dict)
    entry: dict[str, object] = cast("dict[str, object]", v_entry)
    assert entry["type"] == "object"
    assert entry["required"] == ["x", "y", "z"]


def test_quaternion_slerp_general_path_smoke() -> None:
    """A wide-angle slerp exercises the general (non-lerp) branch for completeness."""
    q1: Quaternion = Quaternion.from_axis_angle(Vec3(0.0, 0.0, 1.0), 0.0)
    q2: Quaternion = Quaternion.from_axis_angle(Vec3(0.0, 0.0, 1.0), 90.0)
    result: Quaternion = Quaternion.slerp(q1, q2, 0.5)
    rotated: Vec3 = result.rotate_vector(Vec3(1.0, 0.0, 0.0))
    assert abs(rotated.x - math.cos(math.radians(45.0))) < 1e-6
    assert abs(rotated.y - math.sin(math.radians(45.0))) < 1e-6


class _DirectionHolder(BaseModel):
    """Minimal Pydantic model exercising the ``DirectionField`` schema hook."""

    d: DirectionField


def test_directionfield_turns_a_plain_vec3_into_a_direction() -> None:
    holder = _DirectionHolder.model_validate({"d": Vec3(0, 0, 2)})

    assert type(holder.d) is Direction
    assert holder.d == Direction(0, 0, 1)
