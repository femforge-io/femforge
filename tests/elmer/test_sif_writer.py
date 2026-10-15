"""Fidelity and behavior tests for elmer.sif: output identical to golden files that pyelmer 1.2.0 wrote, LF-only, with pyelmer's registry semantics."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import femforge.elmer.sif as sif
from femforge.run.electrical import read_boundary_names


# Golden outputs that pyelmer 1.2.0 wrote from _build_model's graph; regenerate them with pyelmer (LF-normalized)
# whenever the graph below changes.
_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "sif_reference"


def _build_model() -> sif.Simulation:
    # The representative graph covering sim.py's whole usage surface: multi-solver equations, shared + per-body
    # materials, a body force, Components over bodies and boundaries, boundaries, a constants update, a bare body.
    sim = sif.Simulation()
    sim.settings = {"Coordinate System": '"Axi Symmetric"', "Simulation Type": '"Steady state"', "Steady State Max Iterations": 1}
    sim.constants.update({"Permeability of Vacuum": 1.25663706212e-06})

    av = sif.Solver(sim, "WhitneyAVSolver", {"Equation": '"MGDynamics"', "Procedure": '"MagnetoDynamics" "WhitneyAVSolver"', "Fix Input Current Density": True})
    calc = sif.Solver(sim, "CalcFields", {"Equation": '"MGDynamicsCalc"', "Procedure": '"MagnetoDynamics" "MagnetoDynamicsCalcFields"'})
    scalars = sif.Solver(sim, "SaveScalars", {"Exec Solver": '"after timestep"'})
    main_eq = sif.Equation(sim, "Main Equation", [av, calc, scalars], {"Name": '"Main Equation"'})
    field_eq = sif.Equation(sim, "Field Equation", [calc], {"Name": '"Field Equation"'})

    air_mat = sif.Material(sim, "Air", {"Relative Permeability": 1.0})
    steel_mat = sif.Material(sim, "Steel1080", {"Electric Conductivity": 0.0})
    magnet_mat = sif.Material(sim, "N52_MAG_N", {"Relative Permeability": 1.05, "Magnetization 2": 1113001.2})

    air = sif.Body(sim, "AIR", [1], {"Name": '"AIR"'})
    air.equation = main_eq
    air.material = air_mat
    spacer = sif.Body(sim, "SPACER", [2], {"Name": '"SPACER"'})
    spacer.equation = field_eq
    spacer.material = steel_mat
    spacer.body_force = sif.BodyForce(sim, "SPACER_body_force", {"Joule Heat": "Logical True"})
    rod = sif.Body(sim, "ROD", [3], {"Name": '"ROD"'})
    rod.equation = field_eq
    rod.material = steel_mat
    magnet = sif.Body(sim, "N52_MAG_N", [4], {"Name": '"N52_MAG_N"'})
    magnet.equation = main_eq
    magnet.material = magnet_mat
    _ = sif.Body(sim, "BARE", [5, 6])

    far = sif.Boundary(sim, "AIR__EXT_far_field", [7], data={"AV {e}": "Real 0"})
    _ = sif.Boundary(sim, "ROD__EXT", [8, 9])
    _ = sif.Component(sim, "PhaseA_coil", master_bodies=[spacer, rod], data={"Coil Type": '"Stranded"', "Desired Current Density": 1000.0})
    _ = sif.Component(sim, "flux_probe", master_boundaries=[far], data={"Calculate Magnetic Force": "Logical True"})
    _ = sif.Component(sim, "bare_component", data={"Component Comment": 1})
    return sim


def _write_all(out_dir: Path) -> None:
    sim = _build_model()
    sim.write_startinfo(str(out_dir))
    sim.write_sif(str(out_dir))
    sim.write_boundary_ids(str(out_dir))


# --- fidelity vs the golden reference outputs (read_text normalizes newlines, so equality is modulo newlines) ---


def test_case_sif_matches_reference(tmp_path: Path) -> None:
    _write_all(tmp_path)
    assert (tmp_path / "case.sif").read_text() == (_GOLDEN / "full" / "case.sif").read_text()


def test_empty_simulation_matches_reference(tmp_path: Path) -> None:
    sif.Simulation().write_sif(str(tmp_path))
    assert (tmp_path / "case.sif").read_text() == (_GOLDEN / "empty" / "case.sif").read_text()


def test_startinfo_and_boundary_ids_match_reference(tmp_path: Path) -> None:
    _write_all(tmp_path)
    assert (tmp_path / "ELMERSOLVER_STARTINFO").read_text() == (_GOLDEN / "full" / "ELMERSOLVER_STARTINFO").read_text() == "case.sif\n"
    assert (tmp_path / "boundaries.yml").read_text() == (_GOLDEN / "full" / "boundaries.yml").read_text()
    assert read_boundary_names(tmp_path / "boundaries.yml") == {1: "AIR__EXT_far_field", 2: "ROD__EXT"}


def test_outputs_are_lf_only(tmp_path: Path) -> None:
    _write_all(tmp_path)
    for name in ("case.sif", "ELMERSOLVER_STARTINFO", "boundaries.yml"):
        raw = (tmp_path / name).read_bytes()
        assert b"\r\n" not in raw, name
        assert b"\n" in raw, name


# --- emitted text shapes, pinned as literal sif lines ---


def test_cross_reference_lines(tmp_path: Path) -> None:
    sim = _build_model()
    sim.write_sif(str(tmp_path))
    text = (tmp_path / "case.sif").read_text()
    assert '  CHECK KEYWORDS "Warn"\n' in text
    assert '  Mesh DB "." "."\n' in text
    assert "  Stefan Boltzmann = 5.6704e-08\n" in text
    assert "  Permeability of Vacuum = 1.25663706212e-06\n" in text
    assert "! Main Equation\nEquation 1\n" in text
    assert "  Active Solvers(3) = 1 2 3   ! WhitneyAVSolver, CalcFields, SaveScalars, \n" in text
    assert "  Equation = 1  ! Main Equation\n" in text
    assert "  Material = 2  ! Steel1080\n" in text
    assert "  Body Force = 1  ! SPACER_body_force\n" in text
    assert "  Target Bodies(2) = 5 6\n" in text
    assert "  Target Boundaries(2) = 8 9\n" in text
    assert "  Master Bodies(2) = 2 3  ! SPACER, ROD,\n" in text
    assert "  Master Boundaries(1) = 1  ! AIR__EXT_far_field,\n" in text
    assert "  Name = bare_component\n" in text
    assert "Wrote sif-file" not in text


# --- registry semantics ---


def test_ids_zero_until_write_then_insertion_order(tmp_path: Path) -> None:
    sim = sif.Simulation()
    z_solver = sif.Solver(sim, "z_solver", {})
    a_solver = sif.Solver(sim, "a_solver", {})
    eq = sif.Equation(sim, "eq", [a_solver])
    mat = sif.Material(sim, "mat", {})
    z_body = sif.Body(sim, "z_body", [2])
    a_body = sif.Body(sim, "a_body", [1])
    bc = sif.Boundary(sim, "bc", [3])
    bf = sif.BodyForce(sim, "bf", {"k": 1})
    comp = sif.Component(sim, "comp", master_bodies=[z_body])
    assert all(obj.id == 0 for obj in (z_solver, a_solver, eq, mat, z_body, a_body, bc, bf, comp))

    sim.write_sif(str(tmp_path))
    assert (z_solver.id, a_solver.id) == (1, 2)  # insertion order, not name order
    assert (z_body.id, a_body.id) == (1, 2)
    assert (eq.id, mat.id, bc.id, bf.id, comp.id) == (1, 1, 1, 1, 1)  # ids restart per section
    assert str(a_solver) == "2"


def test_duplicate_construction_keeps_wiring() -> None:
    sim = sif.Simulation()
    eq = sif.Equation(sim, "eq", [])
    body = sif.Body(sim, "b", [1], {"Name": '"b"'})
    body.equation = eq
    again = sif.Body(sim, "b", [1], {"Name": '"b"'})
    assert again is body
    assert again.equation is eq


_ClashFactory = Callable[[sif.Simulation], object]

_CLASH_CASES: list[tuple[str, _ClashFactory, _ClashFactory]] = [
    ("Solver", lambda sim: sif.Solver(sim, "obj", {"a": 1}), lambda sim: sif.Solver(sim, "obj", {"a": 2})),
    ("Equation", lambda sim: sif.Equation(sim, "obj", [sif.Solver(sim, "s", {"x": 1})], {"e": 2}), lambda sim: sif.Equation(sim, "obj", [])),
    ("Material", lambda sim: sif.Material(sim, "obj", {"a": 1}), lambda sim: sif.Material(sim, "obj", None)),
    ("Body", lambda sim: sif.Body(sim, "obj"), lambda sim: sif.Body(sim, "obj", [1])),
    ("BodyForce", lambda sim: sif.BodyForce(sim, "obj", {"a": 1}), lambda sim: sif.BodyForce(sim, "obj", {})),
    ("Component", lambda sim: sif.Component(sim, "obj", master_bodies=[sif.Body(sim, "b", [1])]), lambda sim: sif.Component(sim, "obj")),
    ("Boundary", lambda sim: sif.Boundary(sim, "obj"), lambda sim: sif.Boundary(sim, "obj", [7], {"x": 1})),
]
_CLASH_IDS = [case[0] for case in _CLASH_CASES]


@pytest.mark.parametrize(("kind", "make", "clash"), _CLASH_CASES, ids=_CLASH_IDS)
def test_same_name_same_args_returns_existing(kind: str, make: _ClashFactory, clash: _ClashFactory) -> None:
    sim = sif.Simulation()
    first = make(sim)
    assert make(sim) is first


@pytest.mark.parametrize(("kind", "make", "clash"), _CLASH_CASES, ids=_CLASH_IDS)
def test_same_name_different_args_raises(kind: str, make: _ClashFactory, clash: _ClashFactory) -> None:
    sim = sif.Simulation()
    _ = make(sim)
    with pytest.raises(ValueError, match=f'{kind} name clash: "obj"'):
        _ = clash(sim)
