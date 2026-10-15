"""The max-current search: linear I² seed, drive seam (voltage Picard / prescribed current), and the regula-falsi search in I² space."""

import math
from dataclasses import dataclass

import pytest

from femforge.common.frame import Solid
from femforge.config import SimulationConfig
from femforge.optimize.max_current import (
    DirectDrive,
    LinearSeed,
    MaxCurrentSearch,
    SolveFailedError,
    VoltageDrive,
    find_max_current,
    linear_max_current_seed,
    scale_fixed_potentials,
    seed_from_result,
)
from femforge.common.tags import CurrentDir, TagKey
from femforge.physical.conditions import Convection, FixedPotential
from femforge.physical.domain import EntityTag
from femforge.physical.units import Quantity, U, quantity
from femforge.run.result import Result


def test_seed_scales_current_by_sqrt_of_temperature_headroom() -> None:
    # Reference: 2 A produced a 10 K rise; the limit allows a 40 K rise -> scale 2, I_max 4 A.
    seed = linear_max_current_seed(reference_current=2 * U.A, max_temperature=310 * U.K, ambient_temperature=300 * U.K, limit_temperature=340 * U.K)
    assert isinstance(seed, LinearSeed)
    assert seed.scale == pytest.approx(2.0)
    assert seed.max_current.to(U.A).magnitude == pytest.approx(4.0)


def test_seed_is_sign_insensitive_and_unit_converting() -> None:
    # A negative terminal current (flux direction) and mixed temperature units still yield the same seed.
    seed = linear_max_current_seed(reference_current=-2000 * U.mA, max_temperature=(36.85 * U.degC).to(U.K), ambient_temperature=300 * U.K, limit_temperature=340 * U.K)
    assert seed.max_current.to(U.A).magnitude == pytest.approx(4.0)


def test_seed_at_the_limit_is_identity() -> None:
    # Already exactly at the limit: the seed says "keep the same drive".
    seed = linear_max_current_seed(reference_current=3 * U.A, max_temperature=340 * U.K, ambient_temperature=300 * U.K, limit_temperature=340 * U.K)
    assert seed.scale == pytest.approx(1.0)
    assert seed.max_current.to(U.A).magnitude == pytest.approx(3.0)


def test_seed_rejects_no_rise_no_headroom_and_no_current() -> None:
    with pytest.raises(ValueError, match="no temperature rise"):
        _ = linear_max_current_seed(reference_current=1 * U.A, max_temperature=300 * U.K, ambient_temperature=300 * U.K, limit_temperature=340 * U.K)
    with pytest.raises(ValueError, match="not above ambient"):
        _ = linear_max_current_seed(reference_current=1 * U.A, max_temperature=310 * U.K, ambient_temperature=300 * U.K, limit_temperature=300 * U.K)
    with pytest.raises(ValueError, match="current is zero"):
        _ = linear_max_current_seed(reference_current=0 * U.A, max_temperature=310 * U.K, ambient_temperature=300 * U.K, limit_temperature=340 * U.K)


def test_seed_from_result_reads_temperature_max() -> None:
    result = Result(frame=Solid(), status="ok", temperature_max=310 * U.K)
    seed = seed_from_result(result, reference_current=2 * U.A, ambient_temperature=300 * U.K, limit_temperature=340 * U.K)
    assert seed.max_current.to(U.A).magnitude == pytest.approx(4.0)


def test_seed_from_result_requires_temperature_max() -> None:
    with pytest.raises(ValueError, match="temperature_max"):
        _ = seed_from_result(Result(frame=Solid(), status="ok"), reference_current=2 * U.A, ambient_temperature=300 * U.K, limit_temperature=340 * U.K)


_V_REF = 1.0
"""Entry-electrode volts of the base drive; the fakes read the scaled value back as their excitation."""


