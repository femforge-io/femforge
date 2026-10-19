"""Run identity v2: the STEP, mesh, and bundle keys move with what changes the artifact and with nothing else."""

from pathlib import Path
from typing import Annotated, cast

import pytest

from femforge.artifacts.keys import artifact_key
from femforge.frames import Axis, Axisymmetric, Cartesian2D, PlaneState, Solid
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
from femforge.physical.units import Quantity, U, quantity
from femforge.materials.properties import MaterialProperties
from femforge.common.vector import Direction, Vec3
from femforge.geometry import Build123dGeometry
from femforge.geometry.tags import TaggedCompound, TaggedFace, TaggedPart
from femforge.run.kinds import ELMER_MESH, RUN_BUNDLE, STEP, RunSpec, StepSpec, cache_key, mesh_key, mesh_spec, run_spec_payload, step_digest, step_key, step_spec


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


def _bracket(face_name: str = "Left", face_color: str = "red", shift: float = 0.0, material: str = "Steel316") -> TaggedCompound:
    import build123d as b3d

    box = b3d.Box(20, 4, 6)
    face = TaggedFace(box.faces().sort_by(b3d.Axis.X)[0], name=face_name)
    face.color = b3d.Color(face_color)
    block = TaggedPart([b3d.Box(4, 4, 4)], tags={TagKey.MATERIAL: "Air"}, name="Block")
    block.location = b3d.Location((shift, 0, 10))
    bar = TaggedPart([box], tags={TagKey.MATERIAL: material}, name="Bar")
    bar.color = b3d.Color("gray")
    return TaggedCompound(children=[bar, face, block])


def test_step_digest_is_the_same_for_every_export_of_one_model(tmp_path: Path) -> None:
    model = _bracket()
    digests: set[str] = set()
    for name in ("a.step", "b.step"):
        model.export_step(str(tmp_path / name))
        digests.add(step_digest(tmp_path / name))

    assert digests == {model.fingerprint()}


def test_step_digest_moves_with_names_colors_placement_and_tags(tmp_path: Path) -> None:
    variants = (_bracket(), _bracket(face_name="Right"), _bracket(face_color="blue"), _bracket(shift=1.0), _bracket(material="Steel1080"))
    digests: set[str] = set()
    for variant in variants:
        variant.export_step(str(tmp_path / "model.step"))
        digests.add(step_digest(tmp_path / "model.step"))

    assert len(digests) == len(variants)


def test_step_digest_moves_with_a_resized_magnet_in_the_same_air(tmp_path: Path) -> None:
    import build123d as b3d

    def export(radius: float, height: float) -> str:
        magnet = TaggedPart([b3d.Cylinder(radius, height)], tags={TagKey.MATERIAL: "N52"}, name="Magnet")
        air = TaggedPart([b3d.Box(100, 100, 100)], tags={TagKey.MATERIAL: "Air"}, name="Air")
        step = tmp_path / f"magnet_{radius}.step"
        TaggedCompound(children=[magnet, air]).export_step(str(step))
        return step_digest(step)

    assert export(5.0, 2.0) != export(6.0, 2.4)


def test_step_digest_of_a_missing_file_hashes_its_path() -> None:
    assert step_digest("data/missing.step") == step_digest("data/missing.step")
    assert step_digest("data/missing.step") != step_digest("data/other.step")


def test_step_digest_of_a_step_that_does_not_import_masks_its_timestamp_and_warns(tmp_path: Path) -> None:
    from femforge.common.log import log

    first = tmp_path / "a.step"
    second = tmp_path / "b.step"
    _ = first.write_text(STEP_TEXT.format(stamp="2026-08-30T15:42:25"), encoding="utf-8")
    _ = second.write_text(STEP_TEXT.format(stamp="2026-09-02T21:00:00"), encoding="utf-8")
    warnings: list[str] = []
    sink_id: int = log.add(warnings.append, level="WARNING", format="{message} | {extra}")

    try:
        assert step_digest(first) == step_digest(second)
    finally:
        log.remove(sink_id)

    assert len(warnings) == 2
    assert "did not import" in warnings[0]
    assert "a.step" in warnings[0]

    _ = second.write_text(STEP_TEXT.format(stamp="2026-09-02T21:00:00").replace("(0.,0.,0.)", "(1.,0.,0.)"), encoding="utf-8")
    assert step_digest(first) != step_digest(second)


