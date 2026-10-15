"""Helpers for the SIF value+comment format that material ``to_elmer()`` emits.

Each keyword value is a bare number or a ``"<number>  ! <unit>"`` string, where the trailing ``!`` comment documents the SI unit (inert to Elmer). These read the numeric value and the annotation.
"""

from __future__ import annotations


def sif_value(entry: float | str) -> float:
    """Numeric SI value of an entry, dropping any trailing ``! <unit>`` comment."""
    if isinstance(entry, str):
        return float(entry.split("!", 1)[0])
    return float(entry)


def sif_comment(entry: float | str) -> str | None:
    """The ``! <unit>`` annotation (text after ``!``), or None if the value carries no comment."""
    if isinstance(entry, str) and "!" in entry:
        return entry.split("!", 1)[1].strip()
    return None