def _drive_config(entry_volts: float = _V_REF) -> SimulationConfig:
    """A minimal driven config: an Entry/Exit electrode pair plus a condition-less tag that must pass through untouched."""
    config = SimulationConfig()
    config.domain.tags.append(EntityTag(selector={TagKey.CURRENT_DIR: CurrentDir.ENTRY}, conditions=[FixedPotential(value=entry_volts * U.V)]))
    config.domain.tags.append(EntityTag(selector={TagKey.CURRENT_DIR: CurrentDir.EXIT}, conditions=[FixedPotential(value=0 * U.V)]))
    config.domain.tags.append(EntityTag(selector={TagKey.PHASE: "0"}))
    return config


def _entry_volts(config: SimulationConfig) -> float:
    """The (scaled) nonzero electrode potential — the drive a fake solver runs at."""
    for tag in config.domain.tags:
        for condition in tag.conditions:
            if isinstance(condition, FixedPotential):
                volts = float(condition.value.to(U.V).magnitude)
                if volts != 0:
                    return volts
    raise AssertionError("no driven electrode in config")


def _current_of(result: Result) -> Quantity:
    """The phase current a fake solver stashed in ``Result.raw``."""
    return quantity(result.raw["current"], "ampere")


def _direct() -> DirectDrive:
    """A DirectDrive whose configure encodes the target amps as the entry volts (the fake solvers read it back)."""
    return DirectDrive(configure=lambda _base, amps: _drive_config(amps))


@dataclass
class _ToyCoil:
    """Conductor with linear rho(T): closed-form steady R = (R0 + sqrt(R0² + 4·R0·alpha·theta·V²))/2 at held voltage
    (runaway above 1/sqrt(alpha·theta·R0) at held current); ``diverge_above_volts`` emulates the coupled solve failing."""

    r0: float = 1.0
    alpha: float = 4e-3
    theta: float = 50.0
    t0: float = 300.0
    diverge_above_volts: float | None = None

    def evaluate(self, config: SimulationConfig) -> Result:
        volts = _entry_volts(config)
        if self.diverge_above_volts is not None and volts > self.diverge_above_volts:
            return Result(frame=Solid(), status="diverged")
        r = 0.5 * (self.r0 + math.sqrt(self.r0**2 + 4 * self.r0 * self.alpha * self.theta * volts**2))
        return Result(frame=Solid(), status="ok", temperature_max=(self.t0 + self.theta * volts**2 / r) * U.K, raw={"current": volts / r})

    def max_current_at(self, limit_kelvin: float) -> float:
        """The exact current whose steady temperature is the limit: I = sqrt(dT / (theta·R(T_limit)))."""
        r_lim = self.r0 * (1 + self.alpha * (limit_kelvin - self.t0))
        return math.sqrt((limit_kelvin - self.t0) / (self.theta * r_lim))


# --- voltage-driven search ---------------------------------------------------


def test_voltage_search_lands_on_the_analytic_limit_in_two_steps() -> None:
    coil = _ToyCoil()
    base = _drive_config()
    reference = coil.evaluate(base)
    reference_current = _current_of(reference)
    seed = seed_from_result(reference, reference_current=reference_current, ambient_temperature=300 * U.K, limit_temperature=360 * U.K)

    search = find_max_current(
        evaluate=coil.evaluate,
        base_config=base,
        seed=seed,
        drive=VoltageDrive(current_of=_current_of, reference_current=reference_current),
        limit_temperature=360 * U.K,
        ambient_temperature=300 * U.K,
    )

    assert search.converged and not search.runaway
    # The linear seed ignores the rising resistivity, so it overshoots: the first step must classify "over"...
    assert search.steps[0].outcome == "over"
    # ...and the false-position proposal then lands within the temperature tolerance immediately.
    assert len(search.steps) <= 3
    lo = search.max_current.to(U.A).magnitude
    assert lo == pytest.approx(coil.max_current_at(360.0), rel=0.02)
    assert lo <= coil.max_current_at(360.0) * 1.001  # the accepted current sits on the conservative side
    assert search.upper_bracket_current is not None and lo < search.upper_bracket_current.to(U.A).magnitude
    assert all(step.evaluations >= 1 and step.result.status == "ok" for step in search.steps)
    # The accepted step is the under solve that IS max_current — no re-solve needed to inspect its steady state.
    assert search.accepted in search.steps and search.accepted.outcome == "under"
    assert search.accepted.measured_current is not None
    assert search.accepted.measured_current.to(U.A).magnitude == pytest.approx(lo)
    # The search never mutates the base config's drive.
    assert _entry_volts(base) == _V_REF


