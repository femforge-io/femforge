"""run.electrical: SaveScalars flux columns -> per-electrode currents -> phase resistance."""

from pathlib import Path

import pytest

from femforge.run.electrical import boundary_fluxes, electrode_currents, phase_currents, read_boundary_names, resistance


RAW: dict[str, float] = {
    "diffusive flux: potential over bc 1": 5.95,
    "diffusive flux: potential over bc 4": -5.95,
    "res: potential": 0.001,  # non-flux columns are ignored
    "value: potential in body 2": 42.0,
}


def test_boundary_fluxes_extracts_bc_indexed_columns() -> None:
    assert boundary_fluxes(RAW) == {1: 5.95, 4: -5.95}


def test_boundary_fluxes_tolerates_wording_variants() -> None:
    assert boundary_fluxes({"Diffusive Flux Potential over bc 7": 1.5}) == {7: 1.5}


def test_read_boundary_names_parses_flat_yaml(tmp_path: Path) -> None:
    yml = tmp_path / "boundaries.yml"
    _ = yml.write_text("1: COPPER_ENTRY__EXT_fixed_potential\n4: COPPER_EXIT__EXT_fixed_potential\n")
    assert read_boundary_names(yml) == {1: "COPPER_ENTRY__EXT_fixed_potential", 4: "COPPER_EXIT__EXT_fixed_potential"}


def test_read_boundary_names_skips_malformed_lines(tmp_path: Path) -> None:
    yml = tmp_path / "boundaries.yml"
    _ = yml.write_text("# comment\n1: NAME_A\nnot a mapping\n2:\n")
    assert read_boundary_names(yml) == {1: "NAME_A"}


def test_electrode_currents_joins_names() -> None:
    names = {1: "COPPER_ENTRY__EXT_fixed_potential", 4: "COPPER_EXIT__EXT_fixed_potential"}
    currents = electrode_currents(RAW, names)
    assert currents == {"COPPER_ENTRY__EXT_fixed_potential": 5.95, "COPPER_EXIT__EXT_fixed_potential": -5.95}
    # A flux whose bc has no name entry is dropped rather than invented.
    assert electrode_currents(RAW, {1: "ONLY_THIS"}) == {"ONLY_THIS": 5.95}


def test_phase_currents_takes_dominant_flux_per_phase() -> None:
    # Internal electrode boundaries integrate one-sided: the pad side reads ~0, the conductor side the full
    # terminal current. The dominant |flux| wins per phase; names without a PHASE index are ignored.
    currents = {
        "COPPER_ENTRY_PHASE0__COPPER_PHASE0_fixed_potential": 0.0,
        "COPPER_ENTRY_PHASE0__EXT_fixed_potential": 1e-12,
        "COPPER_EXIT_PHASE0__COPPER_PHASE0_fixed_potential": -5.95,
        "COPPER_ENTRY_PHASE1__COPPER_PHASE1_fixed_potential": 3.10,
        "SOMETHING_ELSE_fixed_potential": 99.0,
    }
    assert phase_currents(currents) == {"0": -5.95, "1": 3.10}


def test_resistance_is_delta_v_over_i() -> None:
    assert resistance(0.001, 5.95) == pytest.approx(0.001 / 5.95)
    # Sign of the flux (in vs out electrode) doesn't matter.
    assert resistance(0.001, -5.95) == pytest.approx(0.001 / 5.95)


def test_resistance_rejects_zero_current() -> None:
    with pytest.raises(ValueError, match="zero"):
        _ = resistance(0.001, 0.0)
