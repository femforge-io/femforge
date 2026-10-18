"""MaterialProperties Pydantic models + the material registry."""

from typing import cast

import pytest
from pydantic import ValidationError

from femforge.materials.air import Air
from femforge.materials.neodymium import N42, N52
from femforge.materials.pcb import FR4
from femforge.materials.properties import (
    MATERIAL_KEY_EQUIVALENTS,
    PERMEABILITY_OF_VACUUM,
    ElmerMaterialKeyword,
    MagneticProperties,
    MaterialProperties,
    ThermalProperties,
)
from femforge.materials.steel import Steel_316, Steel_1080
from femforge.materials.registry import all_materials, available_materials, material
from femforge.physical.property_functions import Calibration, CalibrationPoint, PropertyFunction, Static
from femforge.physical.units import DIM_FLUX_DENSITY, Quantity, U
from tests.physical._sif import sif_comment, sif_value

# Operating point required by to_elmer; ignored by the all-Static materials.
AT: dict[str, Quantity] = {"temperature": 300 * U.K}


def test_materials_construct_and_emit_si_floats() -> None:
    elmer = N52().to_elmer(at=AT)
    # Elmer wants bare SI numbers (optionally '! unit'-annotated), never a pint Quantity.
    assert sif_value(elmer["Density"]) == pytest.approx(7500.0)
    assert sif_value(elmer["Heat Conductivity"]) == pytest.approx(8.7)
    assert sif_value(elmer["Relative Permeability"]) == pytest.approx(1.05)
    assert not any(isinstance(v, U.Quantity) for v in elmer.values())
    # The SI unit annotation makes the emitted .sif self-documenting.
    assert sif_comment(elmer["Density"]) is not None


def test_copper_emits_conduction_solve_keywords() -> None:
    """The registered Copper carries the values the current/thermal solves key off; sigma is sigma(T)."""
    elmer = material("Copper").to_elmer(at=AT)
    # sigma(T) = 5.96e7 / (1 + 3.93e-3 (T - 293.15)), pre-evaluated at the 300 K operating point when no runtime variable is offered.
    sigma_300 = 5.96e7 / (1 + 3.93e-3 * (300 - 293.15))
    assert sif_value(elmer["Electric Conductivity"]) == pytest.approx(sigma_300)
    assert sif_value(elmer["Heat Conductivity"]) == pytest.approx(401.0)
    assert sif_value(elmer["Heat Capacity"]) == pytest.approx(385.0)
    assert sif_value(elmer["Density"]) == pytest.approx(8960.0)
    assert sif_value(elmer["Relative Permeability"]) == pytest.approx(1.0)
    assert sif_value(elmer["Relative Permittivity"]) == pytest.approx(1.0, rel=1e-3)
    assert not material("Copper").is_magnet
    # Conductivity and resistivity stay mutually consistent at the 20 degC reference (rho = 1/sigma(293.15 K)).
    conductivity = material("Copper").electrical.conductivity
    resistivity = material("Copper").electrical.resistivity
    assert conductivity is not None and resistivity is not None
    assert float(conductivity(temperature=293.15 * U.K).to(U.S / U.m).magnitude) == pytest.approx(5.96e7)
    assert float(resistivity(temperature=293.15 * U.K).to(U.ohm * U.m).magnitude) == pytest.approx(1 / 5.96e7, rel=2e-3)


def test_copper_sigma_emits_runtime_matc_under_thermal_coupling() -> None:
    """With temperature offered as a runtime variable, Copper emits sigma(T) as a MATC expression, not a float."""
    emitted = material("Copper").to_elmer(at=AT, runtime=frozenset({"temperature"}))
    sigma = emitted["Electric Conductivity"]
    assert isinstance(sigma, str)
    assert sigma.startswith("Variable Temperature")
    assert 'Real MATC "' in sigma
    assert "tx(0)" in sigma
    # The Lua backend renders the same law with Lua indexing.
    lua = material("Copper").to_elmer(at=AT, runtime=frozenset({"temperature"}), backend="lua")["Electric Conductivity"]
    assert isinstance(lua, str)
    assert 'Real LUA "' in lua and "tx[0]" in lua
    # Static properties are untouched by the runtime set.
    assert sif_value(emitted["Density"]) == pytest.approx(8960.0)