def test_search_projects_upward_past_a_lowball_seed() -> None:
    coil = _ToyCoil()
    base = _drive_config()
    reference_current = _current_of(coil.evaluate(base))
    true_max_current = coil.max_current_at(360.0)
    lowball = LinearSeed(scale=0.3, max_current=quantity(0.3 * true_max_current, "ampere"))

    search = find_max_current(
        evaluate=coil.evaluate,
        base_config=base,
        seed=lowball,
        drive=VoltageDrive(current_of=_current_of, reference_current=reference_current),
        limit_temperature=360 * U.K,
        ambient_temperature=300 * U.K,
        max_steps=20,
    )

    assert search.converged and not search.runaway
    assert search.steps[0].outcome == "under"
    assert search.max_current.to(U.A).magnitude == pytest.approx(true_max_current, rel=0.05)


def test_nonconvergence_is_the_runaway_upper_bracket() -> None:
    coil = _ToyCoil(diverge_above_volts=1.5)
    base = _drive_config()
    reference = coil.evaluate(base)
    reference_current = _current_of(reference)
    seed = seed_from_result(reference, reference_current=reference_current, ambient_temperature=300 * U.K, limit_temperature=400 * U.K)

    search = find_max_current(
        evaluate=coil.evaluate,
        base_config=base,
        seed=seed,
        drive=VoltageDrive(current_of=_current_of, reference_current=reference_current),
        limit_temperature=400 * U.K,
        ambient_temperature=300 * U.K,
    )

    assert search.converged and search.runaway
    # The seed's drive already exceeds the divergence level, so the very first step is the runaway bracket.
    assert search.steps[0].outcome == "runaway"
    assert any(step.outcome == "runaway" and step.measured_current is None for step in search.steps)
    # The limit found is the last steady current before the solver stops converging, not a temperature crossing.
    edge_current = coil.evaluate(_drive_config(1.5)).raw["current"]
    assert search.max_current.to(U.A).magnitude == pytest.approx(edge_current, rel=0.05)


def test_current_saturation_is_runaway_even_when_every_solve_converges() -> None:
    # A hard-saturating drive: no solve ever fails and no steady temperature crosses the limit, yet currents
    # above the saturation level simply cannot be realized — the voltage-iteration shortfall must classify runaway.
    def saturating(config: SimulationConfig) -> Result:
        current = min(_entry_volts(config), 2.0)
        return Result(frame=Solid(), status="ok", temperature_max=(300 + 5 * current * current) * U.K, raw={"current": current})

    search = find_max_current(
        evaluate=saturating,
        base_config=_drive_config(),
        seed=LinearSeed(scale=3.0, max_current=3 * U.A),
        drive=VoltageDrive(current_of=_current_of, reference_current=1 * U.A, current_rtol=0.01),
        limit_temperature=340 * U.K,
        ambient_temperature=300 * U.K,
    )

    assert search.converged and search.runaway
    assert all(step.result.status == "ok" for step in search.steps)
    saturation_steps = [step for step in search.steps if step.outcome == "runaway"]
    assert saturation_steps and all(step.measured_current is not None for step in saturation_steps)
    assert search.max_current.to(U.A).magnitude <= 2.0
    assert search.upper_bracket_current is not None and search.upper_bracket_current.to(U.A).magnitude >= 2.0 * 0.99


