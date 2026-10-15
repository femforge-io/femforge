"""The magpylib source model + label matching: `magnet_source` and the two label matchers over a hand-built `DomainConfig`."""

from __future__ import annotations

import pytest

from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.materials.air import Air
from femforge.materials.neodymium import N52
from femforge.materials.properties import PERMEABILITY_OF_VACUUM, MaterialProperties
from femforge.physical.conditions import Convection, Magnetization
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import U, quantity
from femforge.validation.sources import (
    MagnetSource,
    magnet_source,
    magnetization_for_label,
    material_for_label,
)


def _n52_remanence(t_k: float) -> float:
    """N52's Bᵣ(T) = 1.48·(1 − 0.0012·(T − 293.15)) T; the sources module evaluates it at the given `at` (default 300 K)."""
    return 1.48 * (1 - 0.0012 * (t_k - 293.15))


N52_REMANENCE_T = _n52_remanence(300.0)


_N52_LABEL = "Magnetization=N, Material=N52, Slot=1"
"""A magnet label in the authored key=value convention (see geometry.magnet.create_halbach)."""


def _domain(*, north: Direction | None = Direction(0, 1, 0)) -> DomainConfig:
    """N52 + Air materials with a single north magnetization slot (direction overridable)."""
    return DomainConfig(
        materials=[N52(), Air()],
        tags=[EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=north)])],
    )


# --- material_for_label -----------------------------------------------------


def test_material_matches_by_casefold_value() -> None:
    materials: list[MaterialProperties] = [N52(), Air()]
    matched = material_for_label("Magnetization=N, Material=n52", materials)  # casefold "n52" matches the "N52" tag
    assert matched is not None and matched.tag == "N52"


def test_material_returns_air_for_the_air_box() -> None:
    matched = material_for_label("Material=Air", [N52(), Air()])
    assert matched is not None and matched.tag == "Air"


def test_material_none_when_no_tag_appears() -> None:
    assert material_for_label("Coil_3", [N52(), Air()]) is None


# --- magnetization_for_label ------------------------------------------------


def test_magnetization_resolves_the_slot_direction() -> None:
    mag = magnetization_for_label(_N52_LABEL, _domain().tags)
    assert mag is not None
    assert mag.direction == Direction(0, 1, 0)


def test_magnetization_none_when_no_slot_tag_appears() -> None:
    assert magnetization_for_label("Magnetization=S, Material=N52, Slot=2", _domain().tags) is None


def test_magnetization_ignores_zero_direction_tags() -> None:
    tags = [EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Magnetization(direction=None)])]
    assert magnetization_for_label(_N52_LABEL, tags) is None


def test_magnetization_ignores_non_magnetization_conditions() -> None:
    # A tag carrying only a thermal condition resolves to no Magnetization.
    tags = [EntityTag(selector={TagKey.MAGNETIZATION: "N"}, conditions=[Convection(coefficient=quantity(10, U.W / (U.m**2 * U.K)), ext_temperature=quantity(300, U.K))])]
    assert magnetization_for_label(_N52_LABEL, tags) is None


# --- magnet_source ----------------------------------------------------------


def test_magnet_source_polarization_is_remanence_times_direction() -> None:
    src = magnet_source(_N52_LABEL, Vec3(0.01, 0.02, 0.03), Vec3(0.005, 0.005, 0.0254), _domain())
    assert src is not None
    assert src.polarization.x == pytest.approx(0.0)
    assert src.polarization.y == pytest.approx(N52_REMANENCE_T)  # J = μ₀·(Bᵣ/μ₀) = Bᵣ
    assert src.polarization.z == pytest.approx(0.0)
    # Centre/size pass straight through (already SI).
    assert (src.center.x, src.center.y, src.center.z) == (0.01, 0.02, 0.03)
    assert (src.size.x, src.size.y, src.size.z) == (0.005, 0.005, 0.0254)


def test_magnet_source_normalizes_the_authored_direction() -> None:
    # A raw, non-unit direction gives the same |J| = Bᵣ as a unit one; the condition normalizes.
    src = magnet_source(_N52_LABEL, Vec3(0, 0, 0), Vec3(1, 1, 1), _domain(north=Direction(0, 5, 0)))
    assert src is not None
    assert src.polarization.magnitude() == pytest.approx(N52_REMANENCE_T)


def test_magnet_source_none_for_non_magnet_body() -> None:
    # The air box matches Air, which has no remanence -> not a magnet -> skipped.
    assert magnet_source("Material=Air", Vec3(0, 0, 0), Vec3(1, 1, 1), _domain()) is None


def test_magnet_source_none_when_no_material_matches() -> None:
    assert magnet_source("Coil_3", Vec3(0, 0, 0), Vec3(1, 1, 1), _domain()) is None


def test_magnet_source_raises_for_magnet_without_direction_tag() -> None:
    # Material resolves (N52) but no magnetization slot tag matches -> misconfiguration.
    with pytest.raises(ValueError, match="no usable Magnetization direction"):
        _ = magnet_source("Magnetization=X, Material=N52, Slot=9", Vec3(0, 0, 0), Vec3(1, 1, 1), _domain())


def test_magnet_source_honours_an_explicit_operating_point() -> None:
    # An explicit `at` flows into remanence evaluation: a 350 K magnet is measurably weaker than the 300 K default.
    at = {"temperature": quantity(350, U.K)}
    src = magnet_source(_N52_LABEL, Vec3(0, 0, 0), Vec3(1, 1, 1), _domain(), at=at)
    assert src is not None and src.polarization.magnitude() == pytest.approx(_n52_remanence(350.0))
    assert src.polarization.magnitude() < N52_REMANENCE_T


def test_magnet_source_magnitude_matches_M_times_mu0() -> None:
    # Cross-check the J = μ₀·|M| identity against the material's own |M| = Bᵣ/μ₀.
    domain = _domain()
    src = magnet_source(_N52_LABEL, Vec3(0, 0, 0), Vec3(1, 1, 1), domain)
    assert src is not None
    m_magnitude = N52().magnetic.magnetization_magnitude(at={"temperature": quantity(300, U.K)})
    assert m_magnitude is not None
    assert src.polarization.magnitude() == pytest.approx(m_magnitude * PERMEABILITY_OF_VACUUM)


def test_magnet_source_round_trips_through_pydantic() -> None:
    # MagnetSource is a plain Pydantic model over Vec3 fields, so model_dump/validate round-trips.
    src = magnet_source(_N52_LABEL, Vec3(0.1, 0.2, 0.3), Vec3(0.005, 0.005, 0.0254), _domain())
    assert src is not None
    again = MagnetSource.model_validate(src.model_dump())
    assert again.polarization.y == pytest.approx(N52_REMANENCE_T)