def test_magnet_detection_and_magnitude() -> None:
    n52 = N52()
    assert n52.is_magnet
    # |M| = Br(300 K) / mu0 with Br(T) = 1.48 (1 + alpha (T - 293.15)).
    assert n52.magnetic.magnetization_magnitude(at=AT) == pytest.approx(1.48 * (1 - 0.0012 * (300 - 293.15)) / PERMEABILITY_OF_VACUUM, rel=1e-6)
    assert not Air().is_magnet


def test_n42_remanence_and_coercivity_track_temperature() -> None:
    """Grade-N reversible coefficients: Br falls 0.12 %/K and Hcb 0.60 %/K from their 20 degC values."""
    n42 = material("N42")
    remanence, coercivity = n42.magnetic.remanence, n42.magnetic.coercivity
    assert remanence is not None and coercivity is not None
    assert float(remanence(temperature=293.15 * U.K).to(U.T).magnitude) == pytest.approx(1.30)
    assert float(coercivity(temperature=293.15 * U.K).to(U.A / U.m).magnitude) == pytest.approx(915e3)
    # At 80 degC (the grade's ceiling) the magnet is materially weaker, which is the point of authoring Br(T).
    hot: dict[str, Quantity] = {"temperature": 353.15 * U.K}
    assert float(remanence(**hot).to(U.T).magnitude) == pytest.approx(1.30 * (1 - 0.0012 * 60))
    assert n42.is_magnet
    hot_magnetization = n42.magnetic.magnetization_magnitude(at=hot)
    cold_magnetization = n42.magnetic.magnetization_magnitude(at=AT)
    assert hot_magnetization is not None and cold_magnetization is not None
    assert hot_magnetization < cold_magnetization


def test_neodymium_grades_differ_only_in_br_and_hcb() -> None:
    n52, n42 = N52(), N42()
    for magnet, br, hcb in ((n52, 1.48, 955e3), (n42, 1.30, 915e3)):
        remanence, coercivity = magnet.magnetic.remanence, magnet.magnetic.coercivity
        assert remanence is not None and coercivity is not None
        assert float(remanence(temperature=293.15 * U.K).to(U.T).magnitude) == pytest.approx(br)
        assert float(coercivity(temperature=293.15 * U.K).to(U.A / U.m).magnitude) == pytest.approx(hcb)
        # Hot-magnet weakening applies to every grade through the shared coefficients.
        assert float(remanence(temperature=353.15 * U.K).to(U.T).magnitude) == pytest.approx(br * (1 - 0.0012 * 60))
        assert float(coercivity(temperature=353.15 * U.K).to(U.A / U.m).magnitude) == pytest.approx(hcb * (1 - 0.0060 * 60))
    # The shared base values emit identically for both grades.
    shared = ["Density", "Youngs Modulus", "Poisson Ratio", "Heat Conductivity", "Heat Capacity", "Relative Permeability", "Electric Conductivity", "Relative Permittivity"]
    e52, e42 = n52.to_elmer(at=AT), n42.to_elmer(at=AT)
    for key in shared:
        assert e52[key] == e42[key], key
    assert sif_value(e52["Electric Conductivity"]) == pytest.approx(7.14e5)
    assert sif_value(e52["Heat Capacity"]) == pytest.approx(440.0)


# --- Steel H-B curve --------------------------------------------------------


def _hb_rows(table: str) -> list[tuple[float, float]]:
    """Parse the (B, H) rows out of an emitted H-B Curve table."""
    pairs = [line.split() for line in table.splitlines() if line.startswith("      ")]
    return [(float(b), float(h)) for b, h in pairs]


