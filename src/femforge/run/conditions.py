"""The ``conditions.json`` sidecar: what each body and boundary group of a run received from the sif, and from which condition."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from femforge.common.files import BundleFile
from femforge.common.tags import TagDict
from femforge.elmer.config import SifValue
from femforge.elmer.sections import SifSection
from femforge.elmer.sim import SifLanding, Sim
from femforge.physical.conditions import ConditionTarget, ConditionUnion
from femforge.physical.domain import EntityTag
from femforge.run.bundle import write_atomic


__all__ = ["SifKeyword", "SolveComponent", "ConditionRecord", "GroupConditions", "ConditionsFile"]


class SifKeyword(BaseModel):
    """One keyword an emission put into a sif block."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    name: str
    """The sif keyword, such as ``Force 1``."""

    value: SifValue
    """`[?]`: The emitted value, in the SI unit Elmer reads the keyword in; loads of one kind in one block add up to the value the sif holds."""


class SolveComponent(BaseModel):
    """One solve-frame component of a condition vector."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    index: int
    """The 1-based solve coordinate."""

    value: float
    """`[?]`: The component, in the SI unit of the condition vector."""


class ConditionRecord(BaseModel):
    """One emission into a group: the condition and tag it came from, the sif block it landed in and the keywords it contributed."""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    kind: str
    """The condition kind, or the pseudo-kind of a block femforge emits on its own, such as ``far_field``."""

    condition: ConditionUnion | None = None
    """The authored condition; None for a block femforge emits on its own, such as a far field, a symmetry axis or the force Component of a magnet."""

    tag: int | None = None
    """The index of the authoring tag in the domain tags of the run config; None for a block femforge emits on its own."""

    selector: TagDict = Field(default_factory=dict)
    """The selector of the authoring tag; empty for a block femforge emits on its own."""

    section: SifSection
    """The sif section of the block."""

    block: int
    """The block number within its sif section."""

    block_name: str
    """The block name, as the ``!`` comment above it in the sif records it."""

    keywords: list[SifKeyword] = Field(default_factory=list)
    """The keywords this emission contributed, in the solve frame."""

    solve_components: list[SolveComponent] = Field(default_factory=list)
    """The solve-frame components of the condition vector: held coordinates, a force share or a magnetization direction."""

    dropped: list[str] = Field(default_factory=list)
    """The authored fields the frame has no solve coordinate for, such as ``["z"]`` under a Cartesian 2D frame normal to Z."""


class GroupConditions(BaseModel):
    """Every emission one body or boundary group received; an empty list when it received none."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    group: str
    """The group name, shared by the mesh and the sif."""

    conditions: list[ConditionRecord] = Field(default_factory=list)
    """The emissions in sif order."""


class ConditionsFile(BaseModel):
    """``conditions.json``: the emissions of every body and boundary group of a run."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    bodies: list[GroupConditions] = Field(default_factory=list)
    """Every body group, in mesher order."""

    boundaries: list[GroupConditions] = Field(default_factory=list)
    """Every boundary group, in mesher order."""

    @classmethod
    def capture(cls, sim: Sim, tags: Sequence[EntityTag]) -> ConditionsFile:
        """Record the landings of a written ``sim``, whose blocks carry their sif ids once ``Sim.write`` ran.

        Args:
            sim: The generated and written sim.
            tags: The domain tags of the run config, which ``ConditionRecord.tag`` indexes.
        """
        landings: list[SifLanding] = sim.landings

        def record(landing: SifLanding) -> ConditionRecord:
            keywords: list[SifKeyword] = [SifKeyword(name=name, value=value) for name, value in landing.keywords.items()]
            index: int | None = next((number for number, tag in enumerate(tags) if tag is landing.tag), None)
            condition = cast("ConditionUnion | None", landing.condition)
            components: tuple[tuple[int, float], ...] = () if condition is None else condition.solve_components(sim.frame)
            dropped: tuple[str, ...] = () if condition is None else condition.dropped_fields(sim.frame)

            return ConditionRecord(
                kind=landing.kind,
                condition=condition,
                tag=index,
                selector=dict(landing.tag.selector) if landing.tag is not None else {},
                section=landing.section,
                block=landing.block.id,
                block_name=landing.block.name,
                keywords=keywords,
                solve_components=[SolveComponent(index=coordinate, value=value) for coordinate, value in components],
                dropped=list(dropped),
            )

        records: list[tuple[SifLanding, ConditionRecord]] = [(landing, record(landing)) for landing in landings]

        def of(target: ConditionTarget, names: Sequence[str]) -> list[GroupConditions]:
            return [GroupConditions(group=name, conditions=[entry for landing, entry in records if landing.target is target and landing.group == name]) for name in names]

        return cls(
            bodies=of(ConditionTarget.BODY, [group.name for group in sim.physical_groups]),
            boundaries=of(ConditionTarget.BOUNDARY, [group.name for group in sim.boundary_groups]),
        )

    @classmethod
    def load(cls, sim_dir: str | Path) -> ConditionsFile:
        """Read ``conditions.json`` from ``sim_dir``."""
        return cls.model_validate_json((Path(sim_dir) / BundleFile.CONDITIONS).read_text(encoding="utf-8"))

    def write(self, sim_dir: str | Path) -> Path:
        """Write ``conditions.json`` into ``sim_dir`` and return its path."""
        path: Path = Path(sim_dir) / BundleFile.CONDITIONS
        write_atomic(path, self.model_dump_json(indent=2))

        return path

    def body(self, name: str) -> GroupConditions | None:
        """The emissions of the body group ``name``, or None when the run has no such group."""
        return next((group for group in self.bodies if group.group == name), None)

    def boundary(self, name: str) -> GroupConditions | None:
        """The emissions of the boundary group ``name``, or None when the run has no such group."""
        return next((group for group in self.boundaries if group.group == name), None)
