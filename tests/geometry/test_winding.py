"""Round-wire winding packer: layer stacks, wire positions, and derived electrical quantities."""

import math

import pytest

import femforge.geometry.winding as winding_module
from femforge.geometry.winding import CoilWindow, LengthFit, OuterDiameterFit, Wire, WoundCoil
from femforge.materials.copper import CONDUCTIVITY_AT_REFERENCE, REFERENCE_TEMPERATURE, RESISTIVITY_TEMPERATURE_COEFFICIENT


WIRE = Wire(copper_diameter=0.8e-3, insulation_diameter=1.0e-3)
WINDOW = CoilWindow(inner_radius=10.0e-3, outer_radius=12.75e-3, length=5.5e-3)


def test_wire_validation_and_areas():
    with pytest.raises(ValueError, match="copper diameter"):
        Wire(copper_diameter=1.1e-3, insulation_diameter=1.0e-3)
    with pytest.raises(ValueError, match="copper diameter"):
        Wire(copper_diameter=0.0, insulation_diameter=1.0e-3)

    assert WIRE.copper_area == pytest.approx(math.pi / 4 * 0.64e-6)
    assert WIRE.insulation_area == pytest.approx(math.pi / 4 * 1.0e-6)


def test_window_validation_and_measures():
    with pytest.raises(ValueError, match="inner radius"):
        CoilWindow(inner_radius=5e-3, outer_radius=4e-3, length=1e-3)
    with pytest.raises(ValueError, match="length"):
        CoilWindow(inner_radius=1e-3, outer_radius=2e-3, length=0.0)

    assert WINDOW.area == pytest.approx(2.75e-3 * 5.5e-3)
    assert WINDOW.mean_radius == pytest.approx(11.375e-3)
    assert WINDOW.volume == pytest.approx(2 * math.pi * 11.375e-3 * 2.75e-3 * 5.5e-3)


def test_tangent_under_stack():
    coil = WoundCoil.wind(WIRE, WINDOW)

    assert coil.axial_pitch == pytest.approx(1.0e-3)
    assert coil.radial_pitch == pytest.approx(math.sqrt(3) / 2 * 1.0e-3)
    assert coil.turns_per_layer == (5, 4, 5)
    assert coil.turns == 14
    assert coil.num_layers == 3

    assert coil.actual_outer_diameter == pytest.approx(2 * (10.5e-3 + 2 * coil.radial_pitch + 0.5e-3))
    assert coil.actual_outer_diameter <= 2 * WINDOW.outer_radius
    assert coil.actual_length == pytest.approx(5.0e-3)
    assert coil.end_margin == pytest.approx(0.25e-3)
    assert coil.splay_gap == pytest.approx(0.0)


def test_over_includes_the_crossing_layer():
    coil = WoundCoil.wind(WIRE, WINDOW, outer_diameter_fit=OuterDiameterFit.OVER)

    assert coil.turns_per_layer == (5, 4, 5, 4)
    assert coil.actual_outer_diameter >= 2 * WINDOW.outer_radius


def test_exact_od_tangency_agrees_between_fits():
    exact = CoilWindow(inner_radius=10.0e-3, outer_radius=10.5e-3 + 2 * (math.sqrt(3) / 2 * 1.0e-3) + 0.5e-3, length=5.5e-3)

    under = WoundCoil.wind(WIRE, exact, outer_diameter_fit=OuterDiameterFit.UNDER)
    over = WoundCoil.wind(WIRE, exact, outer_diameter_fit=OuterDiameterFit.OVER)

    assert under.turns_per_layer == over.turns_per_layer == (5, 4, 5)


def test_exact_integer_length_fit():
    window = CoilWindow(inner_radius=10.0e-3, outer_radius=12.75e-3, length=5.0e-3)
    coil = WoundCoil.wind(WIRE, window)

    assert coil.turns_per_layer[0] == 5
    assert coil.end_margin == pytest.approx(0.0)


def test_splayed_fills_the_window():
    coil = WoundCoil.wind(WIRE, WINDOW, length_fit=LengthFit.SPLAYED)

    assert coil.axial_pitch == pytest.approx(1.125e-3)
    assert coil.splay_gap == pytest.approx(0.125e-3)
    assert coil.end_margin == pytest.approx(0.0)
    assert coil.actual_length == pytest.approx(WINDOW.length)
    assert coil.radial_pitch == pytest.approx(math.sqrt(1.0e-6 - (0.5625e-3) ** 2))

    edges = [turn.axial_position for turn in coil.wire_coordinates() if turn.layer == 0]
    assert min(edges) - 0.5e-3 == pytest.approx(-WINDOW.length / 2)
    assert max(edges) + 0.5e-3 == pytest.approx(WINDOW.length / 2)


def test_splayed_two_wire_extreme_stays_real():
    window = CoilWindow(inner_radius=10.0e-3, outer_radius=12.0e-3, length=2.9e-3)
    coil = WoundCoil.wind(WIRE, window, length_fit=LengthFit.SPLAYED)

    assert coil.turns_per_layer[0] == 2
    assert coil.axial_pitch == pytest.approx(1.9e-3)
    assert coil.radial_pitch == pytest.approx(math.sqrt(1.0e-6 - (0.95e-3) ** 2))


