"""Pure logic for meshing.mesher: the gmsh-free Mesher constructor and STEP header checks; `generate()` is proven by the gated integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from femforge.frames import Axisymmetric
from femforge.common.tags import TagKey
from femforge.config import SimulationConfig
from femforge.materials.neodymium import N52
from femforge.meshing import mesher as mesher_module
from femforge.meshing.config import MeshConfig
from femforge.meshing.groups import PhysicalGroup, format_boundary_groups, format_physical_groups, raise_on_duplicate_names
from femforge.meshing.mesher import (
    BUILD123D_ORIGINATING_SYSTEM,
    PLAUSIBLE_MAX_EXTENT_M,
    Mesher,
    oversized_extent_warning,
    step_originating_system,
)
from femforge.meshing.naming import MAX_SAFE_GROUP_NAME, compose_group_name
from femforge.physical.domain import EntityTag
from femforge.physical.units import U


def test_the_group_helpers_keep_their_mesher_import_path() -> None:
    assert mesher_module.PhysicalGroup is PhysicalGroup
    assert mesher_module.raise_on_duplicate_names is raise_on_duplicate_names
    assert mesher_module.format_physical_groups is format_physical_groups
    assert mesher_module.format_boundary_groups is format_boundary_groups


def test_mesher_constructor_is_gmsh_free_and_derives_paths() -> None:
    # The constructor only stores the config and derives output paths (no gmsh).
    mesher: Mesher = Mesher(SimulationConfig(mesh=MeshConfig(step="data/geometry.step")))
    assert mesher.input_filename == "data/geometry.step"
    assert mesher.output_filename == "data/geometry.msh"
    assert mesher.name == "geometry"
    # The fully-defaulted config is exposed for inspection/modification before generate().
    assert mesher.config.mesh.global_mesh_size == 1 * U.mm


def test_the_msh_goes_where_asked_or_else_beside_the_step(tmp_path: Path) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/geometry.step"))
    target = tmp_path / "run" / "geometry.msh"

    assert Mesher(config).output_filename == "data/geometry.msh"
    assert Mesher(config, output=target).output_filename == str(target)


def test_body_and_boundary_dims_follow_the_frame() -> None:
    solid: Mesher = Mesher(SimulationConfig())
    assert (solid.body_dim, solid.boundary_dim) == (3, 2)
    planar: Mesher = Mesher(SimulationConfig(frame=Axisymmetric()))
    assert (planar.body_dim, planar.boundary_dim) == (2, 1)


def test_element_count_is_dim_agnostic_and_the_3d_alias_is_3d_only() -> None:
    # generate() (gmsh, gated) sets the backing count; simulate it to cover both readers.
    solid: Mesher = Mesher(SimulationConfig())
    assert solid.element_count is None and solid.element_count_3D is None
    solid._element_count = 42
    assert solid.element_count == 42
    assert solid.element_count_3D == 42
    planar: Mesher = Mesher(SimulationConfig(frame=Axisymmetric()))
    planar._element_count = 7
    assert planar.element_count == 7
    # The legacy name counts tetrahedra, so a 2D run reports None rather than a misleading number.
    assert planar.element_count_3D is None


def test_oversized_extent_warning_flags_likely_unit_slip() -> None:
    # A metre-scale model is fine; a domain spanning tens of metres is almost certainly mm treated as m.
    assert oversized_extent_warning((0.05, 0.04, 0.03)) is None
    warning: str | None = oversized_extent_warning((50.0, 40.0, 30.0))
    assert warning is not None and "implausibly large" in warning
    # Boundary: exactly at the limit is allowed; just past it warns.
    assert oversized_extent_warning((PLAUSIBLE_MAX_EXTENT_M, 0.0, 0.0)) is None
    assert oversized_extent_warning((PLAUSIBLE_MAX_EXTENT_M + 0.1, 0.0, 0.0)) is not None


def _write_step(path: Path, file_name: str) -> Path:
    path.write_text(f"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('x'),'2;1');\n{file_name}\nENDSEC;\nDATA;\n#1=CARTESIAN_POINT('',(0.,0.,0.));\nENDSEC;\nEND-ISO-10303-21;\n")
    return path


def test_step_originating_system_reads_the_build123d_header(tmp_path: Path) -> None:
    header = "FILE_NAME('Open CASCADE Shape Model','2026-10-09T22:37:11',('Author'),(\n    'Open CASCADE'),'Open CASCADE STEP processor 8.0','build123d',\n  'Unknown');"
    assert step_originating_system(_write_step(tmp_path / "b3d.step", header)) == BUILD123D_ORIGINATING_SYSTEM


def test_step_originating_system_skips_lists_and_unescapes_quotes(tmp_path: Path) -> None:
    header = "FILE_NAME('a','t',('x, y','(z)'),(''),'occ','Bob''s CAD','');"
    assert step_originating_system(_write_step(tmp_path / "other.step", header)) == "Bob's CAD"


def test_step_originating_system_is_none_without_one(tmp_path: Path) -> None:
    assert step_originating_system(_write_step(tmp_path / "unset.step", "FILE_NAME('a','t',('x'),(''),'occ',$,'');")) is None
    assert step_originating_system(_write_step(tmp_path / "short.step", "FILE_NAME('a','t');")) is None
    assert step_originating_system(_write_step(tmp_path / "absent.step", "FILE_SCHEMA(('AP214'));")) is None


def test_groups_raise_before_generate() -> None:
    # Accessing the groups before generate() is a clear error rather than a confusing empty result.
    mesher: Mesher = Mesher(SimulationConfig())
    with pytest.raises(RuntimeError, match="generate"):
        _ = mesher.physical_groups
    with pytest.raises(RuntimeError, match="generate"):
        _ = mesher.boundary_groups


def test_groups_available_after_generate_populates_them() -> None:
    # generate() (gmsh, gated) sets the backing fields; simulate that to cover the populated path.
    mesher: Mesher = Mesher(SimulationConfig())
    group: PhysicalGroup = PhysicalGroup(dim=3, gmsh_id=1, name="N52_MAG_N", material=N52(), tags=[], entity_tags=[1])
    mesher._physical_groups = [group]
    mesher._boundary_groups = []
    assert mesher.physical_groups == [group]
    assert mesher.boundary_groups == []


def test_compose_group_name_warns_past_the_elmer_name_headroom() -> None:
    # Elmer reads names into 128-char buffers and boundary names join two body names, so a single body name
    # past MAX_SAFE_GROUP_NAME chars gets a warning through the injected callback (the single naming seam).
    warnings: list[str] = []
    short = compose_group_name(N52(), [EntityTag(selector={TagKey.MAGNETIZATION: "N"})], warnings.append)
    assert short == "N52_MAGNETIZATIONN"
    assert not warnings
    long_tag = EntityTag(selector={TagKey.PHASE: "X" * (MAX_SAFE_GROUP_NAME + 1)})
    long = compose_group_name(N52(), [long_tag], warnings.append)
    assert len(long) > MAX_SAFE_GROUP_NAME
    assert warnings and "MAX_NAME_LEN" in warnings[0]


def test_compose_group_name_takes_the_authored_name_over_material_and_tags() -> None:
    warnings: list[str] = []
    tags = [EntityTag(selector={TagKey.MAGNETIZATION: "N"})]
    assert compose_group_name(N52(), tags, warnings.append, authored="Seat") == "SEAT"
    assert compose_group_name(N52(), tags, warnings.append, authored=None) == "N52_MAGNETIZATIONN"
    assert not warnings
