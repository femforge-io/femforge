"""Typed gmsh options, each field carrying its gmsh option name as ``Map(...)`` metadata."""

from __future__ import annotations

from collections.abc import Callable
from enum import IntEnum
from typing import Annotated

import psutil
from pydantic import BaseModel, ConfigDict, Field

from femforge.common.pydantic import HashExclude, Map, mapped_items


def _default_num_threads() -> int:
    """All available cores (``psutil.cpu_count(logical=False)``), or 1 if it can't be determined — the default meshing thread count."""
    return psutil.cpu_count(logical=False) or 1


class MeshAlgorithm2D(IntEnum):
    """A gmsh 2D meshing algorithm, valued as the integer ``Mesh.Algorithm`` takes."""

    MESH_ADAPT = 1
    """MeshAdapt, the most robust on complex curved surfaces."""

    AUTOMATIC = 2
    """Delaunay on plane surfaces and MeshAdapt on the rest."""

    DELAUNAY = 5
    """Delaunay, the fastest on large plane surfaces."""

    FRONTAL_DELAUNAY = 6
    """Frontal-Delaunay, gmsh's own default and the choice for high element quality."""


class MeshAlgorithm3D(IntEnum):
    """A gmsh 3D meshing algorithm, valued as the integer ``Mesh.Algorithm3D`` takes."""

    DELAUNAY = 1
    """Delaunay, gmsh's own default and its most robust 3D mesher."""

    FRONTAL = 4
    """Frontal, Netgen's advancing-front tetrahedral mesher."""

    MMG3D = 7
    """MMG3D, for anisotropic tetrahedral meshes."""

    HXT = 10
    """HXT, a parallel reimplementation of Delaunay that spreads over the meshing threads."""


def _gmsh_set_number(name: str, value: float) -> None:  # pragma: no cover - drives the live gmsh option store, exercised by the gated integration tests
    """
    Set one gmsh numeric option on the live option store.

    gmsh is imported lazily so this module (and the pure-logic tests that inject their own setter) load without the gmsh binary present.
    """
    import gmsh

    gmsh.option.setNumber(name, value)


def _gmsh_set_string(name: str, value: str) -> None:  # pragma: no cover - drives the live gmsh option store, exercised by the gated integration tests
    """
    Set one gmsh string option on the live option store.

    gmsh is imported lazily so this module (and the pure-logic tests that inject their own setter) load without the gmsh binary present.
    """
    import gmsh

    gmsh.option.setString(name, value)


class GmshOptions(BaseModel):
    """Typed gmsh meshing options; a knob left at None keeps gmsh's own default."""

    model_config = ConfigDict(validate_assignment=True, use_attribute_docstrings=True)

    import_labels: Annotated[bool, Map("Geometry.OCCImportLabels")] = True
    """Import STEP/OpenCASCADE entity labels (Geometry.OCCImportLabels); the mesher needs these to match parts to materials."""

    terminal_output: Annotated[bool, Map("General.Terminal"), HashExclude()] = False
    """Print gmsh's progress to the terminal (General.Terminal); never changes the mesh, so it stays out of every identity."""

    # Always emitted, because gmsh loads a persisted user options file on initialize that can carry a quad algorithm.
    algorithm_2d: Annotated[MeshAlgorithm2D, Map("Mesh.Algorithm")] = MeshAlgorithm2D.FRONTAL_DELAUNAY
    """2D meshing algorithm (Mesh.Algorithm); the default Frontal-Delaunay gives the all-triangle surface mesh HXT needs and meshes the bodies of a 2D model."""

    algorithm_3d: Annotated[MeshAlgorithm3D, Map("Mesh.Algorithm3D")] = MeshAlgorithm3D.HXT
    """3D meshing algorithm (Mesh.Algorithm3D); the default HXT runs on ``num_threads`` and needs the all-triangle surface mesh ``recombine_all=False`` keeps."""

    # Always emitted, because a persisted gmsh user options file can carry Mesh.RecombineAll = 1.
    recombine_all: Annotated[bool, Map("Mesh.RecombineAll")] = False
    """Recombine the simplex mesh into quads and hexes (Mesh.RecombineAll); keep it False, since WhitneyAVSolver needs tetrahedra and recombining makes 3D meshing fail."""

    mesh_size_min: Annotated[float | None, Map("Mesh.MeshSizeMin")] = None
    """`h_min` `[m]`: Lower clamp on element size (Mesh.MeshSizeMin); None inherits gmsh's default."""

    mesh_size_max: Annotated[float | None, Map("Mesh.MeshSizeMax")] = None
    """`h_max` `[m]`: Upper clamp on element size (Mesh.MeshSizeMax); None inherits gmsh's default."""

    optimize: Annotated[bool, Map("Mesh.Optimize")] = True
    """Run mesh optimization after generation (Mesh.Optimize)."""

    smoothing: Annotated[int | None, Map("Mesh.Smoothing")] = None
    """Number of mesh smoothing steps (Mesh.Smoothing); None inherits gmsh's default."""

    num_threads: Annotated[int | None, Map("General.NumThreads"), HashExclude()] = Field(default_factory=_default_num_threads)
    """Meshing threads (General.NumThreads): every physical core by default, None for gmsh's own default of one, and never part of an identity."""

    target_units: Annotated[str | None, Map("Geometry.OCCTargetUnit")] = "M"
    """Length unit OpenCASCADE converts the imported STEP to (Geometry.OCCTargetUnit): ``"M"`` keeps the pipeline in metres, None keeps the STEP's own unit."""

    def apply(
        self,
        set_number: Callable[[str, float], None] | None = None,
        set_string: Callable[[str, str], None] | None = None,
    ) -> None:
        """Set each option that is not None under its ``Map(...)`` name, through ``set_number`` and ``set_string`` when given, else on the live gmsh option store."""
        # Imported here so loguru stays off the import path of every config.
        from femforge.common.log import log

        emit_number: Callable[[str, float], None] = _gmsh_set_number if set_number is None else set_number
        emit_string: Callable[[str, str], None] = _gmsh_set_string if set_string is None else set_string

        for option_name, value in mapped_items(self):
            # A str option (the OCC target unit) goes through setString; bool / IntEnum / int / float are all setNumber.
            if isinstance(value, str):
                log.bind(option=option_name, value=value).debug("Setting gmsh option.")
                emit_string(option_name, value)
            elif isinstance(value, (int, float)):
                log.bind(option=option_name, value=value).debug("Setting gmsh option.")
                emit_number(option_name, float(value))
