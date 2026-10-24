"""build123d helpers: boundary boxes and cylinders, rectangles, assembly-tree pruning and collinearity checks."""

from typing import Any, Callable, Self, cast

import build123d as b3d
from anytree import PostOrderIter
from build123d.topology import shape_core, three_d

from femforge.common.vector import Vec3
from femforge.frames.base import Axis


__all__ = [
    "MATRIX_IDENTITY",
    "Any3D",
    "AnyShape",
    "Mixin3D",
    "NestedRect",
    "Rect",
    "Shape",
    "V",
    "all_colliinear",
    "are_colliinear",
    "boundary_box",
    "boundary_cylinder",
    "collinear_ends",
    "compound_topology",
    "create_debug_arrow",
    "drop_collinear_points",
    "remove_where",
]

V = b3d.Vector
Mixin3D = three_d.Mixin3D
Any3D = Mixin3D[Any]  # pyright: ignore[reportExplicitAny]
Shape = shape_core.Shape
AnyShape = Shape[Any]  # pyright: ignore[reportExplicitAny]
MATRIX_IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def create_debug_arrow(  # pragma: no cover - viewer-only debug geometry
    axis: b3d.Axis,
    length: float = 10,
    radius: float = 0.2,
    color: b3d.Color = b3d.Color("cyan"),
) -> b3d.Part:
    """A debug arrow part on ``axis``, for the viewer.

    Args:
        axis: The axis the arrow starts on and points along.
        length: The overall length.
        radius: The stem radius; the head is 2.5 times wider.
        color: The arrow's color.
    """
    arrow_compound = b3d.Compound()

    proportion_body = 0.85
    proportion_head = 1 - proportion_body

    with b3d.BuildPart() as arrow:
        # Arrow stem
        b3d.Cylinder(
            radius=radius,
            height=length * proportion_body,
            align=(b3d.Align.CENTER, b3d.Align.CENTER, b3d.Align.MIN),
        )

        # Arrow head
        with b3d.Locations((0, 0, length * proportion_body)):
            b3d.Cone(
                bottom_radius=radius * 2.5,
                top_radius=0,
                height=length * proportion_head,
                align=(b3d.Align.CENTER, b3d.Align.CENTER, b3d.Align.MIN),
            )

    arrow_compound += arrow.part
    arrow_compound.color = color
    arrow_compound.label = "Debug Arrow"
    # build123d's Compound.locate() is typed as a Compound | Wire union; this is always a solid Part.
    return cast(b3d.Part, arrow_compound.locate(axis.location))


def boundary_box(shape: b3d.Compound, padding: Vec3 = Vec3(0, 0, 0)) -> b3d.Part:
    """A translucent box around ``shape``'s bounding box, grown by ``padding`` along each axis, half on each side."""
    half_pad = b3d.Vector(padding.x, padding.y, padding.z) * 0.5

    bounds = shape.bounding_box()
    min_pt = bounds.min - half_pad
    max_pt = bounds.max + half_pad
    size = max_pt - min_pt

    with b3d.BuildPart(mode=b3d.Mode.PRIVATE) as box:
        with b3d.BuildSketch(b3d.Plane.XY.offset(min_pt.Z)):
            with b3d.Locations(min_pt):
                b3d.Rectangle(size.X, size.Y, align=(b3d.Align.MIN, b3d.Align.MIN))

        b3d.extrude(amount=size.Z, dir=b3d.Axis.Z.direction)

    assert box.part is not None  # BuildPart always yields a part after the context

    result = box.part
    result.color = b3d.Color(1, 1, 1, 0.2)
    result.label = "Boundary Box"
    return result


def boundary_cylinder(shape: b3d.Compound, axis: Axis = Axis.Z, padding: float = 0.0) -> b3d.Part:
    """A cylinder about ``axis`` around ``shape``'s bounding box, its diameter and length grown by ``padding``, half on each side.

    Args:
        shape: The bodies the cylinder bounds.
        axis: The cylinder's axis, the symmetry axis of an axisymmetric model.
        padding: How much the diameter and the length grow past the bounding box, in millimetres.
    """
    bounds: b3d.BoundBox = shape.bounding_box()
    origin = b3d.Vector(*axis.origin.to_tuple())
    direction = b3d.Vector(*axis.direction.to_tuple())
    corners: list[b3d.Vector] = [b3d.Vector(x, y, z) - origin for x in (bounds.min.X, bounds.max.X) for y in (bounds.min.Y, bounds.max.Y) for z in (bounds.min.Z, bounds.max.Z)]
    heights: list[float] = [corner.dot(direction) for corner in corners]
    radius: float = max((corner - direction * height).length for corner, height in zip(corners, heights, strict=True))
    low: float = min(heights)
    high: float = max(heights)
    placement: b3d.Location = b3d.Plane(origin=origin + direction * ((low + high) / 2), z_dir=direction).location

    result = cast(b3d.Part, placement * b3d.Cylinder(radius + padding / 2, high - low + padding))
    result.color = b3d.Color(1, 1, 1, 0.2)
    result.label = "Boundary Cylinder"

    return result


