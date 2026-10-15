"""End-to-end (needs gmsh) that sizing changes the mesh: finer global size -> more elements, and a per-region
override refines only that region. Gated with importorskip; decision logic is covered in test_mesh_sizing.py.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gmsh")

import gmsh  # imported after the gate so the file still collects where gmsh is absent

from femforge.meshing.mesh_sizing import ResolvedRegionMeshSize, apply_mesh_sizing, gmsh_set_global_size, gmsh_set_region_size


def _element_count() -> int:
    return sum(len(tags) for tags in gmsh.model.mesh.getElements(dim=3)[1])


def _mesh_two_boxes(global_size: float, region_sizes: list[ResolvedRegionMeshSize]) -> int:
    """Mesh two disjoint 10mm boxes (volumes 1, 2) at the given sizes and return the 3D element count."""
    if gmsh.isInitialized():
        gmsh.clear()
    else:
        gmsh.initialize(interruptible=False)
    gmsh.option.setNumber("General.Terminal", 0)
    # Neutralize any MeshSizeMax/Min clamp leaked from earlier tests (clear() preserves options) so per-point sizes drive the mesh.
    gmsh.option.setNumber("Mesh.MeshSizeMax", 1e22)
    gmsh.option.setNumber("Mesh.MeshSizeMin", 0.0)
    _ = gmsh.model.occ.addBox(0, 0, 0, 10, 10, 10)
    _ = gmsh.model.occ.addBox(20, 0, 0, 10, 10, 10)
    gmsh.model.occ.synchronize()
    apply_mesh_sizing(global_size, region_sizes, set_global=gmsh_set_global_size, set_region=gmsh_set_region_size)
    gmsh.model.mesh.generate(3)
    return _element_count()


def test_finer_global_size_yields_more_elements() -> None:
    coarse: int = _mesh_two_boxes(8.0, [])
    fine: int = _mesh_two_boxes(2.0, [])
    assert fine > coarse


def test_region_override_refines_only_that_region() -> None:
    # Same coarse global on both boxes; refining only volume 2 must raise the total element count.
    uniform: int = _mesh_two_boxes(8.0, [])
    refined: int = _mesh_two_boxes(8.0, [ResolvedRegionMeshSize(volume_tags=(2,), size=2.0)])
    assert refined > uniform
