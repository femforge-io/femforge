"""Mesh convergence: re-run one configuration at increasing refinement, then Richardson-extrapolate the quantity of interest."""

from __future__ import annotations

import math
from typing import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from femforge.config import SimulationConfig
from femforge.physical.units import Quantity
from femforge.run.result import Result


# Extracts the scalar quantity of interest (e.g. force magnitude) from a Result. A plain callable for behaviour.
QuantityOf = Callable[[Result], Quantity]
# Evaluates one config to a Result — the driver's ``evaluate`` (or a fake in tests).
EvaluateFn = Callable[[SimulationConfig], Result]


class ConvergencePoint(BaseModel):
    """One rung of the refinement ladder: the refinement level, the resulting quantity, and its relative change from the previous (coarser) rung."""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    refinement: float
    """`r` `[-]`: Refinement factor applied to global_mesh_size (>1 = finer than the base)."""

    quantity: Quantity
    """`q` `[?]`: The quantity of interest at this level (a pint quantity, e.g. force magnitude in N)."""

    delta_vs_previous: float | None = None
    """`δq` `[-]`: Relative change |q − q_prev| / |q_prev| vs the previous rung; None for the first (no baseline)."""


def _relative_change(current: Quantity, previous: Quantity) -> float:
    """Dimensionless relative change between two same-dimensioned quantities, as a bare float."""
    ratio = abs(current - previous) / abs(previous)
    return float(ratio.to("dimensionless").magnitude)


def convergence_ladder(
    base_config: SimulationConfig,
    refinements: list[float],
    quantity_of: QuantityOf,
    evaluate: EvaluateFn,
) -> list[ConvergencePoint]:
    """Run ``base_config`` at each refinement level and report the quantity of interest plus its relative change between rungs.

    Args:
        base_config: The configuration to refine.
        refinements: Factors that divide ``global_mesh_size``; a factor above 1 is finer than the base.
        quantity_of: Extracts the quantity of interest from a result.
        evaluate: Solves one configuration, such as a driver's ``evaluate``.
    """
    points: list[ConvergencePoint] = []
    previous: Quantity | None = None

    for refinement in refinements:
        refined_size = base_config.mesh.global_mesh_size / refinement
        refined_mesh = base_config.mesh.model_copy(update={"global_mesh_size": refined_size})
        refined: SimulationConfig = base_config.model_copy(update={"mesh": refined_mesh})

        result: Result = evaluate(refined)
        quantity: Quantity = quantity_of(result)
        delta: float | None = None if previous is None else _relative_change(quantity, previous)

        points.append(ConvergencePoint(refinement=refinement, quantity=quantity, delta_vs_previous=delta))
        previous = quantity

    return points


class RichardsonEstimate(BaseModel):
    """Richardson extrapolation of a ladder's finest three rungs, assuming q(h) = q* + C·hᵖ."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    observed_order: float
    """`p` `[-]`: Observed convergence order in q(h) = q* + C·hᵖ; ``inf`` when the finest two rungs are identical."""

    extrapolated_value: float
    """`q*` `[?]`: The h→0 value, in the ladder's own units."""

    uncertainty: float
    """`|q* − q_finest|` `[?]`: The size of the extrapolation correction, the honest error scale of the finest rung."""


def _observed_order(r1: float, r2: float, ratio: float) -> float:
    """Solve r2^p·(r1^p − 1)/(r2^p − 1) = ratio for p (monotone increasing in p) by bisection."""
    g: Callable[[float], float] = lambda p: math.pow(r2, p) * (math.pow(r1, p) - 1.0) / (math.pow(r2, p) - 1.0) - ratio  # noqa: E731 - one-expression residual

    p_lo, p_hi = 1e-3, 16.0
    while g(p_hi) < 0 and p_hi < 64.0:
        p_hi *= 2.0
    if g(p_hi) < 0:
        raise ValueError(f"delta ratio {ratio:.4g} implies a convergence order above {p_hi:g}; the ladder data is not credible.")

    for _ in range(80):
        p_mid = 0.5 * (p_lo + p_hi)
        if g(p_mid) < 0:
            p_lo = p_mid
        else:
            p_hi = p_mid
    return 0.5 * (p_lo + p_hi)


def richardson(scales: Sequence[float], values: Sequence[float]) -> RichardsonEstimate:
    """Richardson-extrapolate the finest three rungs of a ladder whose ``scales`` are mesh-size multipliers (larger is coarser) paired with ``values``.

    Raises when fewer than three rungs are given, scales repeat, or the rung deltas are not shrinking.
    """
    if len(scales) != len(values):
        raise ValueError(f"scales and values differ in length ({len(scales)} vs {len(values)}).")
    if len(scales) < 3:
        raise ValueError(f"Richardson extrapolation needs at least three rungs, got {len(scales)}.")
    if any(scale <= 0 for scale in scales):
        raise ValueError("scales must be positive mesh-size multipliers.")
    if len(set(scales)) != len(scales):
        raise ValueError("scales repeat; each rung must be a distinct refinement.")

    ladder: list[tuple[float, float]] = sorted(zip(scales, values), key=lambda rung: -rung[0])
    (h1, q1), (h2, q2), (h3, q3) = ladder[-3:]

    d1: float = q1 - q2
    d2: float = q2 - q3
    if d2 == 0.0:
        return RichardsonEstimate(observed_order=math.inf, extrapolated_value=q3, uncertainty=0.0)

    ratio: float = d1 / d2
    if ratio <= 1.0:
        raise ValueError(f"rung deltas are not shrinking (ratio {ratio:.4g}); the finest three rungs are outside the asymptotic regime.")

    order: float = _observed_order(h1 / h2, h2 / h3, ratio)
    limit: float = q3 - d2 / (math.pow(h2 / h3, order) - 1.0)
    return RichardsonEstimate(observed_order=order, extrapolated_value=limit, uncertainty=abs(limit - q3))