def test_unsettled_inner_iteration_raises() -> None:
    calls = {"count": 0}

    def alternating(_config: SimulationConfig) -> Result:
        calls["count"] += 1
        return Result(frame=Solid(), status="ok", temperature_max=310 * U.K, raw={"current": 10.0 if calls["count"] % 2 else 1.0})

    with pytest.raises(ValueError, match="did not settle"):
        _ = find_max_current(
            evaluate=alternating,
            base_config=_drive_config(),
            seed=LinearSeed(scale=1.0, max_current=3 * U.A),
            drive=VoltageDrive(current_of=_current_of, reference_current=3 * U.A, max_inner=4),
            limit_temperature=340 * U.K,
            ambient_temperature=300 * U.K,
        )


def test_zero_measured_current_raises() -> None:
    def dead(_config: SimulationConfig) -> Result:
        return Result(frame=Solid(), status="ok", temperature_max=310 * U.K, raw={"current": 0.0})

    with pytest.raises(ValueError, match="zero phase current"):
        _ = find_max_current(
            evaluate=dead,
            base_config=_drive_config(),
            seed=LinearSeed(scale=1.0, max_current=1 * U.A),
            drive=VoltageDrive(current_of=_current_of, reference_current=1 * U.A),
            limit_temperature=340 * U.K,
            ambient_temperature=300 * U.K,
        )


def test_missing_temperature_on_a_converged_solve_raises() -> None:
    def no_temperature(config: SimulationConfig) -> Result:
        return Result(frame=Solid(), status="ok", raw={"current": _entry_volts(config)})

    with pytest.raises(ValueError, match="temperature_max"):
        _ = find_max_current(
            evaluate=no_temperature,
            base_config=_drive_config(),
            seed=LinearSeed(scale=2.0, max_current=2 * U.A),
            drive=VoltageDrive(current_of=_current_of, reference_current=1 * U.A),
            limit_temperature=340 * U.K,
            ambient_temperature=300 * U.K,
        )


# --- prescribed-current search ----------------------------------------------


def test_direct_drive_linear_case_accepts_the_seed_in_one_solve() -> None:
    # A temperature-independent source: dT is exactly quadratic in I, so the I² seed IS the root and the single
    # verification solve accepts it on the temperature residual — no bracketing ever needed.
    def linear(config: SimulationConfig) -> Result:
        current = _entry_volts(config)
        return Result(frame=Solid(), status="ok", temperature_max=(300 + 10 * current * current) * U.K)

    reference = linear(_drive_config(1.0))
    seed = seed_from_result(reference, reference_current=1 * U.A, ambient_temperature=300 * U.K, limit_temperature=340 * U.K)
    assert seed.max_current.to(U.A).magnitude == pytest.approx(2.0)

    search = find_max_current(
        evaluate=linear,
        base_config=_drive_config(),
        seed=seed,
        drive=_direct(),
        limit_temperature=340 * U.K,
        ambient_temperature=300 * U.K,
    )

    assert search.converged and not search.runaway
    assert len(search.steps) == 1
    step = search.steps[0]
    assert step.outcome == "under" and step.evaluations == 1
    assert step.drive_scale is None
    assert step.measured_current is not None and step.measured_current.to(U.A).magnitude == pytest.approx(2.0)
    assert search.max_current.to(U.A).magnitude == pytest.approx(2.0)
    assert search.upper_bracket_current is None  # accepted before any over sample existed
    assert search.accepted is step


def test_direct_drive_converges_on_a_coupled_source_within_a_few_solves() -> None:
    # Self-heating feedback: dT = k·I²/(1 − α·k·I²), the prescribed-current analogue of sigma(T) Joule heating.
    k, alpha = 10.0, 0.02

    def coupled(config: SimulationConfig) -> Result:
        x = _entry_volts(config) ** 2
        return Result(frame=Solid(), status="ok", temperature_max=(300 + k * x / (1 - alpha * k * x)) * U.K)

    true_max_current = math.sqrt(40.0 / (k * (1 + alpha * 40.0)))  # dT = 40 K at the limit
    reference = coupled(_drive_config(1.0))
    seed = seed_from_result(reference, reference_current=1 * U.A, ambient_temperature=300 * U.K, limit_temperature=340 * U.K)

    search = find_max_current(
        evaluate=coupled,
        base_config=_drive_config(),
        seed=seed,
        drive=_direct(),
        limit_temperature=340 * U.K,
        ambient_temperature=300 * U.K,
    )

    assert search.converged and not search.runaway
    assert len(search.steps) <= 6
    assert search.max_current.to(U.A).magnitude == pytest.approx(true_max_current, rel=0.01)


