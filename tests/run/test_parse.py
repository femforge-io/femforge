"""Result parser turns a finished Elmer run directory into a typed Result, tested against a committed fixture (no live solver)."""

import math
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from femforge.frames import Axis, Axisymmetric, Cartesian2D, FrameUnion, Solid
from femforge.config import SimulationConfig
from femforge.elmer.config import CoordinateSystem
from femforge.meshing.config import MeshConfig
from femforge.physical.units import U
from femforge.run.manifest import RunManifest
from femforge.run.parse import parse_result, result_vtu

FIXTURES: Path = Path(__file__).parent / "fixtures"


def test_result_vtu_prefers_the_partitioned_master(tmp_path: Path) -> None:
    # A partitioned run's per-rank pieces match *.vtu and sort BEFORE the serial name — the .pvtu master must win.
    for name in ("case_4np1_t0001.vtu", "case_4np2_t0001.vtu", "case_t0001.vtu", "case_t0001.pvtu"):
        (tmp_path / name).touch()
    found = result_vtu(tmp_path)
    assert found is not None and found.name == "case_t0001.pvtu"


def test_result_vtu_serial_and_empty(tmp_path: Path) -> None:
    assert result_vtu(tmp_path) is None
    (tmp_path / "case_t0001.vtu").touch()
    found = result_vtu(tmp_path)
    assert found is not None and found.name == "case_t0001.vtu"


def test_parse_fixture_yields_expected_force_field_and_residual() -> None:
    result = parse_result(FIXTURES)

    assert result.status == "ok"

    # Net force reassembled from the three nodal-force component columns as a pint vector in newtons.
    assert result.force is not None and result.force.check("[force]")
    assert result.force.magnitude.x == pytest.approx(12.5)
    assert result.force.magnitude.y == pytest.approx(-3.4)
    assert result.force.magnitude.z == pytest.approx(0.27)

    # Peak flux density column maps to Tesla.
    assert result.flux_density_max is not None
    assert result.flux_density_max.to(U.T).magnitude == pytest.approx(1.483)

    # Final RELC from the last ComputeChange line; REAL time from SOLVER TOTAL TIME.
    assert result.residual == pytest.approx(7.240e-9)
    assert result.wall_time == pytest.approx(5.12)

    # The solver version from the log banner reaches the Result (and from there the manifest).
    assert result.elmer_version == "9.0"

    # No torque columns in this fixture, so the field stays None.
    assert result.torque is None

    # Every scalar column is preserved in raw.
    assert result.raw["time"] == pytest.approx(1.0)


def test_parse_force_and_torque_narrow_to_the_nodal_sums(tmp_path: Path) -> None:
    # Per-component force triples must never silently absorb into Result.force (they used to overwrite each other).
    (tmp_path / "case.dat").write_text("12.5 -3.4 0.27 1.0 2.0 3.0 4.0 5.0 6.0 9.9\n", encoding="utf-8")
    names = [
        "res: nodal force 1",
        "res: nodal force 2",
        "res: nodal force 3",
        "res: magnet force 1",
        "res: magnet force 2",
        "res: magnet force 3",
        "res: component 2 force 1",
        "res: component 2 force 2",
        "res: component 2 force 3",
        "res: nodal torque 3",
    ]
    (tmp_path / "case.dat.names").write_text(
        "Variables in columns of matrix:\n" + "".join(f"{i}: {name}\n" for i, name in enumerate(names, 1)),
        encoding="utf-8",
    )
    (tmp_path / "elmersolver.log").write_text("*** Elmer Solver: ALL DONE ***\n", encoding="utf-8")

    result = parse_result(tmp_path)
    assert result.force is not None
    assert (result.force.magnitude.x, result.force.magnitude.y, result.force.magnitude.z) == pytest.approx((12.5, -3.4, 0.27))
    assert result.torque is not None
    assert (result.torque.magnitude.x, result.torque.magnitude.y, result.torque.magnitude.z) == pytest.approx((0.0, 0.0, 9.9))
    # The component triples still ride along in raw for run.magnetic.
    assert result.raw["res: magnet force 2"] == pytest.approx(2.0)
    # Without a components.yml there is nothing to join the triples against.
    assert result.component_forces == {}
    assert result.component_torques == {}


