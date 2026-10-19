"""End-to-end (gmsh + Elmer gated): the coupled StatCurrentSolve -> HeatSolve stack reproduces an analytic steady temperature."""

from __future__ import annotations

from pathlib import Path

import pytest

from femforge.materials.copper import Copper
from femforge.materials.properties import MaterialProperties
from femforge.physical.conditions import ConditionUnion, Convection, FixedVoltage, JouleHeat
from femforge.physical.units import U
from femforge.run.parse import _parse_scalars
from tests.elmer.test_current_solve_integration import _A_SIDE, _L, _PAD, R_ANALYTIC, _Region, _solve_current, _static_copper
from tests.elmer.test_thermal_solve_integration import _gated_elmer


_VOLTS = 0.01  # 10 mV across the bar -> P ~ 59.6 W, a ~10 K rise: comfortably above numeric noise
_H = 15.0  # W/m^2K convection on the bar's shell
_T_AMB = 300.0
_A_LATERAL = 4 * _L * _A_SIDE  # the bar's 4 exposed faces
_SIGMA_20C = 5.96e7
_ALPHA = 3.93e-3


def _regions(material: MaterialProperties) -> list[_Region]:
    entry: list[ConditionUnion] = [FixedVoltage(value=_VOLTS * U.V)]
    exit_: list[ConditionUnion] = [FixedVoltage(value=0 * U.V)]
    bar: list[ConditionUnion] = [JouleHeat(), Convection(coefficient=_H * U.W / (U.m**2 * U.K), ext_temperature=_T_AMB * U.K)]
    return [
        _Region("PAD_A", material, (-_PAD, 0, 0, _PAD, _A_SIDE, _A_SIDE), entry),
        _Region("BAR", material, (0, 0, 0, _L, _A_SIDE, _A_SIDE), bar),
        _Region("PAD_B", material, (_L, 0, 0, _PAD, _A_SIDE, _A_SIDE), exit_),
    ]


def test_joule_heated_bar_reaches_analytic_temperature(tmp_path: Path) -> None:
    elmer = _gated_elmer()
    mesh_dir = _solve_current(tmp_path, elmer, _regions(_static_copper()), physics="joule_heating")

    raw: dict[str, float] = _parse_scalars(mesh_dir)
    t_max = next((value for name, value in raw.items() if "max" in name.lower() and "temperature" in name.lower()), None)
    assert t_max is not None, f"no max-temperature column in {sorted(raw)}"

    power = _VOLTS**2 / R_ANALYTIC  # ~59.6 W
    t_expected = _T_AMB + power / (_H * _A_LATERAL)  # ~309.9 K; copper's tiny Biot number makes the bar near-isothermal, so the energy balance is closed-form

    # 10% band on the *rise*: the film covers exactly the bar's 4 lateral faces (external-only), but the
    # adiabatic pads still sink a little heat and the bar is only near-isothermal.
    assert t_max - _T_AMB == pytest.approx(t_expected - _T_AMB, rel=0.10), f"T_max {t_max} K vs analytic {t_expected} K (raw: { {k: v for k, v in raw.items()} })"
    # And the electrical side still holds in the coupled run: total Joule power matches V^2/R.
    p_int = next((value for name, value in raw.items() if "joule" in name.lower()), None)
    assert p_int is not None
    assert p_int == pytest.approx(power, rel=0.05), f"Joule power {p_int} W vs analytic {power} W"


def test_sigma_t_feedback_reduces_power_at_fixed_voltage(tmp_path: Path) -> None:
    """At fixed voltage, heating lowers sigma(T), so dissipated power drops below the constant-sigma prediction."""
    elmer = _gated_elmer()
    mesh_dir = _solve_current(tmp_path, elmer, _regions(Copper()), physics="joule_heating")

    # The sif carries the runtime law, not a pre-evaluated float.
    sif = (mesh_dir / "case.sif").read_text()
    assert "Variable Temperature" in sif and 'Real MATC "' in sif and "tx(0)" in sif

    raw: dict[str, float] = _parse_scalars(mesh_dir)
    p_int = next((value for name, value in raw.items() if "joule" in name.lower()), None)
    t_max = next((value for name, value in raw.items() if "max" in name.lower() and "temperature" in name.lower()), None)
    assert p_int is not None and t_max is not None

    # Constant-sigma prediction at the ~300 K starting point; the sigma(T(x)) solution must dissipate measurably less.
    p_sigma_300 = _VOLTS**2 * (_SIGMA_20C / (1 + _ALPHA * (300 - 293.15))) * _A_SIDE**2 / _L
    assert p_int < 0.99 * p_sigma_300, f"expected sigma(T) feedback to lower power below {p_sigma_300} W, got {p_int} W"
    assert p_int > 0.85 * p_sigma_300, f"power dropped implausibly far: {p_int} W vs constant-sigma {p_sigma_300} W"

    # Energy balance closes with the reduced power: T ~ ambient + P/(h*A).
    t_expected = _T_AMB + p_int / (_H * _A_LATERAL)
    assert t_max - _T_AMB == pytest.approx(t_expected - _T_AMB, rel=0.10), f"T_max {t_max} K vs energy balance {t_expected} K"