def test_direct_drive_concave_response_converges_via_illinois_damping() -> None:
    # dT ∝ I (concave in x = I²): consecutive over samples pile on one side, which plain false position
    # stalls on; the Illinois halving keeps the proposals moving.
    def concave(config: SimulationConfig) -> Result:
        return Result(frame=Solid(), status="ok", temperature_max=(300 + 40 * _entry_volts(config)) * U.K)

    search = find_max_current(
        evaluate=concave,
        base_config=_drive_config(),
        seed=LinearSeed(scale=2.0, max_current=2 * U.A),
        drive=_direct(),
        limit_temperature=340 * U.K,
        ambient_temperature=300 * U.K,
    )

    assert search.converged and not search.runaway
    assert len(search.steps) <= 6
    assert search.max_current.to(U.A).magnitude == pytest.approx(1.0, rel=0.05)


def test_direct_drive_zero_rise_plateau_expands_geometrically() -> None:
    # No measurable rise below a threshold: the projection is undefined (dT = 0), so the probe grows by the
    # expand cap until the threshold brackets it.
    def plateau(config: SimulationConfig) -> Result:
        hot = _entry_volts(config) >= 2.0
        return Result(frame=Solid(), status="ok", temperature_max=(400.0 if hot else 300.0) * U.K)

    search = find_max_current(
        evaluate=plateau,
        base_config=_drive_config(),
        seed=LinearSeed(scale=0.5, max_current=0.5 * U.A),
        drive=_direct(),
        limit_temperature=350 * U.K,
        ambient_temperature=300 * U.K,
        max_steps=20,
    )

    assert search.converged and not search.runaway
    assert search.max_current.to(U.A).magnitude <= 2.0
    assert search.upper_bracket_current is not None and search.upper_bracket_current.to(U.A).magnitude >= 2.0 * 0.95


def test_direct_drive_runaway_edge_with_ambient_floor_bisects() -> None:
    # Diverges above a threshold, sits at ambient below it: no residual information on either side, so every
    # proposal degrades to the midpoint safeguard and the bracket still closes on the edge.
    def cliff(config: SimulationConfig) -> Result:
        if _entry_volts(config) >= 2.0:
            return Result(frame=Solid(), status="diverged")
        return Result(frame=Solid(), status="ok", temperature_max=300 * U.K)

    search = find_max_current(
        evaluate=cliff,
        base_config=_drive_config(),
        seed=LinearSeed(scale=4.0, max_current=4 * U.A),
        drive=_direct(),
        limit_temperature=350 * U.K,
        ambient_temperature=300 * U.K,
    )

    assert search.converged and search.runaway
    assert search.steps[0].outcome == "runaway"
    assert search.max_current.to(U.A).magnitude < 2.0
    assert search.upper_bracket_current is not None and search.upper_bracket_current.to(U.A).magnitude >= 2.0


def test_solver_error_raises_instead_of_masquerading_as_runaway() -> None:
    # A hard tool failure (status "error", not "diverged") must stop the search: absorbed as a runaway
    # bracket it would silently bias max_current low while the search still reports success.
    def broken(config: SimulationConfig) -> Result:
        if _entry_volts(config) >= 2.0:
            return Result(frame=Solid(), status="error")
        return Result(frame=Solid(), status="ok", temperature_max=(300 + 10 * _entry_volts(config) ** 2) * U.K, raw={"current": _entry_volts(config)})

    for drive in (_direct(), VoltageDrive(current_of=_current_of, reference_current=1 * U.A)):
        with pytest.raises(SolveFailedError, match="not a physics outcome"):
            _ = find_max_current(
                evaluate=broken,
                base_config=_drive_config(),
                seed=LinearSeed(scale=4.0, max_current=4 * U.A),
                drive=drive,
                limit_temperature=340 * U.K,
                ambient_temperature=300 * U.K,
            )