def test_parse_joins_component_forces_against_components_yml(tmp_path: Path) -> None:
    # Named and "component <id>" column stems both join to the components.yml names as typed pint vectors.
    (tmp_path / "case.dat").write_text("1.0 2.0 3.0 4.0 5.0 6.0 7.0 8.0 9.0\n", encoding="utf-8")
    names = [
        "res: n42_magnetizationn force 1",
        "res: n42_magnetizationn force 2",
        "res: n42_magnetizationn force 3",
        "res: component 2 force 1",
        "res: component 2 force 2",
        "res: component 2 force 3",
        "res: n42_magnetizationn torque 1",
        "res: n42_magnetizationn torque 2",
        "res: n42_magnetizationn torque 3",
    ]
    (tmp_path / "case.dat.names").write_text(
        "Variables in columns of matrix:\n" + "".join(f"{i}: {name}\n" for i, name in enumerate(names, 1)),
        encoding="utf-8",
    )
    (tmp_path / "elmersolver.log").write_text("*** Elmer Solver: ALL DONE ***\n", encoding="utf-8")
    (tmp_path / "components.yml").write_text("1: N42_MAGNETIZATIONN\n2: N42_MAGNETIZATIONS\n", encoding="utf-8")

    result = parse_result(tmp_path)

    named = result.component_forces["N42_MAGNETIZATIONN"]
    assert named.check("[force]")
    assert (named.magnitude.x, named.magnitude.y, named.magnitude.z) == pytest.approx((1.0, 2.0, 3.0))

    by_index = result.component_forces["N42_MAGNETIZATIONS"]
    assert (by_index.magnitude.x, by_index.magnitude.y, by_index.magnitude.z) == pytest.approx((4.0, 5.0, 6.0))

    torque = result.component_torques["N42_MAGNETIZATIONN"]
    assert torque.check("[force]*[length]")
    assert (torque.magnitude.x, torque.magnitude.y, torque.magnitude.z) == pytest.approx((7.0, 8.0, 9.0))
    assert "N42_MAGNETIZATIONS" not in result.component_torques


def test_parse_max_temperature_column_maps_to_kelvin(tmp_path: Path) -> None:
    # The coupled joule-heating stack exports "max: temperature" (SaveScalars operator 3) -> Result.temperature_max.
    (tmp_path / "scalars.dat").write_text("309.93\n", encoding="utf-8")
    (tmp_path / "scalars.dat.names").write_text(
        "Variables in columns of matrix:\n1: max: temperature\n",
        encoding="utf-8",
    )
    (tmp_path / "elmersolver.log").write_text("*** Elmer Solver: ALL DONE ***\n", encoding="utf-8")
    result = parse_result(tmp_path)
    assert result.status == "ok"
    assert result.temperature_max is not None
    assert result.temperature_max.to(U.K).magnitude == pytest.approx(309.93)


def test_per_radian_marks_only_the_2d_symmetric_coordinate_systems() -> None:
    assert CoordinateSystem.AXI_SYMMETRIC.per_radian
    assert CoordinateSystem.CYLINDRIC_SYMMETRIC.per_radian
    assert not CoordinateSystem.CARTESIAN.per_radian
    assert not CoordinateSystem.CYLINDRIC.per_radian


def _write_axisym_outputs(tmp_path: Path) -> None:
    (tmp_path / "case.dat").write_text("1.5 -2.0 4.0 5.0\n", encoding="utf-8")
    names = [
        "res: nodal force 1",
        "res: nodal force 2",
        "res: n42_magnetizationn force 1",
        "res: n42_magnetizationn force 2",
    ]
    (tmp_path / "case.dat.names").write_text(
        "Variables in columns of matrix:\n" + "".join(f"{i}: {name}\n" for i, name in enumerate(names, 1)),
        encoding="utf-8",
    )
    (tmp_path / "elmersolver.log").write_text("*** Elmer Solver: ALL DONE ***\n", encoding="utf-8")
    (tmp_path / "components.yml").write_text("1: N42_MAGNETIZATIONN\n", encoding="utf-8")


