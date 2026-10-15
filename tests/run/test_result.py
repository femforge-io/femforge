"""Typed Result round-trips through model_dump / model_validate with pint quantities preserved, in both python and JSON form."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from femforge.common.frame import Axis, Axisymmetric, Solid
from femforge.common.vector import Direction, Vec3
from femforge.physical.units import U, quantity
from femforge.common.files import BundleFile
from femforge.run.result import Result


def _result() -> Result:
    return Result(
        frame=Solid(),
        status="ok",
        force=quantity(Vec3(1.0, 2.0, 3.0), U.N),
        torque=quantity(Vec3(0.0, 0.0, 0.5), U.N * U.m),
        flux_density_max=1.2 * U.T,
        residual=1e-8,
        wall_time=3.0,
        raw={"some scalar": 42.0},
    )


def test_result_round_trips_in_python_mode() -> None:
    restored = Result.model_validate(_result().model_dump())

    assert restored.force is not None and restored.torque is not None and restored.flux_density_max is not None
    assert restored.force.check("[force]")
    assert restored.force.magnitude.x == pytest.approx(1.0)
    assert restored.force.magnitude.z == pytest.approx(3.0)
    assert restored.torque.magnitude.z == pytest.approx(0.5)
    assert restored.flux_density_max.to(U.T).magnitude == pytest.approx(1.2)
    assert restored.residual == pytest.approx(1e-8)
    assert restored.raw["some scalar"] == pytest.approx(42.0)


def test_result_round_trips_through_json() -> None:
    restored = Result.model_validate_json(_result().model_dump_json())
    assert restored.force is not None and restored.torque is not None and restored.flux_density_max is not None
    assert restored.force.magnitude.y == pytest.approx(2.0)
    assert restored.torque.check("[force]*[length]")
    assert restored.flux_density_max.to(U.T).magnitude == pytest.approx(1.2)


def test_optional_quantities_default_to_none() -> None:
    bare = Result(frame=Solid(), status="error")
    assert bare.force is None and bare.torque is None and bare.flux_density_max is None
    assert Result.model_validate_json(bare.model_dump_json()).status == "error"


def test_force_rejects_wrong_dimensionality() -> None:
    # A scalar or wrong-dimension quantity must not validate as a force.
    with pytest.raises(ValueError):
        Result(frame=Solid(), status="ok", force=5.0 * U.T)  # pyright: ignore[reportArgumentType]  # deliberately a scalar quantity, must be rejected at validation


def test_component_forces_round_trip_through_json() -> None:
    result = Result(
        frame=Solid(),
        status="ok",
        component_forces={"N42_MAGNETIZATIONN": quantity(Vec3(0.0, 0.0, 4.2), U.N)},
        component_torques={"N42_MAGNETIZATIONN": quantity(Vec3(0.0, 0.1, 0.0), U.N * U.m)},
    )
    restored = Result.model_validate_json(result.model_dump_json())
    force = restored.component_forces["N42_MAGNETIZATIONN"]
    assert force.check("[force]")
    assert force.magnitude.z == pytest.approx(4.2)
    assert restored.component_torques["N42_MAGNETIZATIONN"].check("[force]*[length]")


def test_component_maps_default_to_empty() -> None:
    restored = Result.model_validate_json('{"status": "ok", "frame": {"kind": "solid"}}')
    assert restored.component_forces == {}
    assert restored.component_torques == {}


def test_a_result_without_a_frame_fails_to_load_rather_than_loading_as_solid() -> None:
    # A pre-frame 2D bundle carried an `axisymmetric` marker; reading it as Solid would drop the 2*pi revolution.
    with pytest.raises(ValidationError) as caught:
        Result.model_validate_json('{"status": "ok", "axisymmetric": true}')

    assert {error["loc"][0] for error in caught.value.errors()} == {"frame", "axisymmetric"}


def test_frame_round_trips() -> None:
    frame = Axisymmetric(axis=Axis(origin=Vec3(1, 2, 3), direction=Direction(1, 0, 0)))
    restored = Result.model_validate_json(Result(status="ok", frame=frame).model_dump_json())
    assert isinstance(restored.frame, Axisymmetric)
    assert restored.frame.axis.direction.to_list() == [1, 0, 0]
    assert restored.frame.axis.origin.to_list() == [1, 2, 3]


MINUS_Z = Axis(direction=Direction(0, 0, -1))


def test_axial_force_of_a_solid_run_projects_onto_the_given_axis() -> None:
    force = quantity(Vec3(3.0, 4.0, -2.0), U.N)
    result = Result(frame=Solid(), status="ok", component_forces={"MAGNET": force})
    assert result.axial_force("MAGNET", axis=Axis.Z).m_as("newton") == pytest.approx(-2.0)
    assert result.axial_force("MAGNET", axis=MINUS_Z).m_as("newton") == pytest.approx(2.0)
    assert result.axial_force("MAGNET", axis=Axis.X).m_as("newton") == pytest.approx(3.0)

    with pytest.raises(ValueError, match="no symmetry axis"):
        _ = result.axial_force("MAGNET")


def test_axial_force_of_an_axisymmetric_run_reads_solve_y_along_the_symmetry_axis() -> None:
    force = quantity(Vec3(0.0, 7.5, 0.0), U.N)
    result = Result(frame=Axisymmetric(), status="ok", component_forces={"MAGNET": force})
    assert result.axial_force("MAGNET").m_as("newton") == pytest.approx(7.5)
    assert result.axial_force("MAGNET", axis=Axis.Z).m_as("newton") == pytest.approx(7.5)
    assert result.axial_force("MAGNET", axis=MINUS_Z).m_as("newton") == pytest.approx(-7.5)

    with pytest.raises(ValueError, match="only along its symmetry axis"):
        _ = result.axial_force("MAGNET", axis=Axis.X)


def test_load_from_bundle_dir_stamps_run_dir(tmp_path: Path) -> None:
    (tmp_path / BundleFile.RESULT).write_text(_result().model_dump_json(), encoding="utf-8")
    loaded = Result.load(tmp_path)
    assert loaded.run_dir == tmp_path
    assert loaded.raw["some scalar"] == pytest.approx(42.0)


def test_load_accepts_the_file_itself(tmp_path: Path) -> None:
    file = tmp_path / "elsewhere.json"
    file.write_text(_result().model_dump_json(), encoding="utf-8")
    assert Result.load(file).run_dir == tmp_path


def test_load_missing_file_names_what_it_looked_for(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="result.json"):
        Result.load(tmp_path)
