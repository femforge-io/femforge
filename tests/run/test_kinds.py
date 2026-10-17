"""Run identity v2: the STEP, mesh, and bundle keys move with what changes the artifact and with nothing else."""

from pathlib import Path
from typing import Annotated

import pytest

from femforge.artifacts.keys import artifact_key
from femforge.common.frame import Axis, Axisymmetric, Solid
from femforge.common.pydantic import HashExclude
from femforge.common.tags import TagKey
from femforge.config import SimulationConfig
from femforge.elmer.config import PhysicsSelection
from femforge.elmer.physics import Physics
from femforge.geometry.config import GeometryArea
from femforge.meshing.config import MeshConfig
from femforge.meshing.gmsh_options import GmshOptions, MeshAlgorithm2D
from femforge.physical.conditions import Magnetization
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import U
from femforge.materials.properties import MaterialProperties
from femforge.common.vector import Direction
from femforge.run.kinds import ELMER_MESH, RUN_BUNDLE, STEP, RunSpec, StepSpec, cache_key, mesh_key, mesh_spec, step_digest, step_key


class _StubGeometry(GeometryArea):
    width: float = 1.0
    label: Annotated[str, HashExclude()] = "a"

    def build_step(self, path: Path) -> Path:
        _ = path.write_text(f"ISO-10303-21; width={self.width}", encoding="utf-8")
        return path


def _config(**mesh: object) -> SimulationConfig:
    return SimulationConfig(
        mesh=MeshConfig(step="data/does-not-exist.step", **mesh),  # pyright: ignore[reportArgumentType]  # test-only keyword forwarding
        domain=DomainConfig(
            materials=[MaterialProperties(name="Air", tag="Air"), MaterialProperties(name="Neodymium", tag="N42")],
            tags=[
                EntityTag(
                    selector={TagKey.MAGNETIZATION: "N"},
                    conditions=[Magnetization(direction=Direction(0, 1, 0))],
                    mesh_size=0.5 * U.mm,
                )
            ],
        ),
        physics=PhysicsSelection(selected=Physics.MAGNETOSTATICS),
    )


STEP_TEXT = "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('x'),'2;1');\nFILE_NAME('a.step','{stamp}',('me'),(''),'occ','b3d','');\nENDSEC;\nDATA;\n#1=CARTESIAN_POINT('',(0.,0.,0.));\nENDSEC;\nEND-ISO-10303-21;\n"


def test_step_digest_masks_the_export_timestamp(tmp_path: Path) -> None:
    first = tmp_path / "a.step"
    second = tmp_path / "b.step"
    _ = first.write_text(STEP_TEXT.format(stamp="2026-08-30T15:42:25"), encoding="utf-8")
    _ = second.write_text(STEP_TEXT.format(stamp="2026-09-02T21:00:00"), encoding="utf-8")
    assert step_digest(first) == step_digest(second)

    _ = second.write_text(STEP_TEXT.format(stamp="2026-09-02T21:00:00").replace("(0.,0.,0.)", "(1.,0.,0.)"), encoding="utf-8")
    assert step_digest(first) != step_digest(second)

    assert step_digest("data/missing.step") == step_digest("data/missing.step")
    assert step_digest("data/missing.step") != step_digest("data/other.step")


def test_step_kind_builds_the_geometry_into_its_workdir(tmp_path: Path) -> None:
    assert STEP.produce is not None
    produced = STEP.produce(StepSpec(source=_StubGeometry(width=4.0), frame=Solid()), tmp_path)
    assert produced.files == ("geometry.step",)
    assert (tmp_path / "geometry.step").read_text(encoding="utf-8").endswith("width=4.0")


def test_step_key_is_the_geometry_artifact_key_when_parametric() -> None:
    config = SimulationConfig(geometry=_StubGeometry(width=2.0))
    assert step_key(config) == artifact_key(STEP, StepSpec(source=_StubGeometry(width=2.0), frame=Solid()))
    assert step_key(config) == step_key(SimulationConfig(geometry=_StubGeometry(width=2.0, label="b")))
    assert step_key(config) != step_key(SimulationConfig(geometry=_StubGeometry(width=3.0)))


def test_step_key_moves_with_the_frame() -> None:
    parametric = SimulationConfig(geometry=_StubGeometry(width=2.0))
    authored = _config()
    for config in (parametric, authored):
        solid: str = step_key(config)
        about_z: str = step_key(config.model_copy(update={"frame": Axisymmetric()}))
        about_x: str = step_key(config.model_copy(update={"frame": Axisymmetric(axis=Axis.X)}))
        assert len({solid, about_z, about_x}) == 3

    assert step_key(authored) == step_key(_config())


