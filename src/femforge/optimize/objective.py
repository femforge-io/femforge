"""Objectives: a typed [`Result`][femforge.run.result.Result] reduced to a scalar to optimize, converted to SI before its units are stripped."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from femforge.elmer.variables import ElmerVariable
from femforge.physical.units import U
from femforge.run.result import Result


@runtime_checkable
class Objective(Protocol):
    """A result → scalar contract."""

    def __call__(self, result: Result) -> float: ...


class PhaseResistanceFromPower:
    """The mean per-phase resistance (Ω) of a current-conduction run, from the Joule volume integral: R = phases·V²/P.

    A run without a positive Joule power column scores ``nan``.
    """

    def __init__(self, *, drive_volts: float, phases: int = 3) -> None:
        """Score runs that drive all ``phases`` near-identical phases at once at ``drive_volts``."""
        self.drive_volts: float = drive_volts
        self.phases: int = phases

    def __call__(self, result: Result) -> float:
        joule_key: str = ElmerVariable.JOULE_HEATING.lower()
        power: float | None = next((value for name, value in result.raw.items() if joule_key in name.lower()), None)

        if power is None or power <= 0:
            return float("nan")
        return self.phases * self.drive_volts**2 / power


class NetForceMagnitude:
    """Maximize the net force magnitude on the moving array (``|force|`` in N).

    A solve that produced no force scores ``-inf``, so it never wins a maximization.
    """

    def __call__(self, result: Result) -> float:
        if result.force is None:
            return float("-inf")
        return result.force.to(U.N).magnitude.magnitude()
