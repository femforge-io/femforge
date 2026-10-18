"""The gmsh mesh settings and the config surface the mesher reads."""

from typing import Protocol

from pydantic import ConfigDict, Field

from femforge.common.config import ConfigArea
from femforge.frames import Frame
from femforge.meshing.gmsh_options import GmshOptions
from femforge.physical.domain import DomainConfig
from femforge.physical.units import Length, quantity


class MeshConfig(ConfigArea):
    """How the geometry is discretized for the gmsh backend: the STEP source, domain-wide element size, and typed gmsh knobs."""

    model_config = ConfigDict(extra="forbid")

    step: str = "data/geometry.step"
    """Path to the STEP file the mesher imports; its build123d part names are parsed into materials and region tags (`MeshEntity`)."""

    global_mesh_size: Length = Field(default_factory=lambda: quantity(1.0, "mm"))
    """`h` `[mm]`: Domain-wide target element size, which a region's [`EntityTag.mesh_size`][femforge.physical.domain.EntityTag.mesh_size] overrides."""

    gmsh_options: GmshOptions = Field(default_factory=GmshOptions)
    """Typed gmsh options applied before meshing."""

    deterministic: bool = False
    """Pin meshing to one thread, so HXT's multithreading cannot vary the result and the same STEP and sizes always yield the identical mesh; slower, and part of the run identity."""

    def resolved_gmsh_options(self) -> GmshOptions:
        """The options the mesher applies: ``gmsh_options``, thread-pinned to 1 when ``deterministic``."""
        if not self.deterministic:
            return self.gmsh_options
        return self.gmsh_options.model_copy(update={"num_threads": 1})


class SupportsMeshing(Protocol):
    """The config surface the gmsh `Mesher` requires, which [`SimulationConfig`][femforge.config.SimulationConfig] satisfies structurally."""

    @property
    def frame(self) -> Frame:
        """The solve frame, whose ``model_dim`` is the body dimension of the STEP."""
        ...

    mesh: MeshConfig
    """The gmsh mesh settings (STEP source, element size, gmsh options)."""

    domain: DomainConfig
    """The materials + per-region tags the mesher matches STEP entities against."""