def test_wind_rejects_impossible_windows():
    with pytest.raises(ValueError, match="at least 2"):
        WoundCoil.wind(WIRE, CoilWindow(inner_radius=10e-3, outer_radius=12e-3, length=1.5e-3))
    with pytest.raises(ValueError, match="radial depth"):
        WoundCoil.wind(WIRE, CoilWindow(inner_radius=10e-3, outer_radius=10.9e-3, length=5.5e-3))


def test_over_accepts_a_single_oversize_layer():
    coil = WoundCoil.wind(WIRE, CoilWindow(inner_radius=10e-3, outer_radius=10.9e-3, length=5.5e-3), outer_diameter_fit=OuterDiameterFit.OVER)

    assert coil.turns_per_layer == (5,)
    assert coil.actual_outer_diameter == pytest.approx(22.0e-3)


@pytest.mark.parametrize("length_fit", [LengthFit.TANGENT, LengthFit.SPLAYED])
def test_wires_nest_tangent_and_never_overlap(length_fit: LengthFit):
    coil = WoundCoil.wind(WIRE, WINDOW, length_fit=length_fit)
    turns = coil.wire_coordinates()

    assert len(turns) == coil.turns
    assert [turn.layer for turn in turns] == [layer for layer, count in enumerate(coil.turns_per_layer) for _ in range(count)]

    diameter = WIRE.insulation_diameter
    for a in turns:
        for b in turns:
            if a is b:
                continue
            distance = math.hypot(a.radius - b.radius, a.axial_position - b.axial_position)
            assert distance >= diameter - 1e-12
            if abs(a.layer - b.layer) == 1 and abs(a.axial_position - b.axial_position) < coil.axial_pitch:
                assert distance == pytest.approx(diameter)


def test_wire_length_includes_the_helical_advance():
    coil = WoundCoil.wind(WIRE, WINDOW)

    by_hand = sum(math.hypot(2 * math.pi * turn.radius, coil.axial_pitch) for turn in coil.wire_coordinates())
    planar = sum(2 * math.pi * turn.radius for turn in coil.wire_coordinates())

    assert coil.wire_length == pytest.approx(by_hand)
    assert coil.wire_length > planar


def test_copper_mass_is_length_times_section_times_density():
    coil = WoundCoil.wind(WIRE, WINDOW)

    assert coil.copper_mass == pytest.approx(coil.wire_length * WIRE.copper_area * 8960.0)


def test_copper_mass_requires_a_material_density(monkeypatch: pytest.MonkeyPatch):
    class _NoDensityCopper:
        def __init__(self) -> None:
            self.mechanical = type("M", (), {"density": None})()

    monkeypatch.setattr(winding_module, "Copper", _NoDensityCopper)
    with pytest.raises(ValueError, match="no density"):
        _ = WoundCoil.wind(WIRE, WINDOW).copper_mass


def test_resistance_follows_the_copper_alpha_law():
    coil = WoundCoil.wind(WIRE, WINDOW)

    assert coil.coil_resistance() == pytest.approx(coil.wire_length / (CONDUCTIVITY_AT_REFERENCE * WIRE.copper_area))
    assert coil.coil_resistance(REFERENCE_TEMPERATURE + 80.0) == pytest.approx(coil.coil_resistance() * (1.0 + RESISTIVITY_TEMPERATURE_COEFFICIENT * 80.0))


def test_fill_factors_bounded_by_hex_packing():
    coil = WoundCoil.wind(WIRE, WINDOW)

    assert coil.mechanical_fill_factor == pytest.approx(14 * WIRE.insulation_area / WINDOW.area)
    assert coil.mechanical_fill_factor < math.pi / (2 * math.sqrt(3))
    assert coil.copper_fill_factor == pytest.approx(coil.mechanical_fill_factor * 0.64)


def test_homogenized_drive_quantities():
    coil = WoundCoil.wind(WIRE, WINDOW)
    current = -2.0
    hot = REFERENCE_TEMPERATURE + 50.0

    assert coil.window_current_density(current) == pytest.approx(14 * current / WINDOW.area)
    assert coil.heat_density(current) == pytest.approx(current**2 * coil.coil_resistance() / WINDOW.volume)
    assert coil.heat_density(current, hot) == pytest.approx(current**2 * coil.coil_resistance(hot) / WINDOW.volume)
    assert coil.wire_current_density(current) == pytest.approx(2.0 / WIRE.copper_area)


def test_tubular_26awg_regression():
    # The original motor's coil window: ID 24 mm, OD 24 + 2·(τ/π) mm, length 5.35 mm.
    wire = Wire(copper_diameter=0.405e-3, insulation_diameter=0.44704e-3)
    window = CoilWindow(inner_radius=12.0e-3, outer_radius=(24.0 + 2 * (9.525 / math.pi)) / 2 * 1e-3, length=5.35e-3)

    coil = WoundCoil.wind(wire, window)
    assert coil.turns_per_layer == (11, 10, 11, 10, 11, 10, 11)
    assert coil.turns == 74
    assert 0.79 < coil.coil_resistance() < 0.83

    over = WoundCoil.wind(wire, window, outer_diameter_fit=OuterDiameterFit.OVER)
    assert over.num_layers == 8