def test_temperature_of_searches_a_custom_metric() -> None:
    # The global hotspot rises twice as fast as the constrained material: the default metric would stop at
    # I = sqrt(2), the per-material extractor must carry the search to the material's own crossing at I = 2.
    def solve(config: SimulationConfig) -> Result:
        x = _entry_volts(config) ** 2
        return Result(frame=Solid(), status="ok", temperature_max=(300 + 20 * x) * U.K, raw={"material_t": 300 + 10 * x})

    def material_temperature(result: Result) -> Quantity:
        return quantity(result.raw["material_t"], "kelvin")

    search = find_max_current(
        evaluate=solve,
        base_config=_drive_config(),
        seed=LinearSeed(scale=2.0, max_current=2 * U.A),
        drive=_direct(),
        limit_temperature=340 * U.K,
        ambient_temperature=300 * U.K,
        temperature_of=material_temperature,
    )

    assert search.converged and not search.runaway
    assert search.max_current.to(U.A).magnitude == pytest.approx(2.0, rel=0.02)


# --- budgets and argument validation -----------------------------------------


def test_budget_exhaustion_without_a_lower_bracket_raises() -> None:
    # A single allowed step that lands over: no current was ever confirmed under the limit.
    coil = _ToyCoil()
    base = _drive_config()
    reference_current = _current_of(coil.evaluate(base))
    with pytest.raises(ValueError, match="found no current"):
        _ = find_max_current(
            evaluate=coil.evaluate,
            base_config=base,
            seed=LinearSeed(scale=0.5, max_current=0.5 * U.A),
            drive=VoltageDrive(current_of=_current_of, reference_current=reference_current),
            limit_temperature=301 * U.K,
            ambient_temperature=300 * U.K,
            max_steps=1,
        )


def test_budget_exhaustion_without_an_upper_bracket_raises() -> None:
    # A seed far below the true limit: every capped upward probe stays under within the budget.
    coil = _ToyCoil()
    base = _drive_config()
    reference_current = _current_of(coil.evaluate(base))
    with pytest.raises(ValueError, match="never exceeded the limit"):
        _ = find_max_current(
            evaluate=coil.evaluate,
            base_config=base,
            seed=LinearSeed(scale=0.01, max_current=0.01 * U.A),
            drive=VoltageDrive(current_of=_current_of, reference_current=reference_current),
            limit_temperature=360 * U.K,
            ambient_temperature=300 * U.K,
            max_steps=3,
        )


def test_budget_exhaustion_with_both_brackets_returns_unconverged() -> None:
    coil = _ToyCoil()
    base = _drive_config()
    reference = coil.evaluate(base)
    reference_current = _current_of(reference)
    seed = seed_from_result(reference, reference_current=reference_current, ambient_temperature=300 * U.K, limit_temperature=360 * U.K)

    # Tolerances far below what three steps can reach: the search must report the standing bracket honestly.
    search = find_max_current(
        evaluate=coil.evaluate,
        base_config=base,
        seed=seed,
        drive=VoltageDrive(current_of=_current_of, reference_current=reference_current),
        limit_temperature=360 * U.K,
        ambient_temperature=300 * U.K,
        max_steps=3,
        bracket_rtol=1e-9,
        temperature_rtol=1e-9,
    )

    assert not search.converged
    assert search.upper_bracket_current is not None
    assert search.max_current.to(U.A).magnitude < search.upper_bracket_current.to(U.A).magnitude
    assert len(search.steps) == 3
    # Even the unconverged return carries the solve behind the standing lower bracket.
    assert search.accepted in search.steps and search.accepted.outcome == "under"
    assert search.accepted.measured_current is not None
    assert search.accepted.measured_current.to(U.A).magnitude == pytest.approx(search.max_current.to(U.A).magnitude)