def test_steel_default_emits_nonlinear_hb_curve() -> None:
    elmer = material("Steel1080").to_elmer(at=AT)
    # The nonlinear material presents an H-B curve instead of a permeability.
    assert "Relative Permeability" not in elmer
    hb = elmer["H-B Curve"]
    assert isinstance(hb, str)
    assert hb.startswith('Variable "dummy"\n    Real Monotone Cubic\n')
    assert "      0.0 0.0\n" in hb  # the table passes through the origin
    assert "      1.08 1500.0\n" in hb  # the knee
    assert hb.endswith("    End")
    rows = _hb_rows(hb)
    bs = [b for b, _h in rows]
    assert bs == sorted(bs) and len(set(bs)) == len(bs), "B column must be strictly increasing"
    assert bs[-1] == pytest.approx(3.242)
    assert rows[-1][1] == pytest.approx(1_000_000.0)


def test_steel_linear_mode_freezes_a_static_mu_r() -> None:
    elmer = Steel_1080(rel_permeability=584.0).to_elmer(at=AT)
    assert sif_value(elmer["Relative Permeability"]) == pytest.approx(584.0)
    assert "H-B Curve" not in elmer
    assert Steel_1080(rel_permeability=584.0).magnetic.bh_curve is None


def test_steel_is_a_soft_conductive_flux_path() -> None:
    steel = material("Steel1080")
    assert steel.tag == "Steel1080"  # CAD STEP files author Material=Steel1080; the mesher matches on tag
    # No remanence: a soft flux path must not be classified as a permanent magnet.
    assert not steel.is_magnet
    assert steel.is_conductor(AT)
    elmer = steel.to_elmer(at=AT)
    assert sif_value(elmer["Density"]) == pytest.approx(7850.0)
    assert sif_value(elmer["Electric Conductivity"]) == pytest.approx(5.56e6)


def test_316_stainless_is_a_nonmagnetic_conductor() -> None:
    ss = material("Steel316")
    assert ss.tag == "Steel316"
    assert not ss.is_magnet
    assert ss.is_conductor(AT)
    elmer = ss.to_elmer(at=AT)
    # Austenitic: a near-unity static mu_r, never an H-B curve.
    assert "H-B Curve" not in elmer
    assert sif_value(elmer["Relative Permeability"]) == pytest.approx(1.008)
    assert sif_value(elmer["Density"]) == pytest.approx(8000.0)
    assert sif_value(elmer["Electric Conductivity"]) == pytest.approx(1.35e6)
    assert sif_value(elmer["Heat Conductivity"]) == pytest.approx(16.3)


def test_hb_curve_linear_method_emits_plain_real() -> None:
    points = [
        CalibrationPoint(inputs={"flux_density": 0.0 * U.T}, output=0.0 * U.A / U.m),
        CalibrationPoint(inputs={"flux_density": 1.0 * U.T}, output=800.0 * U.A / U.m),
    ]
    curve = Calibration(param_dims={"flux_density": DIM_FLUX_DENSITY}, points=points, method="linear")
    hb = MagneticProperties(bh_curve=curve).to_elmer(at=AT)["H-B Curve"]
    assert isinstance(hb, str)
    assert hb.startswith('Variable "dummy"\n    Real\n')
    assert "Monotone" not in hb


def test_hb_curve_requires_a_tabulated_calibration() -> None:
    magnetic = MagneticProperties(bh_curve=Static(value=100 * U.A / U.m))
    with pytest.raises(ValueError, match="tabulated Calibration"):
        _ = magnetic.to_elmer(at=AT)


def test_hb_curve_rejects_unsupported_interpolation() -> None:
    points = [
        CalibrationPoint(inputs={"flux_density": 0.0 * U.T}, output=0.0 * U.A / U.m),
        CalibrationPoint(inputs={"flux_density": 1.0 * U.T}, output=800.0 * U.A / U.m),
    ]
    curve = Calibration(param_dims={"flux_density": DIM_FLUX_DENSITY}, points=points, method="nearest")
    magnetic = MagneticProperties(bh_curve=curve)
    with pytest.raises(ValueError, match="'cubic' or 'linear'"):
        _ = magnetic.to_elmer(at=AT)


def test_material_key_equivalents_single_sources_the_hb_substitution() -> None:
    assert MATERIAL_KEY_EQUIVALENTS[ElmerMaterialKeyword.RELATIVE_PERMEABILITY] == frozenset({ElmerMaterialKeyword.H_B_CURVE})


