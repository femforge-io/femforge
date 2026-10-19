"""Quantity and direction readouts on the unit ladders."""

import math

import pytest

from femforge.common.vector import Direction, Vec3
from femforge.physical.readout import LADDERS, along, readout, vector_readout
from femforge.physical.units import Quantity, U, Unit, quantity


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (quantity(0.02, "m"), "20 mm"),
        (quantity(1e-4, "m"), "0.1 mm"),
        (quantity(2e-4, "m**2"), "200 mm²"),
        (quantity(5e5, "Pa"), "500 kPa"),
        (quantity(999.7, "N"), "1 kN"),
        (quantity(12000, "N"), "12 kN"),
        (quantity(0.5, "V"), "0.5 V"),
        (quantity(1.2e6, "W/m**3"), "1.2 MW/m³"),
        (quantity(1500, "mm"), "1.5 m"),
        (quantity(0.0, "m"), "0 mm"),
        (quantity(-250.0, "N"), "-250 N"),
        (quantity(1.32, "T"), "1.32 T"),
    ],
)
def test_a_quantity_reads_on_its_ladder(value: Quantity, expected: str) -> None:
    assert readout(value) == expected


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (quantity(1500, "K"), U.K, "1500 K"),
        (quantity(2.5e6, "A/m**2"), U.A / U.mm**2, "2.5 A/mm²"),
        (quantity(10, "W/(m**2*K)"), U.W / (U.m**2 * U.K), "10 W/(m²·K)"),
        (quantity(2.0, "s"), U.ms, "2000 ms"),
    ],
)
def test_a_fixed_unit_overrides_the_ladder(value: Quantity, unit: Unit, expected: str) -> None:
    assert readout(value, unit=unit) == expected


def test_a_quantity_without_a_ladder_reads_compacted() -> None:
    assert readout(quantity(1500.0, "J")) == "1.5 kJ"


def test_every_ladder_lists_its_rungs_smallest_first() -> None:
    for ladder in LADDERS:
        sizes = [quantity(1.0, rung.unit).to_base_units().magnitude for rung in ladder.rungs]

        assert sizes == sorted(sizes)


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        (Vec3(1, 0, 0), "(1, 0, 0)"),
        (Vec3(0, -1, 0), "(0, -1, 0)"),
        (Vec3(0.6, 0.8, 0), "(0.6, 0.8, 0)"),
        (Vec3(1, 1, 1) / math.sqrt(3), "(0.577, 0.577, 0.577)"),
        (Direction(0, 0, -5), "(0, 0, -1)"),
        (Vec3(1e-12, -1e-12, 2.0), "(0, 0, 1)"),
    ],
)
def test_a_direction_reads_as_its_unit_components(direction: Vec3, expected: str) -> None:
    assert along(direction) == expected


def test_a_vector_reads_as_its_size_along_its_direction() -> None:
    assert vector_readout(quantity(Vec3(100, 0, 0), "N")) == "100 N Along (1, 0, 0)"
    assert vector_readout(quantity(Vec3(0, 0, 0), "N")) == "0 N"
