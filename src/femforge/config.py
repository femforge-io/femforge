"""The composed simulation config: the composition root that ties the gmsh mesh, the domain and the Elmer physics areas together."""

from typing import Mapping

from pydantic import ConfigDict, Field, SerializeAsAny, ValidationInfo, field_validator

from femforge.common.config import ConfigArea
from femforge.common.tags import TagDict
from femforge.elmer.config import PhysicsConfig, PhysicsSelection, solved_stack
from femforge.elmer.physics import Physics
from femforge.frames import Frame, FrameUnion, Solid
from femforge.geometry.config import GeometryArea
from femforge.meshing.config import MeshConfig
from femforge.physical.domain import DomainConfig


__all__ = ["RunMeta", "SimulationConfig"]


class RunMeta(ConfigArea):
    """
    Searchable run metadata: a description and free key=value tags, recorded verbatim in the bundle manifest and
    NEVER part of the run identity — editing them re-labels a run without re-solving it.
    """

    description: str | None = None
    """One line on what this run is for (shown in the driver log, searchable via ``run.find_runs``)."""

    tags: TagDict = Field(default_factory=dict)
    """Free key=value labels (the same tag-dict shape the geometry uses); ``find_runs`` matches by subset."""

    def matches(self, selector: Mapping[str, str]) -> bool:
        """Whether these tags satisfy every selector pair (the same containment rule as ``EntityTag.matches``)."""
        return all(self.tags.get(key) == value for key, value in selector.items())


class SimulationConfig(ConfigArea):
    """The single config spanning the whole pipeline, one area model per concern; ``solved_physics`` / ``resolved_stack`` give what the run solves under ``frame``."""

    # Field validators see the other fields on assignment too, so a reassigned frame/physics/domain that disagrees is rejected before it lands.
    model_config = ConfigDict(validate_assignment=True)

    geometry: SerializeAsAny[GeometryArea] | None = None
    """Optional parametric geometry: when set, the driver calls its ``build_step`` into the run directory before
    meshing and repoints ``mesh.step`` at it — the STEP is derived output, and the run identity comes from these
    parameters rather than an authored file."""

    frame: FrameUnion = Field(default_factory=Solid)
    """How the authored model maps onto the solve; see [`Frame`][femforge.frames.Frame]."""

    mesh: MeshConfig = Field(default_factory=MeshConfig)
    """The gmsh mesh settings: STEP source, global element size, gmsh options."""

    domain: DomainConfig = Field(default_factory=DomainConfig, validate_default=True)
    """Materials + per-region tags."""

    physics: PhysicsSelection = Field(default_factory=PhysicsSelection, validate_default=True)
    """Which physics the Elmer stage solves."""

    meta: RunMeta = Field(default_factory=RunMeta)
    """Searchable description + tags; excluded from ``cache_key``, so labeling never re-identifies a run."""

    @field_validator("frame")
    @classmethod
    def frame_fits_inputs(cls, frame: Frame, info: ValidationInfo) -> Frame:
        """On reassignment, reject a ``frame`` the domain or the physics selection cannot solve."""
        domain: object = info.data.get("domain")
        physics: object = info.data.get("physics")

        if isinstance(domain, DomainConfig):
            domain.check_frame(frame)

        if isinstance(physics, PhysicsSelection):
            physics.check_frame(frame)

        return frame

    @field_validator("domain")
    @classmethod
    def domain_fits_frame(cls, domain: DomainConfig, info: ValidationInfo) -> DomainConfig:
        """Reject conditions the frame cannot solve."""
        frame: object = info.data.get("frame")

        if isinstance(frame, Frame):
            domain.check_frame(frame)

        return domain

    @field_validator("physics")
    @classmethod
    def physics_fits_frame(cls, physics: PhysicsSelection, info: ValidationInfo) -> PhysicsSelection:
        """Reject a selection the frame cannot solve."""
        frame: object = info.data.get("frame")

        if isinstance(frame, Frame):
            physics.check_frame(frame)

        return physics

    def solved_physics(self) -> Physics:
        """The physics the selection solves as under ``frame``."""
        return self.frame.solves(self.physics.selected)

    def resolved_stack(self) -> PhysicsConfig:
        """The solver stack the run solves: the authored ``physics.overrides``, else the solved physics' preset in ``frame``."""
        if self.physics.overrides is not None:
            return self.physics.overrides

        return solved_stack(self.physics.selected, self.frame)

    def customize_physics(self) -> PhysicsConfig:
        """Materialize the resolved stack into ``physics.overrides`` (idempotent) and return it for typed editing."""
        if self.physics.overrides is None:
            self.physics.overrides = solved_stack(self.physics.selected, self.frame)

        return self.physics.overrides
