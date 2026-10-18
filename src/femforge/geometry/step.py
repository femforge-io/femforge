"""Read an authored STEP into tagged parts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, cast

from femforge.common.tags import TagDict, parse_tags


if TYPE_CHECKING:
    import build123d as b3d

    from femforge.geometry.tags import TaggedCompound, TaggedFace, TaggedPart


def import_tagged_step(path: Path) -> TaggedCompound:
    """The STEP's named solids as world-placed [`TaggedPart`][] children and its named sheet faces as [`TaggedFace`][] children, tagged from their name paths."""
    import build123d as b3d

    from femforge.geometry.tags import TaggedCompound, TaggedFace, TaggedPart

    def tagged_leaves(node: b3d.Compound | b3d.Solid, inherited: TagDict) -> list[TaggedPart | TaggedFace]:
        tags: TagDict = {**inherited, **parse_tags(node.label)}
        children = cast("tuple[b3d.Compound | b3d.Solid, ...]", node.children)
        if children:
            return [leaf for child in children for leaf in tagged_leaves(child, tags)]

        if not tags:
            raise ValueError(f"{path}: a body has no key=value name to tag it by, so the mesh could not group it.")

        placed: b3d.Compound | b3d.Solid = node.located(node.global_location)
        if placed.solids():
            part = TaggedPart([*placed.solids()], tags=tags)
            part.color = node.color
            return [part]

        if not placed.faces():
            raise ValueError(f"{path}: the entry {node.label!r} has neither a solid nor a face to mesh.")

        faces: list[TaggedPart | TaggedFace] = []
        for face in placed.faces():
            tagged = TaggedFace(face, tags=tags)
            tagged.color = node.color
            faces.append(tagged)

        return faces

    read_step = cast("Callable[[Path], b3d.Compound]", b3d.import_step)
    imported: b3d.Compound = read_step(path)
    leaves: list[TaggedPart | TaggedFace] = tagged_leaves(imported, {})
    if not any(isinstance(leaf, TaggedPart) for leaf in leaves):
        raise ValueError(f"{path}: no body has a solid (an already-2D STEP); author the 3D model and let the frame section it.")

    return TaggedCompound(children=leaves)
