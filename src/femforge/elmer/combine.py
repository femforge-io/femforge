"""Merges the keywords several conditions put in one sif block: loads add, fixed values must agree, and a load a fixed value overrides is rejected."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from femforge.common.utils import title
from femforge.elmer.config import SifValue
from femforge.physical.conditions import Combine, Condition


__all__ = ["Source", "Merged", "merge"]

TEMPERATURE_MATC = re.compile(r'^Variable Temperature\s+Real MATC "(?P<expression>[^"]*)"$')
"""A keyword value that varies with the solved temperature, such as a heat source with a temperature coefficient."""


@dataclass(frozen=True)
class Source:
    """One emission bound for a block: its keywords and where they came from."""

    kind: str
    """The condition kind, or the pseudo-kind of a block femforge emits on its own, such as ``far_field``."""

    condition: Condition | None
    """The authored condition; None for a block femforge emits on its own."""

    origin: str
    """Who set it, as an error names it, such as ``tag LeftFace``."""

    keywords: Mapping[str, SifValue]
    """The keywords it contributes."""


@dataclass(frozen=True)
class Merged:
    """One block's keywords after the merge, and every clash the merge found."""

    keywords: dict[str, SifValue]
    """The merged keywords; on a clash, the first value stays."""

    clashes: list[str]
    """One line per clash, naming the group, the conditions and their values."""


def _combine(source: Source) -> Combine:
    """How ``source``'s keywords combine; a block femforge emits on its own holds a fixed value."""
    return source.condition.combine if source.condition is not None else Combine.MATCH


def _shown(value: SifValue) -> str:
    """A keyword value as an error reads it."""
    return f"{value:.6g}" if isinstance(value, float) else str(value)


def _expression(value: SifValue) -> str | None:
    """``value`` as a MATC term in the solved temperature ``tx(0)``; None for a value that is neither a number nor such an expression."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return f"{value:.9g}"

    found: re.Match[str] | None = TEMPERATURE_MATC.match(value) if isinstance(value, str) else None

    return f"({found.group('expression')})" if found is not None else None


def _added(first: SifValue, second: SifValue) -> SifValue | None:
    """The sum of two load values: a number for two numbers, a MATC expression when either varies with temperature; None for values that do not add."""
    if isinstance(first, float | int) and isinstance(second, float | int) and not isinstance(first, bool) and not isinstance(second, bool):
        return first + second

    left: str | None = _expression(first)
    right: str | None = _expression(second)
    if left is None or right is None:
        return None

    return f'Variable Temperature\n    Real MATC "{left}+{right}"'


def _same(first: SifValue, second: SifValue) -> bool:
    """Whether two fixed values agree, numbers within rounding."""
    if isinstance(first, float | int) and isinstance(second, float | int) and not isinstance(first, bool) and not isinstance(second, bool):
        return math.isclose(first, second, rel_tol=1e-9, abs_tol=1e-15)

    return first == second


def merge(group: str, sources: Sequence[Source]) -> Merged:
    """Merges ``sources`` into one block's keywords: same-kind loads add, an ``ALONE`` kind appears once, any other repeated keyword keeps its value, and a load a fixed value overrides clashes.

    Args:
        group: The body or boundary group the block belongs to, as the clashes name it.
        sources: The emissions bound for the block.
    """
    keywords: dict[str, SifValue] = {}
    owners: dict[str, Source] = {}
    clashes: list[str] = []
    alone: dict[str, Source] = {}

    for source in sources:
        name: str = title(source.kind)
        if _combine(source) is Combine.ALONE:
            first: Source | None = alone.get(source.kind)
            if first is not None:
                clashes.append(f"{group}: two {name} conditions ({first.origin} and {source.origin}); keep one")
                continue

            alone[source.kind] = source

        for key, value in source.keywords.items():
            owner: Source | None = owners.get(key)
            if owner is None:
                keywords[key] = value
                owners[key] = source
                continue

            if _combine(source) is Combine.SUM and _combine(owner) is Combine.SUM and owner.kind == source.kind:
                total: SifValue | None = _added(keywords[key], value)
                if total is None:
                    clashes.append(f"{group}: {name} {key} values {_shown(keywords[key])} and {_shown(value)} cannot be added")
                else:
                    keywords[key] = total

                continue

            if not _same(keywords[key], value):
                clashes.append(f"{group}: {key} is {_shown(keywords[key])} from {title(owner.kind)} ({owner.origin}) but {_shown(value)} from {name} ({source.origin}); make them agree or remove one")

    for source in sources:
        if source.condition is None:
            continue

        overriding: list[str] = list(dict.fromkeys(fixed for _, fixed in source.condition.yields_to(source.keywords) if fixed in keywords))
        for fixed in overriding:
            held: Source = owners[fixed]
            clashes.append(f"{group}: {title(source.kind)} ({source.origin}) has no effect under the fixed {fixed} of {title(held.kind)} ({held.origin}); remove one")

    return Merged(keywords, clashes)
