"""Max-current search: a closed-form I² seed plus a bracketed regula-falsi search for the largest current whose steady temperature stays at or below the limit.

The method is described at https://docs.femforge.io/user-guide/optimization/#maximum-current-search.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Literal, Protocol

from femforge.config import SimulationConfig
from femforge.physical.conditions import FixedPotential
from femforge.physical.domain import EntityTag
from femforge.physical.units import Quantity, U, quantity
from femforge.run.convergence import EvaluateFn, QuantityOf
from femforge.run.result import Result, RunStatus


@dataclass(frozen=True)
class LinearSeed:
    """The closed-form first-order estimate of the thermal current limit."""

    scale: float
    """`k_drive` `[-]`: Drive multiplier √(ΔT_limit/ΔT_ref); applies to the current and (σ static) the voltage drive alike."""

    max_current: Quantity
    """`I_seed` `[A]`: Estimated phase current at which the steady temperature reaches the limit."""


def linear_max_current_seed(*, reference_current: Quantity, max_temperature: Quantity, ambient_temperature: Quantity, limit_temperature: Quantity) -> LinearSeed:
    """The linear I² seed from one reference solve: I_seed = I_ref·√(ΔT_limit/ΔT_ref).

    Args:
        reference_current: The phase current of the reference solve.
        max_temperature: The reference solve's peak temperature.
        ambient_temperature: The ambient temperature both rises are measured from.
        limit_temperature: The temperature the coil may reach.
    """
    reference_rise: float = float((max_temperature - ambient_temperature).to(U.K).magnitude)
    allowed_rise: float = float((limit_temperature - ambient_temperature).to(U.K).magnitude)
    if reference_rise <= 0:
        raise ValueError(f"reference solve shows no temperature rise (ΔT_ref = {reference_rise} K); the seed is undefined without one.")
    if allowed_rise <= 0:
        raise ValueError(f"temperature limit {limit_temperature} is not above ambient {ambient_temperature}; no positive current satisfies it.")
    reference_magnitude: float = abs(float(reference_current.to(U.A).magnitude))
    if reference_magnitude == 0:
        raise ValueError("reference current is zero; the reference solve carried no current.")

    scale: float = math.sqrt(allowed_rise / reference_rise)
    return LinearSeed(scale=scale, max_current=quantity(reference_magnitude * scale, "ampere"))


def seed_from_result(result: Result, *, reference_current: Quantity, ambient_temperature: Quantity, limit_temperature: Quantity) -> LinearSeed:
    """The linear seed straight from a thermal [`Result`][femforge.run.result.Result] (its ``temperature_max``).

    Args:
        result: The reference solve.
        reference_current: The phase current the reference solve carried.
        ambient_temperature: The ambient temperature both rises are measured from.
        limit_temperature: The temperature the coil may reach.
    """
    if result.temperature_max is None:
        raise ValueError("result carries no temperature_max — was it produced by a physics stack with the SaveScalars max-Temperature export?")
    return linear_max_current_seed(
        reference_current=reference_current,
        max_temperature=result.temperature_max,
        ambient_temperature=ambient_temperature,
        limit_temperature=limit_temperature,
    )


StepOutcome = Literal["under", "over", "runaway"]
"""How one search step classified its target current: steady and at/under the limit, steady but over it, or no steady state at all."""


class SolveFailedError(RuntimeError):
    """A search solve failed for non-physical reasons (solver/tool error), so no bracket can be trusted from it."""


def _classify_failed(result: Result, target_current: float) -> None:
    """Raise [`SolveFailedError`][] unless the failure is a divergence (the physics signature of no steady state)."""
    if result.status is not RunStatus.DIVERGED:
        raise SolveFailedError(
            f"solve at {target_current:.4f} A failed with status {result.status.value!r} (run {result.run_id}, {result.run_dir}); "
            "a solver error is not a physics outcome — fix the solve before trusting any bracket."
        )


def scale_fixed_potentials(config: SimulationConfig, scale: float) -> SimulationConfig:
    """
    A copy of ``config`` with every [`FixedPotential`][femforge.physical.conditions.FixedPotential] value multiplied by ``scale``.

    Never mutates ``config``; grounded exits stay at 0 V, so every phase's ΔV scales by the same factor.
    """
    if not (math.isfinite(scale) and scale > 0):
        raise ValueError(f"drive scale must be a positive finite number, got {scale!r}.")

    found: bool = False
    tags: list[EntityTag] = []

    for tag in config.domain.tags:
        if not any(isinstance(condition, FixedPotential) for condition in tag.conditions):
            tags.append(tag)
            continue
        found = True
        conditions = [condition.model_copy(update={"value": condition.value * scale}) if isinstance(condition, FixedPotential) else condition for condition in tag.conditions]
        tags.append(tag.model_copy(update={"conditions": conditions}))

    if not found:
        raise ValueError("config carries no FixedPotential condition; there is no electrical drive to scale.")

    domain = config.domain.model_copy(update={"tags": tags})
    return config.model_copy(update={"domain": domain})


@dataclass(frozen=True)
class SearchStep:
    """One search step: a target phase current, realized by the drive and classified against the limit."""

    target_current: Quantity
    """`I_target` `[A]`: The phase current this step tried to realize."""

    outcome: StepOutcome
    """``under``/``over``: a steady state at the target with T_max at/under vs over the limit; ``runaway``: no steady state at this current."""

    drive_scale: float | None
    """`k_drive` `[-]`: The last voltage multiplier a [`VoltageDrive`][] applied to the base config; None for a prescribed-current drive."""

    measured_current: Quantity | None
    """`I_meas` `[A]`: The phase current the last converged solve measured (the target itself for a prescribed-current drive); None when that solve did not converge."""

    result: Result
    """The last solve of the step — the one that classified it."""

    evaluations: int
    """Coupled solves this step spent (one, unless an inner voltage iteration ran)."""


@dataclass(frozen=True)
class MaxCurrentSearch:
    """The search outcome: the bracket [``max_current``, ``upper_bracket_current``] around the thermal current limit, plus the audit trail."""

    max_current: Quantity
    """`I_max` `[A]`: The largest phase current confirmed to hold T_max at/below the limit (the final lower bracket)."""

    accepted: SearchStep
    """The step whose solve classified ``max_current`` — its ``result`` is the steady state at that current, so callers never re-solve to inspect it."""

    upper_bracket_current: Quantity | None
    """`I_upper` `[A]`: The smallest current confirmed over the limit or without a steady state; None when the search accepted ``max_current`` before ever exceeding it."""

    converged: bool
    """Whether the bracket narrowed to ``bracket_rtol`` (or ``max_current``'s temperature landed within ``temperature_rtol`` of the limit) within the step budget."""

    runaway: bool
    """Whether the standing upper bracket is a thermal-runaway limit (no steady state) rather than an over-limit steady temperature."""

    steps: list[SearchStep]
    """Every step taken, in order."""


def temperature_max_of(result: Result) -> Quantity:
    """The solve's global max-temperature scalar — the default search metric."""
    if result.temperature_max is None:
        raise ValueError("the solve carries no temperature_max — does the physics stack export the SaveScalars max-Temperature reduction?")
    return result.temperature_max


def _steady_outcome(result: Result, limit_temperature: float, temperature_of: QuantityOf) -> StepOutcome:
    """Classify a converged solve against the limit via the search's temperature metric."""
    over: bool = float(temperature_of(result).to(U.K).magnitude) > limit_temperature
    return "over" if over else "under"


class Drive(Protocol):
    """Realizes one target phase current against a base config and classifies the resulting steady state."""

    def run_step(
        self,
        *,
        evaluate: EvaluateFn,
        base_config: SimulationConfig,
        target_current: float,
        limit_temperature: float,
        temperature_of: QuantityOf,
    ) -> SearchStep:
        """Realize ``target_current`` against ``base_config`` and classify the steady state it reaches.

        Args:
            evaluate: Solves a config.
            base_config: The config every step starts from.
            target_current: The phase current to realize, in A.
            limit_temperature: The temperature limit, in K.
            temperature_of: Reads the limited temperature off a solved result.
        """
        ...


@dataclass
class VoltageDrive:
    """Voltage-actuated excitation: each step realizes its target current by a fixed-point voltage iteration V ← V·I_target/I_measured.

    A runaway step, a diverged solve or one whose every iterate undershoots, keeps the gain the step started with.
    """

    current_of: QuantityOf
    """Measures the realized phase current off a solved result (the same way ``reference_current`` was measured)."""

    reference_current: Quantity
    """`I_ref` `[A]`: The current the scale-1 base config produced — the initial amps-per-unit-scale gain."""

    current_rtol: float = 0.02
    """`[-]`: Relative tolerance for accepting a realized current against its target."""

    max_inner: int = 8
    """Inner-iteration solve budget per step."""

    _gain: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.current_rtol <= 0:
            raise ValueError(f"current_rtol must be positive, got {self.current_rtol}.")
        if self.max_inner < 1:
            raise ValueError(f"max_inner must be at least 1, got {self.max_inner}.")
        reference_magnitude: float = abs(float(self.reference_current.to(U.A).magnitude))
        if reference_magnitude == 0:
            raise ValueError("reference current is zero; the reference solve carried no current.")
        self._gain = reference_magnitude

    def run_step(
        self,
        *,
        evaluate: EvaluateFn,
        base_config: SimulationConfig,
        target_current: float,
        limit_temperature: float,
        temperature_of: QuantityOf,
    ) -> SearchStep:
        """Realize ``target_current`` by iterating the voltage scale until the measured current lands within ``current_rtol``.

        Args:
            evaluate: Solves a config.
            base_config: The config every step starts from.
            target_current: The phase current to realize, in A.
            limit_temperature: The temperature limit, in K.
            temperature_of: Reads the limited temperature off a solved result.
        """
        target: Quantity = quantity(target_current, "ampere")
        scale: float = target_current / self._gain
        undershoot: bool = True
        evaluations: int = 0

        while True:
            evaluations += 1
            scaled_config: SimulationConfig = scale_fixed_potentials(base_config, scale)
            result: Result = evaluate(scaled_config)

            if result.status is not RunStatus.OK:
                _classify_failed(result, target_current)
                return SearchStep(
                    target_current=target,
                    outcome="runaway",
                    drive_scale=scale,
                    measured_current=None,
                    result=result,
                    evaluations=evaluations,
                )

            measured: float = abs(float(self.current_of(result).to(U.A).magnitude))
            if measured == 0:
                raise ValueError(f"the solve at drive scale {scale} measured zero phase current; check current_of and the electrode boundaries.")

            if abs(measured - target_current) <= self.current_rtol * target_current:
                self._gain = measured / scale
                return SearchStep(
                    target_current=target,
                    outcome=_steady_outcome(result, limit_temperature, temperature_of),
                    drive_scale=scale,
                    measured_current=quantity(measured, "ampere"),
                    result=result,
                    evaluations=evaluations,
                )

            if measured > target_current:
                undershoot = False

            if evaluations == self.max_inner:
                if undershoot:
                    # Every iterate fell short while the voltage escalated: the terminal current has saturated below the target.
                    return SearchStep(
                        target_current=target,
                        outcome="runaway",
                        drive_scale=scale,
                        measured_current=quantity(measured, "ampere"),
                        result=result,
                        evaluations=evaluations,
                    )
                raise ValueError(
                    f"inner voltage iteration did not settle in {self.max_inner} solves at target {target_current} A (last measured {measured} A); loosen current_rtol or raise max_inner."
                )

            scale *= target_current / measured


@dataclass(frozen=True)
class DirectDrive:
    """Prescribed-current excitation: ``configure`` rebuilds the config for a target current exactly, so each step is one solve."""

    configure: Callable[[SimulationConfig, float], SimulationConfig]
    """Builds the config driving the given phase current (A) — e.g. rescaling CurrentDensity/HeatSource conditions."""

    def run_step(
        self,
        *,
        evaluate: EvaluateFn,
        base_config: SimulationConfig,
        target_current: float,
        limit_temperature: float,
        temperature_of: QuantityOf,
    ) -> SearchStep:
        """Realize ``target_current`` with one solve of the reconfigured config.

        Args:
            evaluate: Solves a config.
            base_config: The config every step starts from.
            target_current: The phase current to realize, in A.
            limit_temperature: The temperature limit, in K.
            temperature_of: Reads the limited temperature off a solved result.
        """
        target: Quantity = quantity(target_current, "ampere")
        result: Result = evaluate(self.configure(base_config, target_current))

        if result.status is not RunStatus.OK:
            _classify_failed(result, target_current)
            return SearchStep(target_current=target, outcome="runaway", drive_scale=None, measured_current=None, result=result, evaluations=1)

        return SearchStep(
            target_current=target,
            outcome=_steady_outcome(result, limit_temperature, temperature_of),
            drive_scale=None,
            measured_current=target,
            result=result,
            evaluations=1,
        )


def find_max_current(
    *,
    evaluate: EvaluateFn,
    base_config: SimulationConfig,
    seed: LinearSeed,
    drive: Drive,
    limit_temperature: Quantity,
    ambient_temperature: Quantity,
    temperature_of: QuantityOf = temperature_max_of,
    temperature_rtol: float = 0.02,
    bracket_rtol: float = 0.05,
    max_steps: int = 12,
    expand: float = 2.0,
) -> MaxCurrentSearch:
    """Find the largest phase current whose steady state stays at or below ``limit_temperature``, by regula falsi on the temperature residual in x = I² coordinates.

    Args:
        evaluate: Solves a config.
        base_config: The config every step starts from.
        seed: Supplies the first current to try.
        drive: Realizes each step's target current.
        limit_temperature: The temperature the limited metric may reach.
        ambient_temperature: The temperature at zero current.
        temperature_of: Reads the limited temperature off a result: the global hotspot by default, or one material's peak for a per-material limit.
        temperature_rtol: Accept once the temperature lies within this fraction of the allowed rise below the limit.
        bracket_rtol: Accept once the bracket narrows to this fraction of its upper current.
        max_steps: The step budget.
        expand: The largest factor the probe current grows by per step while no step has exceeded the limit.
    """
    if temperature_rtol <= 0:
        raise ValueError(f"temperature_rtol must be positive, got {temperature_rtol}.")
    if bracket_rtol <= 0:
        raise ValueError(f"bracket_rtol must be positive, got {bracket_rtol}.")
    if max_steps < 1:
        raise ValueError(f"max_steps must be at least 1, got {max_steps}.")
    if expand <= 1:
        raise ValueError(f"expand must exceed 1 (it caps the probe step while a bracket side is missing), got {expand}.")

    allowed_rise: float = float((limit_temperature - ambient_temperature).to(U.K).magnitude)
    if allowed_rise <= 0:
        raise ValueError(f"temperature limit {limit_temperature} is not above ambient {ambient_temperature}; no positive current satisfies it.")
    limit: float = float(limit_temperature.to(U.K).magnitude)

    seed_current: float = float(seed.max_current.to(U.A).magnitude)
    if not (math.isfinite(seed_current) and seed_current > 0):
        raise ValueError(f"seed current must be positive and finite, got {seed.max_current}.")

    # Anchors in x = I², residual f = T_max − T_limit (K). x = 0 is exact: zero current holds ambient.
    lo_x: float = 0.0
    lo_f: float = -allowed_rise
    lo_f_eff: float = -allowed_rise
    hi_x: float | None = None
    hi_f_eff: float | None = None  # None while the upper bracket is missing or runaway (no steady temperature there)
    last_side: Literal["lo", "hi"] | None = None
    runaway: bool = False
    steps: list[SearchStep] = []
    accepted: SearchStep | None = None

    target_x: float = seed_current**2

    def steady_temperature(step: SearchStep) -> float:
        return float(temperature_of(step.result).to(U.K).magnitude)

    while len(steps) < max_steps:
        step: SearchStep = drive.run_step(
            evaluate=evaluate,
            base_config=base_config,
            target_current=math.sqrt(target_x),
            limit_temperature=limit,
            temperature_of=temperature_of,
        )
        steps.append(step)

        # The realized current (a voltage drive lands within current_rtol of target) is the truthful sample abscissa.
        realized: Quantity | None = step.measured_current
        x: float = float(realized.to(U.A).magnitude) ** 2 if realized is not None else target_x

        if step.outcome == "under":
            f: float = steady_temperature(step) - limit
            if x > lo_x:
                lo_x, lo_f, lo_f_eff = x, f, f
                accepted = step
                if last_side == "lo" and hi_f_eff is not None:
                    hi_f_eff *= 0.5  # Illinois: the retained upper anchor is stale — damp it toward the root
                last_side = "lo"
            if -f <= temperature_rtol * allowed_rise:
                assert accepted is not None
                return MaxCurrentSearch(
                    max_current=quantity(math.sqrt(lo_x), "ampere"),
                    accepted=accepted,
                    upper_bracket_current=quantity(math.sqrt(hi_x), "ampere") if hi_x is not None else None,
                    converged=True,
                    runaway=runaway,
                    steps=steps,
                )
        elif hi_x is None or x < hi_x:
            hi_x = x
            hi_f_eff = steady_temperature(step) - limit if step.outcome == "over" else None
            if last_side == "hi":
                lo_f_eff *= 0.5
            last_side = "hi"
            runaway = step.outcome == "runaway"

        if hi_x is not None and lo_x > 0:
            lower_current, upper_current = math.sqrt(lo_x), math.sqrt(hi_x)
            if upper_current - lower_current <= bracket_rtol * upper_current:
                assert accepted is not None
                return MaxCurrentSearch(
                    max_current=quantity(lower_current, "ampere"),
                    accepted=accepted,
                    upper_bracket_current=quantity(upper_current, "ampere"),
                    converged=True,
                    runaway=runaway,
                    steps=steps,
                )

        rise_at_lower: float = allowed_rise + lo_f  # the observed rise at the lower anchor (0 at the virtual anchor)
        if hi_x is None:
            # No upper bracket yet: project the linear model through the lower anchors to the root, growth-capped.
            projected: float = lo_x * allowed_rise / rise_at_lower if rise_at_lower > 0 else math.inf
            target_x = min(projected, max(lo_x, target_x) * expand**2)
        else:
            guard: float = 0.05 * (hi_x - lo_x)
            if hi_f_eff is not None:
                # Both residuals known: Illinois-damped false position, clamped just inside the bracket.
                proposal: float = lo_x - lo_f_eff * (hi_x - lo_x) / (hi_f_eff - lo_f_eff)
                target_x = min(max(proposal, lo_x + guard), hi_x - guard)
            else:
                # Runaway upper bracket carries no residual: project from the lower anchors; a projection past the
                # bracket says nothing about where the runaway starts, so bisect instead of creeping along the edge.
                proposal = lo_x * allowed_rise / rise_at_lower if rise_at_lower > 0 else 0.5 * (lo_x + hi_x)
                target_x = proposal if lo_x + guard <= proposal <= hi_x - guard else 0.5 * (lo_x + hi_x)

    if lo_x == 0:
        raise ValueError(f"step budget ({max_steps} steps) found no current that holds T_max at or below {limit} K — is the limit realistically above ambient?")
    if hi_x is None:
        raise ValueError(f"step budget ({max_steps} steps) never exceeded the limit — the seed sits far below the true maximum; raise max_steps or expand.")

    assert accepted is not None
    return MaxCurrentSearch(
        max_current=quantity(math.sqrt(lo_x), "ampere"),
        accepted=accepted,
        upper_bracket_current=quantity(math.sqrt(hi_x), "ampere"),
        converged=False,
        runaway=runaway,
        steps=steps,
    )