def test_an_authored_step_key_moves_with_the_step_producer_version(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from femforge.run import kinds

    before: str = step_key(_config())
    monkeypatch.setattr(kinds, "STEP", replace(kinds.STEP, producer_version=kinds.STEP.producer_version + 1))
    assert step_key(_config()) != before


def test_an_authored_step_key_ignores_where_the_file_lives(tmp_path: Path) -> None:
    for name in ("a.step", "b.step"):
        _ = (tmp_path / name).write_text("ISO-10303-21; same bytes", encoding="utf-8")

    keys = {step_key(SimulationConfig(mesh=MeshConfig(step=str(tmp_path / name)))) for name in ("a.step", "b.step")}
    assert len(keys) == 1


def test_mesh_spec_carries_the_sizes_in_metres() -> None:
    spec = mesh_spec(_config(global_mesh_size=2 * U.mm))

    assert spec.global_mesh_size == pytest.approx(0.002)
    assert [tag.mesh_size for tag in spec.tags] == [pytest.approx(0.0005)]


def test_mesh_key_is_the_same_for_the_same_size_in_any_unit() -> None:
    assert mesh_key(_config(global_mesh_size=0.5 * U.mm)) == mesh_key(_config(global_mesh_size=0.0005 * U.m))


def test_mesh_key_moves_with_mesh_settings_and_grouping_inputs() -> None:
    base = mesh_key(_config())
    assert base == mesh_key(_config())
    assert base == artifact_key(ELMER_MESH, mesh_spec(_config()))

    assert base != mesh_key(_config(global_mesh_size=0.5 * U.mm))
    planar = _config()
    planar.frame = Axisymmetric(axis=Axis.Y)
    assert mesh_spec(planar).model_dim == 2
    assert base != mesh_key(planar)
    assert base != mesh_key(_config(deterministic=True))
    assert base != mesh_key(_config(gmsh_options=GmshOptions(algorithm_2d=MeshAlgorithm2D.DELAUNAY)))

    resized = _config()
    resized.domain.tags[0].mesh_size = 0.1 * U.mm
    assert base != mesh_key(resized)

    regrouped = _config()
    regrouped.domain.tags.append(EntityTag(selector={TagKey.MAGNETIZATION: "S"}))
    assert base != mesh_key(regrouped)

    reordered = _config()
    reordered.domain.tags.insert(0, EntityTag(selector={TagKey.PHASE: "A"}))
    reordered_other = _config()
    reordered_other.domain.tags.append(EntityTag(selector={TagKey.PHASE: "A"}))
    assert mesh_key(reordered) != mesh_key(reordered_other)

    more_materials = _config()
    more_materials.domain.materials.append(MaterialProperties(name="Copper", tag="Copper"))
    assert base != mesh_key(more_materials)


def test_mesh_key_ignores_everything_that_only_changes_the_solve() -> None:
    base = mesh_key(_config())

    threaded = _config(gmsh_options=GmshOptions(num_threads=3, terminal_output=True))
    assert base == mesh_key(threaded)

    conditions = _config()
    conditions.domain.tags[0].conditions = [Magnetization(direction=Direction(0, -1, 0))]
    assert base == mesh_key(conditions)

    properties = _config()
    properties.domain.materials[1] = MaterialProperties(name="Another Magnet", tag="N42")
    assert base == mesh_key(properties)

    physics = _config()
    physics.physics = PhysicsSelection(selected=Physics.THERMAL)
    assert base == mesh_key(physics)

    labeled = _config()
    labeled.meta.description = "a label"
    assert base == mesh_key(labeled)

    assert mesh_spec(_config()).materials == ["Air", "N42"]
    assert mesh_spec(_config(), "override").step_key == "override"


def test_cache_key_is_machine_and_path_independent() -> None:
    base = cache_key(_config())
    assert base == cache_key(_config(gmsh_options=GmshOptions(num_threads=64, terminal_output=True)))

    moved = _config()
    moved.mesh.step = "elsewhere/does-not-exist.step"
    assert base != cache_key(moved)

    labeled = _config()
    labeled.meta.description = "label"
    assert base == cache_key(labeled)

    assert base != cache_key(_config(global_mesh_size=0.5 * U.mm))
    assert artifact_key(RUN_BUNDLE, RunSpec(config=_config())) == base


def test_cache_key_of_a_parametric_run_ignores_hash_excluded_geometry_fields() -> None:
    a = cache_key(SimulationConfig(geometry=_StubGeometry(width=2.0, label="a")))
    b = cache_key(SimulationConfig(geometry=_StubGeometry(width=2.0, label="b")))
    assert a == b
    assert a != cache_key(SimulationConfig(geometry=_StubGeometry(width=2.5)))
