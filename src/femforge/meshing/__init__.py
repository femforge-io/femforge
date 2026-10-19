"""The meshing stage: typed gmsh settings, the mesh backends, element sizing, and the body and boundary groups a STEP is meshed into."""

from __future__ import annotations

from femforge.meshing.backend import GmshMesher, MeshBackend, MeshError, MeshInputs, MeshJob, MeshReport, WorkerMesher, mesh_inputs
from femforge.meshing.boundary import AXIS_SUFFIX, AXIS_TOLERANCE_M, BOUNDARY_SEP, EXTERNAL_SUFFIX, BoundaryGroup, BoundaryPartition, axis_entity_tags, partition_boundary_faces, read_face_adjacency
from femforge.meshing.config import MeshConfig, SupportsMeshing
from femforge.meshing.firewall import block_gmsh
from femforge.meshing.geometry_utils import EntityGeometry, extract_entity_geometry
from femforge.meshing.gmsh_options import GmshOptions, MeshAlgorithm2D, MeshAlgorithm3D
from femforge.meshing.groups import BodyGroupRecord, BoundaryGroupRecord, Bounds, MeshGroupsFile, PhysicalGroup, format_boundary_groups, format_physical_groups, raise_on_duplicate_names
from femforge.meshing.mesh_sizing import ResolvedRegionMeshSize, apply_mesh_sizing, gmsh_set_global_size, gmsh_set_region_size, resolve_region_sizes
from femforge.meshing.spec import MeshSpec, TagSelectorSpec


__all__ = [
    "MeshConfig",
    "SupportsMeshing",
    "GmshOptions",
    "MeshAlgorithm2D",
    "MeshAlgorithm3D",
    "MeshBackend",
    "MeshJob",
    "MeshReport",
    "MeshError",
    "MeshInputs",
    "mesh_inputs",
    "GmshMesher",
    "WorkerMesher",
    "block_gmsh",
    "MeshSpec",
    "TagSelectorSpec",
    "PhysicalGroup",
    "raise_on_duplicate_names",
    "format_physical_groups",
    "format_boundary_groups",
    "MeshGroupsFile",
    "Bounds",
    "BodyGroupRecord",
    "BoundaryGroupRecord",
    "BoundaryGroup",
    "BoundaryPartition",
    "partition_boundary_faces",
    "read_face_adjacency",
    "axis_entity_tags",
    "BOUNDARY_SEP",
    "EXTERNAL_SUFFIX",
    "AXIS_SUFFIX",
    "AXIS_TOLERANCE_M",
    "ResolvedRegionMeshSize",
    "resolve_region_sizes",
    "apply_mesh_sizing",
    "gmsh_set_global_size",
    "gmsh_set_region_size",
    "EntityGeometry",
    "extract_entity_geometry",
]