def _write_manifest(tmp_path: Path, frame: FrameUnion) -> None:
    config = SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step"), frame=frame)
    manifest = RunManifest.build(config, status="ok", code_sha="sha1", created_at=datetime(2026, 6, 14, tzinfo=timezone.utc))
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")


def test_parse_axisymmetric_revolves_the_typed_vectors(tmp_path: Path) -> None:
    # Per-radian Elmer integrals become full-revolution totals: axial (y) x2*pi, transverse zeroed; raw stays verbatim.
    _write_axisym_outputs(tmp_path)
    result = parse_result(tmp_path, frame=Axisymmetric())

    assert isinstance(result.frame, Axisymmetric)
    assert result.force is not None
    assert result.force.magnitude.x == 0.0
    assert result.force.magnitude.y == pytest.approx(-2.0 * math.tau)

    magnet = result.component_forces["N42_MAGNETIZATIONN"]
    assert magnet.magnitude.x == 0.0
    assert magnet.magnitude.y == pytest.approx(5.0 * math.tau)

    assert result.raw["res: nodal force 1"] == pytest.approx(1.5)
    assert result.raw["res: n42_magnetizationn force 2"] == pytest.approx(5.0)


def test_parse_cartesian_2d_scales_the_per_metre_vectors_by_the_thickness(tmp_path: Path) -> None:
    _write_axisym_outputs(tmp_path)
    result = parse_result(tmp_path, frame=Cartesian2D(thickness=10 * U.mm))

    assert isinstance(result.frame, Cartesian2D)
    assert result.force is not None
    assert result.force.magnitude.to_list() == pytest.approx([0.015, -0.02, 0.0])
    assert result.component_forces["N42_MAGNETIZATIONN"].magnitude.y == pytest.approx(0.05)
    assert result.raw["res: n42_magnetizationn force 2"] == pytest.approx(5.0)


def test_parse_reads_the_frame_from_the_manifest(tmp_path: Path) -> None:
    _write_axisym_outputs(tmp_path)
    _write_manifest(tmp_path, Axisymmetric(axis=Axis.X))

    result = parse_result(tmp_path)
    assert isinstance(result.frame, Axisymmetric)
    assert result.frame.axis.direction.to_list() == Axis.X.direction.to_list()
    assert result.force is not None
    assert result.force.magnitude.y == pytest.approx(-2.0 * math.tau)


def test_parse_3d_manifest_leaves_newtons_alone(tmp_path: Path) -> None:
    _write_axisym_outputs(tmp_path)
    _write_manifest(tmp_path, Solid())

    result = parse_result(tmp_path)
    assert isinstance(result.frame, Solid)
    assert result.force is not None
    assert result.force.magnitude.x == pytest.approx(1.5)
    assert result.force.magnitude.y == pytest.approx(-2.0)


def test_parse_explicit_frame_overrides_the_manifest(tmp_path: Path) -> None:
    # The driver passes the definite answer from the config; an explicit Solid must win over a 2D manifest.
    _write_axisym_outputs(tmp_path)
    _write_manifest(tmp_path, Axisymmetric())

    result = parse_result(tmp_path, frame=Solid())
    assert isinstance(result.frame, Solid)
    assert result.force is not None
    assert result.force.magnitude.y == pytest.approx(-2.0)


def test_parse_raises_on_an_unreadable_manifest(tmp_path: Path) -> None:
    # The manifest decides the revolution; guessing Solid would silently report per-radian forces.
    _write_axisym_outputs(tmp_path)
    (tmp_path / "manifest.json").write_text("not json", encoding="utf-8")

    with pytest.raises(ValidationError):
        _ = parse_result(tmp_path)


def test_parse_without_a_manifest_is_a_bare_solid_parse(tmp_path: Path) -> None:
    _write_axisym_outputs(tmp_path)

    result = parse_result(tmp_path)
    assert isinstance(result.frame, Solid)
    assert result.force is not None
    assert result.force.magnitude.y == pytest.approx(-2.0)