def test_search_rejects_degenerate_arguments() -> None:
    coil = _ToyCoil()
    base = _drive_config()

    def run(
        seed: LinearSeed | None = None,
        limit_temperature: Quantity = 340 * U.K,
        temperature_rtol: float = 0.02,
        bracket_rtol: float = 0.05,
        max_steps: int = 12,
        expand: float = 2.0,
    ) -> MaxCurrentSearch:
        actual_seed = LinearSeed(scale=1.0, max_current=1 * U.A) if seed is None else seed
        return find_max_current(
            evaluate=coil.evaluate,
            base_config=base,
            seed=actual_seed,
            drive=VoltageDrive(current_of=_current_of, reference_current=1 * U.A),
            limit_temperature=limit_temperature,
            ambient_temperature=300 * U.K,
            temperature_rtol=temperature_rtol,
            bracket_rtol=bracket_rtol,
            max_steps=max_steps,
            expand=expand,
        )

    with pytest.raises(ValueError, match="temperature_rtol must be positive"):
        _ = run(temperature_rtol=0)
    with pytest.raises(ValueError, match="bracket_rtol must be positive"):
        _ = run(bracket_rtol=-0.1)
    with pytest.raises(ValueError, match="max_steps must be at least 1"):
        _ = run(max_steps=0)
    with pytest.raises(ValueError, match="expand must exceed 1"):
        _ = run(expand=1.0)
    with pytest.raises(ValueError, match="not above ambient"):
        _ = run(limit_temperature=300 * U.K)
    with pytest.raises(ValueError, match="seed current must be positive"):
        _ = run(seed=LinearSeed(scale=1.0, max_current=0 * U.A))


def test_voltage_drive_rejects_degenerate_arguments() -> None:
    with pytest.raises(ValueError, match="current_rtol must be positive"):
        _ = VoltageDrive(current_of=_current_of, reference_current=1 * U.A, current_rtol=0)
    with pytest.raises(ValueError, match="max_inner must be at least 1"):
        _ = VoltageDrive(current_of=_current_of, reference_current=1 * U.A, max_inner=0)
    with pytest.raises(ValueError, match="reference current is zero"):
        _ = VoltageDrive(current_of=_current_of, reference_current=0 * U.A)


# --- scale_fixed_potentials ---------------------------------------------------


def test_scale_fixed_potentials_scales_electrodes_only_and_copies() -> None:
    convection = Convection(coefficient=quantity(10.0, "watt / meter**2 / kelvin"), ext_temperature=300 * U.K)
    config = SimulationConfig()
    config.domain.tags.append(EntityTag(selector={TagKey.CURRENT_DIR: CurrentDir.ENTRY}, conditions=[FixedPotential(value=2 * U.V), convection]))
    config.domain.tags.append(EntityTag(selector={TagKey.CURRENT_DIR: CurrentDir.EXIT}, conditions=[FixedPotential(value=0 * U.V)]))
    config.domain.tags.append(EntityTag(selector={TagKey.PHASE: "0"}))

    scaled = scale_fixed_potentials(config, 3.0)

    entry, exit_, bare = scaled.domain.tags
    assert isinstance(entry.conditions[0], FixedPotential)
    assert entry.conditions[0].value.to(U.V).magnitude == pytest.approx(6.0)
    # Non-electrode conditions and condition-less tags ride through as the same objects.
    assert entry.conditions[1] is convection
    assert bare is config.domain.tags[2]
    # A grounded exit stays grounded.
    assert isinstance(exit_.conditions[0], FixedPotential)
    assert exit_.conditions[0].value.to(U.V).magnitude == 0.0
    # The original config is untouched.
    original = config.domain.tags[0].conditions[0]
    assert isinstance(original, FixedPotential)
    assert original.value.to(U.V).magnitude == pytest.approx(2.0)


def test_scale_fixed_potentials_rejects_bad_scale_and_driveless_config() -> None:
    config = _drive_config()
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="positive finite"):
            _ = scale_fixed_potentials(config, bad)
    with pytest.raises(ValueError, match="no FixedPotential"):
        _ = scale_fixed_potentials(SimulationConfig(), 2.0)