class Rect:
    """An axis-aligned 2D rectangle of build123d vectors, with edge and corner accessors."""

    def __init__(self, min: V | None = None, max: V | None = None, label: str = ""):
        """Span ``min`` to ``max``.

        Args:
            min: The lower-left corner; None is the origin.
            max: The upper-right corner; None is the origin.
            label: A name shown in the repr.
        """
        self.min = min or V()
        self.max = max or V()
        self.label = label

    @property
    def left(self) -> float:
        """The x of the left edge; setting it moves only that edge."""
        return self.min.X

    @left.setter
    def left(self, value: float):
        self.min.X = value

    @property
    def right(self) -> float:
        """The x of the right edge; setting it moves only that edge."""
        return self.max.X

    @right.setter
    def right(self, value: float):
        self.max.X = value

    @property
    def bottom(self) -> float:
        """The y of the bottom edge; setting it moves only that edge."""
        return self.min.Y

    @bottom.setter
    def bottom(self, value: float):
        self.min.Y = value

    @property
    def top(self) -> float:
        """The y of the top edge; setting it moves only that edge."""
        return self.max.Y

    @top.setter
    def top(self, value: float):
        self.max.Y = value

    @property
    def width(self) -> float:
        """The extent along x."""
        return self.max.X - self.min.X

    @property
    def height(self) -> float:
        """The extent along y."""
        return self.max.Y - self.min.Y

    @property
    def size(self) -> V:
        """The width and height as a vector."""
        return V(self.width, self.height)

    @property
    def center(self) -> V:
        """The midpoint."""
        return V(
            (self.min.X + self.max.X) / 2,
            (self.min.Y + self.max.Y) / 2,
        )

    @property
    def corners(self) -> list[V]:
        """The four corners, counter-clockwise from ``min``."""
        return [
            self.min,
            V(self.max.X, self.min.Y),
            self.max,
            V(self.min.X, self.max.Y),
        ]

    @property
    def points(self) -> list[V]:
        """The four corners, the same as ``corners``."""
        return self.corners

    # Allow for offestting by addition
    def __add__(self, other: V) -> "Rect":
        return Rect(self.min + other, self.max + other)

    def __sub__(self, other: V) -> "Rect":
        return Rect(self.min - other, self.max - other)

    # += and -= operators
    def __iadd__(self, other: V) -> "Rect":
        self.min += other
        self.max += other
        return self

    def __isub__(self, other: V) -> "Rect":
        self.min -= other
        self.max -= other
        return self

    def __repr__(self) -> str:
        return self.__format__("")

    def __format__(self, format_spec: str, /) -> str:
        format_spec = format_spec or ".3f"
        parts = {
            "min": f"({self.min.X:{format_spec}}, {self.min.Y:{format_spec}})",
            "max": f"({self.max.X:{format_spec}}, {self.max.Y:{format_spec}})",
            "label": f"{self.label}" if self.label else "",
        }
        return f"Rect({', '.join(v for v in parts.values() if v)})"