def test_parse_reports_error_status_when_log_has_error(tmp_path: Path) -> None:
    (tmp_path / "elmersolver.log").write_text("MAIN: starting\nERROR:: Something blew up\n", encoding="utf-8")
    result = parse_result(tmp_path)
    assert result.status == "error"


def test_parse_reports_diverged_when_marker_present_and_done(tmp_path: Path) -> None:
    (tmp_path / "elmersolver.log").write_text(
        "ComputeChange: NS (ITER=1) (NRM,RELC): ( 0.1E+01 0.9E+00 )\n"
        "WARNING:: IterSolve: FAILED to converge to tolerance\n"
        "*** Elmer Solver: ALL DONE ***\n",
        encoding="utf-8",
    )
    result = parse_result(tmp_path)
    assert result.status == "diverged"


def test_parse_missing_log_is_error(tmp_path: Path) -> None:
    # No log means the solve can't be confirmed finished, so treat as error not a silent ok.
    result = parse_result(tmp_path)
    assert result.status == "error"
    assert result.force is None


def test_parse_dat_without_names_sidecar_is_skipped(tmp_path: Path) -> None:
    # A .dat with no sibling .dat.names can't be column-mapped, so it is skipped rather than guessed.
    (tmp_path / "orphan.dat").write_text("12.5 -3.4 0.27\n", encoding="utf-8")
    result = parse_result(tmp_path)
    assert result.raw == {}
    assert result.force is None


def test_parse_mpi_rank_dats_read_rank_zero(tmp_path: Path) -> None:
    # An MPI solve writes scalars.dat.<rank> instead of scalars.dat; the res: values are already globally
    # reduced (every rank writes the same row), so rank 0 must be read as-is — summing would overcount 8x.
    (tmp_path / "scalars.dat.names").write_text(
        "Variables in columns of matrix:\n1: res: nodal force 1\n2: res: nodal force 2\n3: res: nodal force 3\n",
        encoding="utf-8",
    )
    for rank in range(3):
        (tmp_path / f"scalars.dat.{rank}").write_text("12.5 -3.4 0.27\n", encoding="utf-8")
    (tmp_path / "elmersolver.log").write_text("*** Elmer Solver: ALL DONE ***\n", encoding="utf-8")

    result = parse_result(tmp_path)
    assert result.force is not None
    assert (result.force.magnitude.x, result.force.magnitude.y, result.force.magnitude.z) == pytest.approx((12.5, -3.4, 0.27))


def test_parse_mpi_rank_disagreement_warns_and_uses_rank_zero(tmp_path: Path) -> None:
    # Ranks only disagree when a SaveScalars operator ran without a global reduction; rank 0 still wins, loudly.
    (tmp_path / "scalars.dat.names").write_text(
        "Variables in columns of matrix:\n1: res: nodal force 2\n",
        encoding="utf-8",
    )
    (tmp_path / "scalars.dat.0").write_text("-3.4\n", encoding="utf-8")
    (tmp_path / "scalars.dat.1").write_text("-9.9\n", encoding="utf-8")

    result = parse_result(tmp_path)
    assert result.raw["res: nodal force 2"] == pytest.approx(-3.4)


def test_parse_names_sidecar_without_any_dat_is_skipped(tmp_path: Path) -> None:
    # A .names with neither a base .dat nor rank files has nothing to map, so it contributes no columns.
    (tmp_path / "scalars.dat.names").write_text(
        "Variables in columns of matrix:\n1: res: nodal force 1\n",
        encoding="utf-8",
    )
    result = parse_result(tmp_path)
    assert result.raw == {}


def test_parse_blank_only_dat_yields_no_columns(tmp_path: Path) -> None:
    # A .dat with only blank lines has no data row, yielding an empty map.
    (tmp_path / "case.dat").write_text("\n   \n\n", encoding="utf-8")
    (tmp_path / "case.dat.names").write_text(
        "Variables in columns of matrix:\n1: net force 1\n",
        encoding="utf-8",
    )
    result = parse_result(tmp_path)
    assert result.raw == {}
    assert result.force is None
