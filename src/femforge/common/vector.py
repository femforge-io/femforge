"""Small geometric value types: 2D and 3D vectors, unit directions, boxes, quaternions and coordinate frames."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, cast

from pydantic_core import core_schema


DIRECTION_DIGITS = 12
"""`[-]`: The decimal places a Direction keeps, so scaled copies of one direction compare and hash equal."""

DIRECTION_SLACK = 10.0 ** (1 - DIRECTION_DIGITS)
"""`[-]`: The error a rounded Direction can carry; anything smaller is taken as already exact."""


class Vec2:
    """A mutable 2D vector."""

    def __init__(self, x: float, y: float):
        """Set the components."""
        self.x = x
        self.y = y

    def __add__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: float) -> "Vec2":
        return Vec2(self.x * scalar, self.y * scalar)

    def __truediv__(self, scalar: float) -> "Vec2":
        return Vec2(self.x / scalar, self.y / scalar)

    def rotate(self, angle: float, center: "Vec2 | None" = None) -> "Vec2":
        """Rotate the vector by a given angle in degrees around a center point"""
        import math

        if center is None:
            center = Vec2(0, 0)

        radians = math.radians(angle)
        cos_angle = math.cos(radians)
        sin_angle = math.sin(radians)

        # Translate the point to the origin
        translated_x = self.x - center.x
        translated_y = self.y - center.y

        # Rotate the point
        rotated_x = translated_x * cos_angle - translated_y * sin_angle
        rotated_y = translated_x * sin_angle + translated_y * cos_angle

        # Translate back to the original position
        final_x = rotated_x + center.x
        final_y = rotated_y + center.y

        return Vec2(final_x, final_y)

    def magnitude(self) -> float:
        """Euclidean length of the vector."""
        import math

        return math.sqrt(self.x**2 + self.y**2)

    def __repr__(self):
        return f"Vec2({self.x}, {self.y})"

    def __str__(self):
        return f"({self.x:.3f}, {self.y:.3f})"

    def to_list(self) -> list[float]:
        """The components as ``[x, y]``."""
        return [self.x, self.y]

    def to_tuple(self) -> tuple[float, float]:
        """The components as ``(x, y)``."""
        return (self.x, self.y)


class Rect:
    """An axis-aligned 2D rectangle spanned by its min and max corners."""

    def __init__(self, min: Vec2, max: Vec2):
        """Span ``min`` to ``max``."""
        self.min = min
        self.max = max

    @property
    def center(self) -> Vec2:
        """The midpoint; setting it moves the rectangle."""
        return Vec2(
            (self.min.x + self.max.x) / 2,
            (self.min.y + self.max.y) / 2,
        )

    @center.setter
    def center(self, new_center: Vec2):
        current_center = self.center
        offset = new_center - current_center
        self.min += offset
        self.max += offset

    @property
    def size(self) -> Vec2:
        """The width and height; setting them resizes the rectangle about its center."""
        return Vec2(
            self.max.x - self.min.x,
            self.max.y - self.min.y,
        )

    @size.setter
    def size(self, new_size: Vec2):
        center = self.center
        self.min = Vec2(center.x - new_size.x / 2, center.y - new_size.y / 2)
        self.max = Vec2(center.x + new_size.x / 2, center.y + new_size.y / 2)


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class Vec3:
    """An immutable 3D vector; ``==`` and ``hash`` compare its exact components."""

    x: float
    """`x` `[?]`: The x component."""

    y: float
    """`y` `[?]`: The y component."""

    z: float
    """`z` `[?]`: The z component."""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Vec3):
            return NotImplemented

        return self.to_tuple() == other.to_tuple()

    def __hash__(self) -> int:
        return hash(self.to_tuple())

    def isclose(self, other: "Vec3", tolerance: float = 1e-9) -> bool:
        """Whether every component matches ``other`` within ``tolerance``, relative or absolute."""
        pairs = zip(self.to_tuple(), other.to_tuple())
        return all(math.isclose(mine, theirs, rel_tol=tolerance, abs_tol=tolerance) for mine, theirs in pairs)

    def __add__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scalar: float) -> "Vec3":
        return Vec3(self.x * scalar, self.y * scalar, self.z * scalar)

    def __rmul__(self, scalar: float) -> "Vec3":
        return Vec3(scalar * self.x, scalar * self.y, scalar * self.z)

    def __truediv__(self, scalar: float) -> "Vec3":
        return Vec3(self.x / scalar, self.y / scalar, self.z / scalar)

    def magnitude(self) -> float:
        """Euclidean length |v| of the vector."""
        return math.sqrt(self.x**2 + self.y**2 + self.z**2)

    def dot(self, other: "Vec3") -> float:
        """The scalar product with ``other``."""
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: "Vec3") -> "Vec3":
        """The vector product ``self × other``."""
        return Vec3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def normalized(self) -> "Vec3":
        """A unit vector in the same direction; raises ``ValueError`` for a zero-length vector."""
        mag: float = self.magnitude()
        if mag == 0:
            raise ValueError("cannot normalize a zero-length Vec3")
        return self / mag

    def __abs__(self) -> float:
        return self.magnitude()

    def __repr__(self) -> str:
        return f"Vec3({self.x}, {self.y}, {self.z})"

    def __str__(self):
        return repr(self)

    def __format__(self, format_spec: str) -> str:
        if format_spec.endswith("~"):
            format_spec = format_spec[:-1]
            return f"({format(self.x, format_spec)}, {format(self.y, format_spec)}, {format(self.z, format_spec)})"
        else:
            return f"({format(self.x, format_spec)}, {format(self.y, format_spec)}, {format(self.z, format_spec)})"

    def __rich__(self) -> str:
        return f"([red]{self.x:.3f}[/red], [green]{self.y:.3f}[/green], [blue]{self.z:.3f}[/blue])"

    def to_list(self) -> list[float]:
        """The components as ``[x, y, z]``."""
        return [self.x, self.y, self.z]

    def to_tuple(self) -> tuple[float, float, float]:
        """The components as ``(x, y, z)``."""
        return (self.x, self.y, self.z)


class Direction(Vec3):
    """A unit-length orientation, normalized and rounded on construction; arithmetic on it returns a plain Vec3."""

    __slots__ = ()

    def __init__(self, x: float, y: float, z: float):
        """Normalize ``(x, y, z)`` to unit length and round it to ``DIRECTION_DIGITS`` places.

        Args:
            x: The x component of any non-zero vector along the direction.
            y: The y component.
            z: The z component.
        """
        magnitude: float = math.sqrt(x**2 + y**2 + z**2)

        if magnitude == 0:
            raise ValueError("A direction needs a non-zero vector.")

        # An already-rounded unit vector is kept as given, so a dump and reload cannot shift its last digit.
        scale: float = 1.0 if abs(magnitude - 1.0) <= DIRECTION_SLACK else magnitude
        unit: list[float] = [round(component / scale, DIRECTION_DIGITS) + 0.0 for component in (x, y, z)]
        super().__init__(*unit)


class Box:
    """An axis-aligned 3D box spanned by its min and max corners."""

    def __init__(self, min: Vec3, max: Vec3):
        """Span ``min`` to ``max``."""
        self.min = min
        self.max = max

    @property
    def center(self) -> Vec3:
        """The midpoint; setting it moves the box."""
        return Vec3(
            (self.min.x + self.max.x) / 2,
            (self.min.y + self.max.y) / 2,
            (self.min.z + self.max.z) / 2,
        )

    @center.setter
    def center(self, new_center: Vec3):
        current_center = self.center
        offset = new_center - current_center
        self.min += offset
        self.max += offset

    @property
    def size(self) -> Vec3:
        """The edge lengths; setting them resizes the box about its center."""
        return Vec3(
            self.max.x - self.min.x,
            self.max.y - self.min.y,
            self.max.z - self.min.z,
        )

    @size.setter
    def size(self, new_size: Vec3):
        center = self.center
        self.min = Vec3(center.x - new_size.x / 2, center.y - new_size.y / 2, center.z - new_size.z / 2)
        self.max = Vec3(center.x + new_size.x / 2, center.y + new_size.y / 2, center.z + new_size.z / 2)

    def __format__(self, format_spec: str) -> str:
        return f"Box(min={format(self.min, format_spec)}, max={format(self.max, format_spec)})"

    def __repr__(self) -> str:
        return f"Box(min={self.min}, max={self.max})"

    def __str__(self) -> str:
        return f"Box(min={self.min}, max={self.max})"

    def __rich__(self) -> str:
        return f"Box(min={self.min.__rich__()}, max={self.max.__rich__()})"


class Quaternion:
    """A rotation quaternion ``w + xi + yj + zk``."""

    def __init__(self, w: float, x: float, y: float, z: float):
        """Set the components.

        Args:
            w: The scalar part.
            x: The i component.
            y: The j component.
            z: The k component.
        """
        self.w = w
        self.x = x
        self.y = y
        self.z = z

    def __mul__(self, other: "Quaternion") -> "Quaternion":
        """Hamilton product of two quaternions"""
        w1, x1, y1, z1 = self.w, self.x, self.y, self.z
        w2, x2, y2, z2 = other.w, other.x, other.y, other.z

        w = w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2
        x = w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2
        y = w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2
        z = w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2

        return Quaternion(w, x, y, z)

    def conjugate(self) -> "Quaternion":
        """Conjugate of the quaternion"""
        return Quaternion(self.w, -self.x, -self.y, -self.z)

    def magnitude(self) -> float:
        """The Euclidean norm of the quaternion."""
        import math

        return math.sqrt(self.w**2 + self.x**2 + self.y**2 + self.z**2)

    def normalized(self) -> "Quaternion":
        """The unit quaternion with the same orientation; raises ``ValueError`` for a zero quaternion."""
        mag = self.magnitude()
        if mag == 0:
            raise ValueError("Cannot normalize a zero-length quaternion")
        return Quaternion(self.w / mag, self.x / mag, self.y / mag, self.z / mag)

    def rotate_vector(self, vec: Vec3) -> Vec3:
        """Rotate a vector by this quaternion"""
        q_vec = Quaternion(0, vec.x, vec.y, vec.z)
        rotated_q = self * q_vec * self.conjugate()
        return Vec3(rotated_q.x, rotated_q.y, rotated_q.z)

    def to_rotation_matrix(self) -> list[list[float]]:
        """Convert the quaternion to a 3x3 rotation matrix"""
        w, x, y, z = self.w, self.x, self.y, self.z

        return [
            [1 - 2 * (y**2 + z**2), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x**2 + z**2), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x**2 + y**2)],
        ]

    def to_euler_angles(self) -> tuple[float, float, float]:
        """Convert the quaternion to Euler angles (yaw, pitch, roll) in degrees"""
        w, x, y, z = self.w, self.x, self.y, self.z

        # Yaw (Z-axis rotation)
        siny_cosp = 2 * (w * z + x * y)
        cosy_cosp = 1 - 2 * (y**2 + z**2)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        # Pitch (Y-axis rotation)
        sinp = 2 * (w * y - z * x)
        if abs(sinp) >= 1:
            pitch = math.copysign(math.pi / 2, sinp)  # use 90 degrees if out of range
        else:
            pitch = math.asin(sinp)

        # Roll (X-axis rotation)
        sinr_cosp = 2 * (w * x + y * z)
        cosr_cosp = 1 - 2 * (x**2 + y**2)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        return (math.degrees(yaw), math.degrees(pitch), math.degrees(roll))

    @staticmethod
    def from_euler_angles(yaw: float, pitch: float, roll: float):
        """Create a quaternion from Euler angles in degrees.

        Args:
            yaw: The rotation about Z, in degrees.
            pitch: The rotation about Y, in degrees.
            roll: The rotation about X, in degrees.
        """
        yaw_rad = math.radians(yaw)
        pitch_rad = math.radians(pitch)
        roll_rad = math.radians(roll)

        cy = math.cos(yaw_rad * 0.5)
        sy = math.sin(yaw_rad * 0.5)
        cp = math.cos(pitch_rad * 0.5)
        sp = math.sin(pitch_rad * 0.5)
        cr = math.cos(roll_rad * 0.5)
        sr = math.sin(roll_rad * 0.5)

        return Quaternion(
            w=cy * cp * cr + sy * sp * sr,
            x=cy * cp * sr - sy * sp * cr,
            y=cy * sp * cr + sy * cp * sr,
            z=sy * cp * cr - cy * sp * sr,
        ).normalized()

    @staticmethod
    def from_axis_angle(axis: Vec3, angle_degrees: float) -> "Quaternion":
        """Create a quaternion from an axis and angle in degrees"""
        angle_rad = math.radians(angle_degrees)
        half_angle = angle_rad / 2
        sin_half_angle = math.sin(half_angle)

        return Quaternion(
            w=math.cos(half_angle),
            x=axis.x * sin_half_angle,
            y=axis.y * sin_half_angle,
            z=axis.z * sin_half_angle,
        ).normalized()

    @staticmethod
    def slerp(q1: "Quaternion", q2: "Quaternion", t: float) -> "Quaternion":
        """Spherical linear interpolation between two quaternions.

        Args:
            q1: The orientation at ``t = 0``.
            q2: The orientation at ``t = 1``.
            t: The interpolation fraction.
        """
        q1 = q1.normalized()
        q2 = q2.normalized()

        dot = q1.w * q2.w + q1.x * q2.x + q1.y * q2.y + q1.z * q2.z

        if dot < 0.0:
            q2 = Quaternion(-q2.w, -q2.x, -q2.y, -q2.z)
            dot = -dot

        if dot > 0.9995:
            # If the quaternions are very close, use linear interpolation
            result = Quaternion(
                w=q1.w + t * (q2.w - q1.w),
                x=q1.x + t * (q2.x - q1.x),
                y=q1.y + t * (q2.y - q1.y),
                z=q1.z + t * (q2.z - q1.z),
            ).normalized()
            return result

        theta_0 = math.acos(dot)
        theta = theta_0 * t

        sin_theta_0 = math.sin(theta_0)
        sin_theta = math.sin(theta)

        s0 = math.cos(theta) - dot * sin_theta / sin_theta_0
        s1 = sin_theta / sin_theta_0

        return Quaternion(
            w=(s0 * q1.w) + (s1 * q2.w),
            x=(s0 * q1.x) + (s1 * q2.x),
            y=(s0 * q1.y) + (s1 * q2.y),
            z=(s0 * q1.z) + (s1 * q2.z),
        ).normalized()

    def __repr__(self):
        return f"Quaternion(w={self.w}, x={self.x}, y={self.y}, z={self.z})"

    def __str__(self):
        return f"({self.w:.3f}, {self.x:.3f}, {self.y:.3f}, {self.z:.3f})"


class CoordinateFrame:
    """A position and orientation that maps local coordinates to global ones."""

    def __init__(self, origin: Vec3, orientation: Quaternion):
        """Place the frame at ``origin``, rotated by ``orientation``."""
        self.origin = origin
        self.orientation = orientation

    def transform_position(self, position: Vec3) -> Vec3:
        """Transform a position from local to global coordinates"""
        rotated_position = self.orientation.rotate_vector(position)
        return self.origin + rotated_position

    def inverse_transform_position(self, position: Vec3) -> Vec3:
        """Transform a position from global to local coordinates"""
        translated_position = position - self.origin
        inverse_orientation = self.orientation.conjugate()
        return inverse_orientation.rotate_vector(translated_position)

    def transform_direction(self, direction: Vec3) -> Vec3:
        """Transform a direction vector (ignoring translation) from local to global coordinates"""
        return self.orientation.rotate_vector(direction)

    def inverse_transform_direction(self, direction: Vec3) -> Vec3:
        """Transform a direction vector (ignoring translation) from global to local coordinates"""
        inverse_orientation = self.orientation.conjugate()
        return inverse_orientation.rotate_vector(direction)

    def __mul__(self, other: "CoordinateFrame") -> "CoordinateFrame":
        """Combine two coordinate frames by applying this transformation followed by the other"""
        new_origin = self.transform_position(other.origin)
        new_orientation = self.orientation * other.orientation
        return CoordinateFrame(new_origin, new_orientation)

    def __rmul__(self, other: "CoordinateFrame") -> "CoordinateFrame":
        """Allow left multiplication to combine coordinate frames"""
        return self.__mul__(other)

    def __imul__(self, other: "CoordinateFrame") -> "CoordinateFrame":
        """In-place combination of coordinate frames"""
        combined = self * other
        self.origin = combined.origin
        self.orientation = combined.orientation
        return self

    def __str__(self):
        return f"CoordinateFrame(origin={self.origin}, orientation={self.orientation})"

    def __repr__(self):
        return f"CoordinateFrame(origin={self.origin}, orientation={self.orientation})"


@dataclass(frozen=True)
class _Vec3PydanticSchema:
    """Annotated metadata that validates ``kind`` from a ``Vec3``, a 3-element ``[x, y, z]`` or ``{x, y, z}``, and dumps ``{x, y, z}``."""

    kind: type[Vec3]
    description: str

    def __get_pydantic_core_schema__(self, source: object, handler: object) -> core_schema.CoreSchema:
        def validate(v: object) -> Vec3:
            if isinstance(v, self.kind):
                return v

            if isinstance(v, Vec3):
                return self.kind(v.x, v.y, v.z)

            if isinstance(v, dict):
                d = cast("dict[str, float]", v)
                return self.kind(d["x"], d["y"], d["z"])

            if isinstance(v, (list, tuple)):
                s = cast("Sequence[float]", v)

                if len(s) == 3:
                    return self.kind(s[0], s[1], s[2])

            raise ValueError(f"expected a {self.kind.__name__}, a 3-element [x, y, z], or {{x, y, z}}")

        def serialize(v: Vec3) -> dict[str, float]:
            return {"x": v.x, "y": v.y, "z": v.z}

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(serialize),
        )

    def __get_pydantic_json_schema__(self, core: object, handler: object) -> dict[str, object]:
        num = {"type": "number"}
        return {
            "type": "object",
            "properties": {"x": num, "y": num, "z": num},
            "required": ["x", "y", "z"],
            "description": self.description,
        }


Vec3Field = Annotated[Vec3, _Vec3PydanticSchema(Vec3, "3D vector {x, y, z}")]
"""A [`Vec3`][femforge.common.vector.Vec3] pydantic field, loaded from a Vec3, ``[x, y, z]`` or ``{x, y, z}``."""

DirectionField = Annotated[Direction, _Vec3PydanticSchema(Direction, "Unit direction {x, y, z}, normalized on load")]
"""A [`Direction`][femforge.common.vector.Direction] pydantic field, normalized on load."""