class NestedRect:
    """The frame-shaped region between an inner and an outer rectangle."""

    def __init__(self, inside: Rect | None = None, outside: Rect | None = None, label: str = ""):
        """Hold the two rectangles.

        Args:
            inside: The inner rectangle; None starts an empty one.
            outside: The outer rectangle; None starts an empty one.
            label: A name for the region and its faces; empty derives one from the object id.
        """

        self.label = label or f"NestedRect_{id(self)}"
        self.inside = inside or Rect(label=f"{self.label}.inside")
        self.outside = outside or Rect(label=f"{self.label}.outside")

    def __add__(self, other: V) -> Self:
        new_inside = self.inside + other
        new_outside = self.outside + other
        result = type(self)()
        result.inside = new_inside
        result.outside = new_outside
        return result

    def __sub__(self, other: V) -> Self:
        new_inside = self.inside - other
        new_outside = self.outside - other
        return type(self)(inside=new_inside, outside=new_outside)

    def __iadd__(self, other: V) -> Self:
        self.inside += other
        self.outside += other
        return self

    def __isub__(self, other: V) -> Self:
        self.inside -= other
        self.outside -= other
        return self

    def __repr__(self) -> str:
        parts = {
            "inside": f"{self.inside}",
            "outside": f"{self.outside}",
            "label": f"{self.label}" if self.label else "",
        }
        return f"NestedRect({', '.join(v for v in parts.values() if v)})"

    def face(self) -> b3d.Face:
        """Return a face representing the area between the inside and outside rectangles."""
        outer_wire = b3d.Rectangle(self.outside.width, self.outside.height).wire().translate(self.outside.center)
        inner_wire = b3d.Rectangle(self.inside.width, self.inside.height).wire().translate(self.inside.center)
        face = b3d.Face(outer_wire, [inner_wire], label=f"{self.label}_Face")
        return face

    def extrude(self, thickness: float) -> b3d.Part:
        """Return a part representing the area between the inside and outside rectangles extruded to the given thickness."""
        face = self.face()
        part = b3d.extrude(face, amount=thickness)
        part.label = f"{self.label}_Part"
        return part


def _tree_nodes(root: b3d.Compound, iterator: Callable[[b3d.Compound], object]) -> list[b3d.Compound]:
    """The assembly-tree nodes under ``root`` via an anytree iterator (untyped upstream; single cast site)."""
    return cast("list[b3d.Compound]", list(iterator(root)))  # pyright: ignore[reportArgumentType]


def remove_where(assembly: b3d.Compound, criteria_func: Callable[[b3d.Compound], bool]) -> b3d.Compound:
    """Filter nodes based on criteria and automatically vacuum empty leftover compounds."""

    nodes_to_remove = set(node for node in _tree_nodes(assembly, PostOrderIter) if criteria_func(node))

    for node in _tree_nodes(assembly, PostOrderIter):
        children = cast("tuple[b3d.Compound, ...]", node.children)
        kept = [child for child in children if child not in nodes_to_remove]
        if children:
            node.children = kept

        if isinstance(node, b3d.Compound) and not kept and len(node.solids()) == 0 and node != assembly:
            nodes_to_remove.add(node)

    return assembly


def are_colliinear(p1: V, p2: V, p3: V, tolerance: float = 1e-9) -> bool:
    """Check if three points are collinear within a given tolerance.

    Args:
        p1: The first point.
        p2: The middle point.
        p3: The last point.
        tolerance: The largest cross product of the two segments still counted as collinear.
    """
    v1 = p2 - p1
    v2 = p3 - p2
    cross_product = v1.cross(v2)
    return cross_product.length <= tolerance


def all_colliinear(points: list[V], tolerance: float = 1e-9) -> bool:
    """Check if all points in the list are collinear within a given tolerance."""
    if len(points) < 3:
        return True  # Less than 3 points are always collinear

    p1 = points[0]
    p2 = points[1]

    for i in range(2, len(points)):
        p3 = points[i]
        if not are_colliinear(p1, p2, p3, tolerance):
            return False

    return True


def drop_collinear_points(points: list[V]) -> list[V]:
    """``points`` with interior points that continue the previous direction dropped (endpoints kept)."""
    kept: list[V] = points[:1]
    for index in range(1, len(points) - 1):
        if not are_colliinear(points[index - 1], points[index], points[index + 1]):
            kept.append(points[index])

    kept.append(points[-1])
    return kept


def collinear_ends(points: list[V]) -> list[V]:
    """Assume points are collinear, find the furthest and closest point along that line"""
    if len(points) < 2:
        return points

    # Use the first two points to define the line
    p1 = points[0]
    p2 = points[1]
    direction = (p2 - p1).normalized()

    # Project all points onto the line defined by p1 and direction
    projections = [(point, (point - p1).dot(direction)) for point in points]

    # Find the minimum and maximum projections
    min_proj = min(projections, key=lambda x: x[1])
    max_proj = max(projections, key=lambda x: x[1])

    return [min_proj[0], max_proj[0]]


def compound_topology(compound: b3d.Compound):
    """A rich tree of ``compound``'s descendants, by type and label."""
    from rich.tree import Tree

    tree = Tree(f"Compound: {compound.label or 'Unnamed'}")

    def walk(node: b3d.Compound, parent_tree: Tree):
        for child in cast("tuple[b3d.Compound, ...]", node.children):
            label = f"{type(child).__name__}: {child.label or 'Unnamed'}"
            child_tree = parent_tree.add(label)
            if isinstance(child, b3d.Compound):
                walk(child, child_tree)

    walk(compound, tree)
    return tree