def test_wrong_dimensionality_raises_on_construction() -> None:
    # A bare quantity is coerced into a Static; a wrong dimensionality fails.
    with pytest.raises(ValidationError):
        ThermalProperties(conductivity=cast(PropertyFunction, 5 * U.kg))


def test_wrong_dimensionality_raises_on_assignment() -> None:
    n = N52()
    with pytest.raises(ValidationError):
        n.thermal.conductivity = cast(PropertyFunction, 5 * U.kg)


def test_round_trip_preserves_to_elmer() -> None:
    for factory in (N52, N42, Air, FR4, Steel_1080, Steel_316):
        m = factory()
        restored = MaterialProperties.model_validate(m.model_dump())
        assert restored.to_elmer(at=AT) == m.to_elmer(at=AT)


def test_schema_has_unit_annotated_quantity_field() -> None:
    schema: dict[str, object] = MaterialProperties.model_json_schema()
    defs = cast("dict[str, object]", schema["$defs"])
    thermal = cast("dict[str, object]", defs["ThermalProperties"])
    props = cast("dict[str, object]", thermal["properties"])
    cond = cast("dict[str, object]", props["conductivity"])
    any_of = cast("list[dict[str, object]]", cond["anyOf"])
    inner = any_of[0]
    assert inner["x-unit-dimensionality"] == "[power]/[length]/[temperature]"


def test_registry_replaces_hardcoded_known_materials() -> None:
    assert set(available_materials()) == {"N52", "N42", "Air", "FR4", "Copper", "Steel1080", "Steel316"}
    assert material("N52").is_magnet
    assert isinstance(material("Steel1080"), Steel_1080)
    assert isinstance(material("Steel316"), Steel_316)
    assert len(all_materials()) == 7


def test_registry_unknown_tag_raises() -> None:
    with pytest.raises(KeyError):
        material("Unobtainium")


def test_registry_returns_fresh_instances() -> None:
    a, b = material("N52"), material("N52")
    assert a is not b


# --- registry.register_material --------------------------------------------


def test_register_material_adds_and_overrides() -> None:
    from femforge.materials.registry import register_material

    original: list[str] = available_materials()
    try:
        register_material("FR4Copy", FR4)
        assert "FR4Copy" in available_materials()
        assert material("FR4Copy").tag == FR4().tag
        # Re-registering the same tag replaces the factory.
        register_material("FR4Copy", Air)
        assert isinstance(material("FR4Copy"), Air)
    finally:
        # Restore the registry for other tests.
        from femforge.materials.registry import _FACTORIES

        for tag in list(_FACTORIES):
            if tag not in original:
                del _FACTORIES[tag]


# --- properties.py edge cases ----------------------------------------------


def test_non_magnet_magnetization_magnitude_is_none() -> None:
    # A material with no remanence returns None from magnetization_magnitude.
    air = Air()
    assert air.magnetic.magnetization_magnitude(at=AT) is None


def test_material_properties_equality_and_hash() -> None:
    # __eq__ compares (name, tag); __hash__ derives from the same pair.
    a = N52()
    b = N52()
    assert a == b
    assert hash(a) == hash(b)
    # A material with a different tag is unequal.
    air = Air()
    assert a != air
    # __eq__ against a non-MaterialProperties returns NotImplemented -> not equal.
    assert a != object()
    # Distinct tags hash differently.
    assert hash(a) != hash(air)


def test_is_conductor_classifies_by_conductivity_floor() -> None:
    from femforge.materials.air import Air
    from femforge.materials.copper import Copper
    from femforge.materials.pcb import FR4
    from femforge.materials.properties import MaterialProperties
    from femforge.physical.units import quantity

    at = {"temperature": quantity(300, "kelvin")}
    assert Copper().is_conductor(at)
    assert not FR4().is_conductor(at), "authored leakage sigma must not classify FR4 as a conductor"
    assert not Air().is_conductor(at)
    assert not MaterialProperties().is_conductor(at), "no conductivity at all -> not a conductor"
