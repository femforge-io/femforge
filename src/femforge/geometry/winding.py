"""Round-wire winding packer: the orthocyclic layer stack a wire settles into in a rectangular window, every wire position, and the derived electrical quantities."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from femforge.materials.copper import CONDUCTIVITY_AT_REFERENCE, REFERENCE_TEMPERATURE, RESISTIVITY_TEMPERATURE_COEFFICIENT, Copper


_COUNT_TOL = 1e-9
"""Relative slack when counting wires against the window length, so an exact integer fit survives float dust."""

_DIAMETER_TOL = 1e-12
"""`[m]`: Absolute slack when comparing a layer's build against the window OD; sub-picometre, far below any real dimension."""


class OuterDiameterFit(StrEnum):
    """How the specified window OD bounds the layer stack."""

    UNDER = "under"
    """Keep only layers that stay inside the specified OD: actual coil OD <= specified."""

    OVER = "over"
    """Stop after the first layer that reaches the specified OD: actual coil OD >= specified."""


class LengthFit(StrEnum):
    """How the wires occupy the specified window length."""

    TANGENT = "tangent"
    """The largest integer count of mutually tangent wires, centered in the window; an end gap likely remains."""

    SPLAYED = "splayed"
    """The base layer spans the window wall to wall: the same wire count, splayed to an axial pitch above the wire OD."""


@dataclass(frozen=True)
class Wire:
    """Round magnet wire."""

    copper_diameter: float
    """`d_cu` `[m]`: Bare copper diameter."""

    insulation_diameter: float
    """`d_ins` `[m]`: Overall diameter including the enamel."""

    def __post_init__(self) -> None:
        if not 0.0 < self.copper_diameter <= self.insulation_diameter:
            raise ValueError(f"wire needs 0 < copper diameter <= insulation diameter, got {self.copper_diameter} / {self.insulation_diameter} m.")

    @property
    def copper_area(self) -> float:
        """`a_cu` `[m²]`: Bare copper cross-section."""
        return math.pi / 4 * self.copper_diameter**2

    @property
    def insulation_area(self) -> float:
        """`a_ins` `[m²]`: Insulated-wire cross-section."""
        return math.pi / 4 * self.insulation_diameter**2


@dataclass(frozen=True)
class CoilWindow:
    """One coil's revolved winding window: the annular slot between the mandrel and the coil OD."""

    inner_radius: float
    """`r_in` `[m]`: Radius of the window's inner wall, where the first layer seats."""

    outer_radius: float
    """`r_out` `[m]`: Radius of the window's outer wall (the CAD coil OD); the last layer may stand proud of it under OVER fit."""

    length: float
    """`w` `[m]`: Axial length of the window: the coil width, which the geometry sets equal to the magnet length."""

    def __post_init__(self) -> None:
        if not 0.0 <= self.inner_radius < self.outer_radius:
            raise ValueError(f"window needs 0 <= inner radius < outer radius, got {self.inner_radius} / {self.outer_radius} m.")
        if self.length <= 0.0:
            raise ValueError(f"window length must be positive, got {self.length} m.")

    @property
    def area(self) -> float:
        """`A_w` `[m²]`: The r-z cross-section of the window: radial depth × axial length."""
        return (self.outer_radius - self.inner_radius) * self.length

    @property
    def mean_radius(self) -> float:
        """`r_mean` `[m]`: Mid-radius of the window."""
        return (self.outer_radius + self.inner_radius) / 2

    @property
    def volume(self) -> float:
        """`V_w` `[m³]`: The revolved window volume, 2π·r_mean·A_w."""
        return 2 * math.pi * self.mean_radius * self.area


@dataclass(frozen=True)
class WireTurn:
    """One turn's centerline circle."""

    layer: int
    """Zero-based layer index; layer 0 seats on the window's inner radius."""

    index: int
    """Zero-based position along the layer."""

    radius: float
    """`r` `[m]`: Radius of the turn's centerline."""

    axial_position: float
    """`z` `[m]`: Axial position of the turn's center, measured from the window mid-plane."""