def test_step_kind_builds_the_geometry_into_its_workdir(tmp_path: Path) -> None:
    assert STEP.produce is not None
    produced = STEP.produce(StepSpec(source=_StubGeometry(width=4.0), frame=Solid()), tmp_path)
    assert produced.files == ("geometry.step",)
    assert (tmp_path / "geometry.step").read_text(encoding="utf-8").endswith("width=4.0")


class _NamedBar(Build123dGeometry):
    """A steel bar with its -X end face named Left."""

    def model(self) -> TaggedCompound:
        import build123d as b3d

        box = b3d.Box(20, 4, 6)
        bar = TaggedPart([box], tags={TagKey.MATERIAL: "Steel316"}, name="Bar")
        bar.color = b3d.Color("gray")
        return TaggedCompound(children=[bar, TaggedFace(box.faces().sort_by(b3d.Axis.X)[0], name="Left")])


def test_step_kind_keeps_the_edge_file_and_the_geometry_check_of_a_section(tmp_path: Path) -> None:
    assert STEP.produce is not None
    frame = Cartesian2D(normal=Axis(origin=Vec3(0, 0, 0), direction=Direction(0, 0, 1)), thickness=6 * U.mm)

    produced = STEP.produce(StepSpec(source=_NamedBar(), frame=frame), tmp_path)

    assert produced.files == ("geometry.step", "geometry.boundaries.json", "geometry.check.json", "geometry.authored.vtp")


def test_a_cartesian_2d_step_is_always_rebuilt_and_keys_on_its_thickness_and_state() -> None:
    planar = Cartesian2D(thickness=10 * U.mm)
    authored = _config()

    assert not step_spec(authored.model_copy(update={"frame": planar})).passes_through

    keys = {
        step_key(authored.model_copy(update={"frame": frame}))
        for frame in (planar, Cartesian2D(thickness=20 * U.mm), Cartesian2D(thickness=10 * U.mm, state=PlaneState.STRAIN), Axisymmetric())
    }
    assert len(keys) == 4


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
    assert mesh_key(_config(global_mesh_size=2.5 * U.um)) == mesh_key(_config(global_mesh_size=2.5e-6 * U.m))


def test_mesh_key_is_the_same_for_the_same_clamps_in_any_unit() -> None:
    in_millimetres = GmshOptions(mesh_size_min=0.2 * U.mm, mesh_size_max=5 * U.mm)
    in_metres = GmshOptions(mesh_size_min=0.0002 * U.m, mesh_size_max=0.005 * U.m)

    assert mesh_key(_config(gmsh_options=in_millimetres)) == mesh_key(_config(gmsh_options=in_metres))
    assert mesh_key(_config(gmsh_options=in_millimetres)) != mesh_key(_config())
    assert mesh_spec(_config(gmsh_options=in_millimetres)).gmsh_options.mesh_size_min == 0.0002 * U.m


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


def test_cache_key_is_the_same_for_the_same_quantity_in_any_unit() -> None:
    spellings: list[Quantity] = [0.5 * U.mm, 0.0005 * U.m, 500 * U.um]
    keys: set[str] = {cache_key(_config(global_mesh_size=size)) for size in spellings}

    assert len(keys) == 1
    assert cache_key(_config(global_mesh_size=0.6 * U.mm)) not in keys


def test_the_run_payload_hashes_base_units_while_the_dump_keeps_the_spelling() -> None:
    config = _config(global_mesh_size=0.5 * U.mm)
    hashed = cast("dict[str, dict[str, object]]", run_spec_payload(config)["config"])
    dumped = cast("dict[str, dict[str, object]]", config.model_dump())

    assert hashed["mesh"]["global_mesh_size"] == "0.0005 m"
    assert dumped["mesh"]["global_mesh_size"] == "0.5 mm"


def test_cache_key_reads_the_operating_point_in_base_units() -> None:
    def at(temperature: Quantity) -> str:
        config = _config()
        config.customize_physics().operating_point.temperature = temperature
        return cache_key(config)

    assert at(quantity(26.85, "degC")) == cache_key(_config())
    assert at(quantity(30, "degC")) != cache_key(_config())
