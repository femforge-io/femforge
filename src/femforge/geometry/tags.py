"""Tagged build123d shapes whose tags ride their STEP names into the mesh."""

import json
from collections.abc import Iterable, Sequence
from typing import ClassVar, cast

import build123d as b3d

from femforge.artifacts.keys import sha256_json
from femforge.common.tags import HasTags, TagDict, TagKey
from femforge.geometry.utils import Any3D, AnyShape, Mixin3D


def named(tags: TagDict | None, name: str | None) -> TagDict | None:
    """``tags`` with ``name`` set as its [`TagKey.NAME`][] tag, or ``tags`` unchanged when ``name`` is None."""
    return tags if name is None else {**(tags or {}), TagKey.NAME: name}


class TaggedCompound(b3d.Compound, HasTags):
    """A build123d compound whose tags pass down to its tagged leaves on export."""

    def __init__(self, *, children: Sequence[AnyShape] = (), tags: TagDict | None = None) -> None:
        """Build the assembly over ``children`` and tag it with ``tags``, the ``key=value`` pairs every tagged leaf below inherits on export."""
        b3d.Compound.__init__(self, children=children)  # pyright: ignore[reportUnknownMemberType]  # OCP ships no stubs
        HasTags.__init__(self, tags)
        self.label = self.tags_str()

    def rebuild_labels(self) -> None:
        """Relabel every tagged part below this compound with its tags."""
        for child in cast("tuple[object, ...]", self.descendants):
            if isinstance(child, TaggedPart):
                child.label = child.tags_str()

    def placed_leaves(self, location: b3d.Location | None = None) -> list["TaggedPart | TaggedFace | TaggedEdge"]:
        """Fresh copies of the tagged leaves at most one dimension below the highest, world-placed, moved by ``location``, and carrying their ancestors' tags (deeper wins).

        Solids keep their marker faces and faces their marker edges; edges under solids are construction geometry and dropped.
        """
        moved_by: b3d.Location = location if location is not None else b3d.Location()
        found: list[TaggedPart | TaggedFace | TaggedEdge] = [leaf for leaf in cast("tuple[object, ...]", self.descendants) if isinstance(leaf, (TaggedPart, TaggedFace, TaggedEdge))]
        kept_from: int = max((leaf.dim for leaf in found), default=0) - 1

        leaves: list[TaggedPart | TaggedFace | TaggedEdge] = []
        for leaf in found:
            if leaf.dim < kept_from:
                continue

            # A compound's own location never reaches its children's shapes, and leaf.moved() deep-copies the whole
            # parent tree, so the shapes are rebuilt with every ancestor's location applied.
            parent = cast("b3d.Compound | None", leaf.parent)
            ancestors: b3d.Location = parent.global_location if parent is not None else b3d.Location()
            placement: b3d.Location = moved_by * ancestors
            tags: TagDict = {key: value for node in leaf.path if isinstance(node, HasTags) for key, value in node.tags.items()}

            if isinstance(leaf, TaggedPart):
                placed: list[TaggedPart | TaggedFace | TaggedEdge] = [TaggedPart([solid.moved(placement) for solid in leaf.solids()], tags=tags)]
            elif isinstance(leaf, TaggedFace):
                placed = [TaggedFace(face.moved(placement), tags=tags) for face in leaf.faces()]
            else:
                placed = [TaggedEdge(edge.moved(placement), tags=tags) for edge in leaf.edges()]

            for copy in placed:
                copy.color = leaf.color

            leaves.extend(placed)

        return leaves

    def fingerprint(self) -> str:
        """Order-independent SHA-256 of the placed leaves: kind, tags, color, size and quantized mass properties, faces and vertices.

        Two imports of one STEP agree; an authored compound and its import agree as closely as OCCT integrates them alike.
        """

        def rounded(value: float, scale: float) -> float:
            # + 0.0 folds -0.0 into 0.0, which json.dumps tells apart.
            return round(value / scale, 9) + 0.0

        def point(vector: b3d.Vector, scale: float) -> list[float]:
            return [rounded(coordinate, scale) for coordinate in vector]

        def describe(leaf: TaggedPart | TaggedFace | TaggedEdge) -> list[object]:
            box: b3d.BoundBox = leaf.bounding_box(optimal=True)
            scale: float = box.diagonal
            center: b3d.Vector = leaf.center(b3d.CenterOf.MASS)
            # STEP export drops transparency, so alpha is left out.
            rgba = None if leaf.color is None else cast("tuple[float, float, float, float]", tuple(leaf.color))
            color: list[float] | None = None if rgba is None else [rounded(channel, 1.0) for channel in rgba[:3]]
            volume: float | None = rounded(leaf.volume, scale**3) if isinstance(leaf, TaggedPart) else None
            area: float | None = None if isinstance(leaf, TaggedEdge) else rounded(leaf.area, scale**2)
            length: float | None = rounded(leaf.length, scale) if isinstance(leaf, TaggedEdge) else None
            tags: list[list[str]] = sorted([str(key), value] for key, value in leaf.tags.items())
            faces: list[list[float]] = sorted([rounded(face.area, scale**2), *point(face.center(b3d.CenterOf.MASS), scale)] for face in leaf.faces())
            vertices: list[list[float]] = sorted(point(vertex.center(), scale) for vertex in leaf.vertices())

            return [
                type(leaf).__name__,
                leaf.tags.get(TagKey.NAME),
                tags,
                color,
                float(f"{scale:.9g}"),
                volume,
                area,
                length,
                point(center, scale),
                point(box.min, scale),
                point(box.max, scale),
                faces,
                vertices,
            ]

        described: list[list[object]] = [describe(leaf) for leaf in self.placed_leaves()]
        return sha256_json(sorted(described, key=json.dumps))

    def flatten_and_tag(self) -> b3d.Compound:
        """A new compound of the world-placed tagged leaves (see [`placed_leaves`][]), labelled with their tags; ``self`` is left untouched."""
        return b3d.Compound(children=self.placed_leaves(), label=f"Flattened_{self.label}")

    def export_step(self, filename: str, flatten: bool = True) -> None:
        """Export the compound, flattened into its tagged leaves unless ``flatten`` is False, to the STEP file ``filename``."""
        compound_to_export: b3d.Compound = self.flatten_and_tag() if flatten else self
        _ = b3d.export_step(compound_to_export, filename)  # pyright: ignore[reportUnknownMemberType]  # OCP ships no stubs

    @property
    def children_at_global_location(self) -> list[Any3D]:
        """Return a list of children located at their global location."""
        return [child.located(child.global_location) for child in cast("tuple[object, ...]", self.children) if isinstance(child, Mixin3D)]


