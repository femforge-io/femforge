"""run.magnetic: per-component force/torque triples from Result.raw, the components.yml join, and the energy/eddy scalars."""

from pathlib import Path

import pytest

from femforge.common.vector import Vec3
from femforge.run.magnetic import component_forces, component_torques, eddy_current_power, force_triples, magnetic_field_energy, read_component_names


RAW: dict[str, float] = {
    "res: nodal force 1": 12.5,
    "res: nodal force 2": -3.4,
    "res: nodal force 3": 0.27,
    "res: magnet force 1": 1.0,
    "res: magnet force 2": 2.0,
    "res: magnet force 3": 3.0,
    "res: component 2 force 1": 4.0,
    "res: component 2 force 2": 5.0,  # no index-3 column: the missing component defaults to 0.0
    "res: nodal torque 3": 9.9,
    "res: magnet torque 1": 0.5,
    "res: magnet torque 2": 0.6,
    "res: magnet torque 3": 0.7,
    "res: magnetic field energy": 42.0,
    "res: eddy current power": 0.001,
    "time": 1.0,  # no trailing component index -> never a triple
}


def _xyz(vec: Vec3) -> tuple[float, float, float]:
    return (vec.x, vec.y, vec.z)


def test_force_triples_excludes_the_nodal_sum() -> None:
    triples = force_triples(RAW)
    assert set(triples) == {"res: magnet force", "res: component 2 force"}
    assert _xyz(triples["res: magnet force"]) == pytest.approx((1.0, 2.0, 3.0))
    assert _xyz(triples["res: component 2 force"]) == pytest.approx((4.0, 5.0, 0.0))


def test_component_forces_join_by_name_or_component_id() -> None:
    forces = component_forces(RAW, {1: "MAGNET", 2: "SPACER"})
    # MAGNET matches its name in the stem; SPACER's stem carries only "component 2".
    assert set(forces) == {"MAGNET", "SPACER"}
    assert _xyz(forces["MAGNET"]) == pytest.approx((1.0, 2.0, 3.0))
    assert _xyz(forces["SPACER"]) == pytest.approx((4.0, 5.0, 0.0))


def test_component_with_no_matching_column_is_dropped() -> None:
    assert component_forces(RAW, {7: "NOPE"}) == {}


def test_component_torques_join_like_forces() -> None:
    torques = component_torques(RAW, {1: "MAGNET"})
    assert set(torques) == {"MAGNET"}
    assert _xyz(torques["MAGNET"]) == pytest.approx((0.5, 0.6, 0.7))


def test_energy_and_eddy_scalars() -> None:
    assert magnetic_field_energy(RAW) == pytest.approx(42.0)
    assert eddy_current_power(RAW) == pytest.approx(0.001)


def test_empty_raw_yields_empty_or_none() -> None:
    assert force_triples({}) == {}
    assert component_forces({}, {1: "MAGNET"}) == {}
    assert component_torques({}, {1: "MAGNET"}) == {}
    assert magnetic_field_energy({}) is None
    assert eddy_current_power({}) is None


def test_read_component_names_parses_the_flat_yaml(tmp_path: Path) -> None:
    yml = tmp_path / "components.yml"
    _ = yml.write_text("1: MAGNET\n2: SPACER\n")
    assert read_component_names(yml) == {1: "MAGNET", 2: "SPACER"}
