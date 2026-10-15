"""Convert an authored STEP into the frame the solve meshes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, cast

from femforge.common.frame import Axisymmetric, Frame, Solid
from femforge.common.log import log
from femforge.common.tags import TagDict, parse_tags


if TYPE_CHECKING:
    import build123d as b3d

    from femforge.geometry.tags import TaggedCompound, TaggedPart


def import_tagged_step(path: Path) -> TaggedCompound:
    """The STEP's named solids as world-placed [`TaggedPart`][] children tagged from their name paths."""
    import build123d as b3d

    from femforge.geometry.tags import TaggedCompound, TaggedPart

    def tagged_leaves(node: b3d.Compound | b3d.Solid, inherited: TagDict) -> list[TaggedPart]:
        tags: TagDict = {**inherited, **parse_tags(node.label)}
        children = cast("tuple[b3d.Compound | b3d.Solid, ...]", node.children)
        if children:
            return [leaf for child in children for leaf in tagged_leaves(child, tags)]

        if not tags:
            raise ValueError(f"{path}: a body has no key=value name to tag it by, so the mesh could not group it.")

        if not node.solids():
            raise ValueError(f"{path}: the body {node.label!r} has no solid (an already-2D STEP); author the 3D model and let the frame section it.")

        placed: b3d.Compound | b3d.Solid = node.located(node.global_location)
        part = TaggedPart([*placed.solids()], tags=tags)
        part.color = node.color
        return [part]

    read_step = cast("Callable[[Path], b3d.Compound]", b3d.import_step)
    imported: b3d.Compound = read_step(path)
    return TaggedCompound(children=tagged_leaves(imported, {}))


def apply_frame(step_in: Path, step_out: Path, frame: Frame) -> Path:
    """The STEP the solve meshes: ``step_in`` itself for a solid frame, else its axisymmetric section written to ``step_out``.

    Args:
        step_in: The authored STEP.
        step_out: Where an axisymmetric section is written.
        frame: The solve frame.
    """
    match frame:
        case Solid():
            return step_in

        case Axisymmetric(axis=axis, radial=radial):
            from femforge.geometry.section import axisymmetric_cross_section

            section: TaggedCompound = axisymmetric_cross_section(import_tagged_step(step_in), axis, radial)
            section.export_step(str(step_out))
            log.bind(step=str(step_out), axis=axis.model_dump(mode="json")).info("Axisymmetric section written.")
            return step_out