class TaggedFace(b3d.Face, HasTags):
    """A build123d face whose tags ride its STEP name into the mesh: a body of a 2D model, or a named boundary group of a 3D one."""

    dim: ClassVar[int] = 2
    """The topological dimension of the leaf."""

    def __init__(self, face: b3d.Face, *, tags: TagDict | None = None, name: str | None = None) -> None:
        """Wrap ``face`` and tag it.

        Args:
            face: The face to wrap, placed where it is.
            tags: The ``key=value`` pairs domain materials and ``EntityTag`` selectors match, e.g. ``{TagKey.MATERIAL: N52().tag}``.
            name: The body or boundary-group name, kept as written in the ``Name`` tag and uppercased in the mesh, Elmer, ``entities.json`` and the viewer; None composes it from the tags.
        """
        b3d.Face.__init__(self, cast("object", face.wrapped))  # pyright: ignore[reportUnknownMemberType]  # OCP ships no stubs
        HasTags.__init__(self, named(tags, name))
        self.label = self.tags_str()


class TaggedSketch(b3d.Sketch, HasTags):
    """A build123d sketch that carries tags."""

    def __init__(self, shapes: Iterable[AnyShape] | None = None, *, tags: TagDict | None = None) -> None:
        """Build the sketch from ``shapes``, such as faces, and tag it with ``tags``."""
        b3d.Sketch.__init__(self, shapes)  # pyright: ignore[reportUnknownMemberType]  # OCP ships no stubs
        HasTags.__init__(self, tags)
        self.label = self.tags_str()


class TaggedPart(b3d.Part, HasTags):
    """A build123d part whose tags ride its STEP name into the mesh."""

    dim: ClassVar[int] = 3
    """The topological dimension of the leaf."""

    def __init__(self, shapes: Iterable[AnyShape], *, tags: TagDict | None = None, name: str | None = None) -> None:
        """Build the part from ``shapes`` and tag it.

        Args:
            shapes: The solids the part holds, such as ``[box]`` or a build123d part.
            tags: The ``key=value`` pairs domain materials and ``EntityTag`` selectors match, e.g. ``{TagKey.MATERIAL: N52().tag}``.
            name: The body name, kept as written in the ``Name`` tag and uppercased in the mesh, Elmer, ``entities.json`` and the viewer; None composes it from the tags.
        """
        b3d.Part.__init__(self, shapes)  # pyright: ignore[reportUnknownMemberType]  # OCP ships no stubs
        HasTags.__init__(self, named(tags, name))
        self.label = self.tags_str()


class TaggedEdge(b3d.Edge, HasTags):
    """A build123d edge whose tags name a boundary group of a 2D model, such as the section of a named face."""

    dim: ClassVar[int] = 1
    """The topological dimension of the leaf."""

    def __init__(self, edge: b3d.Edge, *, tags: TagDict | None = None, name: str | None = None) -> None:
        """Wrap ``edge`` and tag it.

        Args:
            edge: The edge to wrap, placed where it is.
            tags: The ``key=value`` pairs ``EntityTag`` selectors match.
            name: The boundary-group name, kept as written in the ``Name`` tag and uppercased in the mesh, Elmer and ``entities.json``; None composes it from the tags.
        """
        b3d.Edge.__init__(self, cast("object", edge.wrapped))  # pyright: ignore[reportUnknownMemberType]  # OCP ships no stubs
        HasTags.__init__(self, named(tags, name))
        self.label = self.tags_str()