@dataclass(frozen=True)
class WoundCoil:
    """One packed coil, computed by [`wind`][]; every quantity derives from the wire and window alone."""

    wire: Wire
    """The wound wire."""

    window: CoilWindow
    """The window the wire fills."""

    outer_diameter_fit: OuterDiameterFit
    """Whether the stack stops under or just over the specified window OD."""

    length_fit: LengthFit
    """Whether the base layer is tangent-packed or splayed wall to wall."""

    axial_pitch: float
    """`p_z` `[m]`: Center-to-center spacing of neighbouring turns in a layer: the wire OD when tangent, wider when splayed."""

    radial_pitch: float
    """`p_r` `[m]`: Center-to-center radial rise from one layer to the next, the groove-nested √(d² − (p_z/2)²)."""

    turns_per_layer: tuple[int, ...]
    """Turn count of each layer from the inside out; nested layers alternate n and n − 1."""

    @classmethod
    def wind(
        cls,
        wire: Wire,
        window: CoilWindow,
        outer_diameter_fit: OuterDiameterFit = OuterDiameterFit.UNDER,
        length_fit: LengthFit = LengthFit.TANGENT,
    ) -> WoundCoil:
        """Pack the wire into the window: nested layers alternate n and n-1 turns, upper wires tangent in the grooves of the layer below.

        Args:
            wire: The wire to wind.
            window: The window to fill.
            outer_diameter_fit: Whether the stack stops under or just over the window OD.
            length_fit: Whether the base layer is tangent-packed or splayed wall to wall.
        """
        diameter = wire.insulation_diameter
        base_turns = math.floor(window.length / diameter + _COUNT_TOL)
        if base_turns < 2:
            raise ValueError(f"window length {window.length} m fits {base_turns} wires of OD {diameter} m; the nested-layer model needs at least 2 in the base layer.")

        if length_fit is LengthFit.TANGENT:
            axial_pitch = diameter
        else:
            axial_pitch = (window.length - diameter) / (base_turns - 1)

        # A groove wire touches both neighbours (center distance d) at a radial rise of sqrt(d^2 - (p/2)^2);
        # p < 2d always holds because base_turns is the floor count, so the sqrt stays real and positive.
        radial_pitch = math.sqrt(diameter**2 - (axial_pitch / 2) ** 2)

        seat_radius = window.inner_radius + diameter / 2
        turns_per_layer: list[int] = []
        layer = 0
        while True:
            build_radius = seat_radius + layer * radial_pitch + diameter / 2

            if outer_diameter_fit is OuterDiameterFit.UNDER and build_radius > window.outer_radius + _DIAMETER_TOL:
                break

            turns_per_layer.append(base_turns if layer % 2 == 0 else base_turns - 1)
            layer += 1

            if outer_diameter_fit is OuterDiameterFit.OVER and build_radius >= window.outer_radius - _DIAMETER_TOL:
                break

        if not turns_per_layer:
            raise ValueError(f"window radial depth {window.outer_radius - window.inner_radius} m cannot hold one layer of OD {diameter} m wire under {outer_diameter_fit}.")

        return cls(
            wire=wire,
            window=window,
            outer_diameter_fit=outer_diameter_fit,
            length_fit=length_fit,
            axial_pitch=axial_pitch,
            radial_pitch=radial_pitch,
            turns_per_layer=tuple(turns_per_layer),
        )

    # -- geometry ------------------------------------------------------------

    @property
    def turns(self) -> int:
        """`N` `[-]`: Wire turns in the coil."""
        return sum(self.turns_per_layer)

    @property
    def num_layers(self) -> int:
        """The number of layers in the stack."""
        return len(self.turns_per_layer)

    @property
    def layer_radii(self) -> tuple[float, ...]:
        """`[m]`: Each layer's wire-center radius, layer 0 seated on the window's inner radius."""
        seat_radius = self.window.inner_radius + self.wire.insulation_diameter / 2
        return tuple(seat_radius + layer * self.radial_pitch for layer in range(self.num_layers))

    def wire_coordinates(self) -> tuple[WireTurn, ...]:
        """Every turn's centerline circle, layer-major; odd layers sit in the grooves, offset by half the axial pitch."""
        base_turns = self.turns_per_layer[0]
        first_position = -(base_turns - 1) * self.axial_pitch / 2

        turns: list[WireTurn] = []
        for layer, (radius, count) in enumerate(zip(self.layer_radii, self.turns_per_layer)):
            start = first_position + (self.axial_pitch / 2 if layer % 2 else 0.0)
            for index in range(count):
                turns.append(WireTurn(layer=layer, index=index, radius=radius, axial_position=start + index * self.axial_pitch))
        return tuple(turns)

    @property
    def actual_outer_diameter(self) -> float:
        """`[m]`: The built coil's outer diameter: the outermost layer plus one wire radius."""
        return 2 * (self.layer_radii[-1] + self.wire.insulation_diameter / 2)

    @property
    def actual_length(self) -> float:
        """`[m]`: The base layer's axial extent; upper layers are inset by half a pitch and never wider."""
        return (self.turns_per_layer[0] - 1) * self.axial_pitch + self.wire.insulation_diameter

    @property
    def end_margin(self) -> float:
        """`[m]`: The gap between the base layer and each window wall (zero when splayed)."""
        return (self.window.length - self.actual_length) / 2

    @property
    def splay_gap(self) -> float:
        """`[m]`: The clearance between neighbouring wires in a layer (zero when tangent)."""
        return self.axial_pitch - self.wire.insulation_diameter

    # -- electrical ----------------------------------------------------------

    @property
    def wire_length(self) -> float:
        """`ℓ_wire` `[m]`: Total wire length: each turn's circumference with the helical advance of one axial pitch."""
        return sum(math.hypot(2 * math.pi * turn.radius, self.axial_pitch) for turn in self.wire_coordinates())

    @property
    def copper_mass(self) -> float:
        """`m_wire` `[kg]`: Copper mass of the wire: length × bare-copper section × the Copper material's density."""
        return self.wire_length * self.wire.copper_area * _copper_density()

    def coil_resistance(self, temperature: float = REFERENCE_TEMPERATURE) -> float:
        """`R_coil` `[Ω]`: Per-coil DC resistance at ``temperature`` (K), by the same α law the FEA's σ(T) emits."""
        at_reference = self.wire_length / (CONDUCTIVITY_AT_REFERENCE * self.wire.copper_area)
        return at_reference * (1.0 + RESISTIVITY_TEMPERATURE_COEFFICIENT * (temperature - REFERENCE_TEMPERATURE))

    @property
    def copper_fill_factor(self) -> float:
        """`f_cu` `[-]`: Copper cross-section over the specified window cross-section."""
        return self.turns * self.wire.copper_area / self.window.area

    @property
    def mechanical_fill_factor(self) -> float:
        """`f_mech` `[-]`: Insulated-wire cross-section over the specified window cross-section; hex packing bounds it at π/(2√3) ≈ 0.907."""
        return self.turns * self.wire.insulation_area / self.window.area

    # -- homogenized drive ---------------------------------------------------

    def window_current_density(self, current: float) -> float:
        """`J` `[A/m²]`: Homogenized azimuthal current density (signed): the ampere-turns spread over the specified window area — the FEA coil body's drive."""
        return self.turns * current / self.window.area

    def heat_density(self, current: float, temperature: float = REFERENCE_TEMPERATURE) -> float:
        """`q` `[W/m³]`: Per-coil Joule density, I²·R_coil(T) over the revolved window volume."""
        return current**2 * self.coil_resistance(temperature) / self.window.volume

    def wire_current_density(self, current: float) -> float:
        """`J_wire` `[A/m²]`: Current density in the copper itself."""
        return abs(current) / self.wire.copper_area


def _copper_density() -> float:
    """`ρ_cu` `[kg/m³]`: The Copper material's density."""
    density = Copper().mechanical.density
    if density is None:
        raise ValueError("the Copper material carries no density; the wire mass needs one.")
    return float(density().to("kg/m**3").magnitude)
