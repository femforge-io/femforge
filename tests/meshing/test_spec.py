"""The mesh spec keeps its artifact key and its import paths."""

from __future__ import annotations

from femforge.artifacts.keys import artifact_key
from femforge.meshing.gmsh_options import GmshOptions
from femforge.meshing.spec import MeshSpec, TagSelectorSpec
from femforge.run.kinds import ELMER_MESH

PINNED_MESH_KEY = "f70155b03f7f7e09c7b266da1bfd1ecd9f67cec8138745bd7df6b26e6f68c4e8"
"""The mesh key of the fixed spec below; it moves only when mesh identity changes on purpose, which re-meshes every cached mesh."""


def test_a_fixed_mesh_spec_keeps_its_key() -> None:
    spec = MeshSpec(
        step_key="0123456789abcdef" * 4,
        model_dim=3,
        global_mesh_size=0.002,
        gmsh_options=GmshOptions(),
        deterministic=True,
        tags=[
            TagSelectorSpec(selector={"Magnetization": "N"}, mesh_size=0.0005),
            TagSelectorSpec(selector={"Phase": "A"}),
        ],
        materials=["Air", "Copper", "N42"],
    )

    key = artifact_key(ELMER_MESH, spec)

    assert key == PINNED_MESH_KEY


def test_the_spec_names_keep_their_import_paths() -> None:
    import femforge.meshing
    import femforge.run
    from femforge.run import kinds

    assert kinds.MeshSpec is MeshSpec and kinds.TagSelectorSpec is TagSelectorSpec
    assert femforge.run.MeshSpec is MeshSpec
    assert femforge.meshing.MeshSpec is MeshSpec and femforge.meshing.TagSelectorSpec is TagSelectorSpec
