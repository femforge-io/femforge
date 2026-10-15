"""GmshOptions: a typed wrapper over gmsh.option.setNumber/setString.

apply() takes injectable setters so the synthesized calls are asserted without a live gmsh binary.
"""

from __future__ import annotations

import pytest

from femforge.meshing.gmsh_options import GmshOptions, MeshAlgorithm2D, MeshAlgorithm3D


def _capture(opts: GmshOptions) -> dict[str, float | str]:
    """Record the (name, value) calls apply() makes via injected setters (numbers via setNumber, strings via setString)."""
    calls: dict[str, float | str] = {}

    def record_number(name: str, value: float) -> None:
        calls[name] = value

    def record_string(name: str, value: str) -> None:
        calls[name] = value

    opts.apply(set_number=record_number, set_string=record_string)
    return calls


def test_defaults_reproduce_historical_behavior() -> None:
    # The two options the mesher always sets: labels on, terminal quiet.
    calls: dict[str, float | str] = _capture(GmshOptions())
    assert calls["Geometry.OCCImportLabels"] == 1.0
    assert calls["General.Terminal"] == 0.0


def test_bool_fields_emit_one_or_zero() -> None:
    calls: dict[str, float | str] = _capture(GmshOptions(terminal_output=True, import_labels=False, optimize=False))
    assert calls["General.Terminal"] == 1.0
    assert calls["Geometry.OCCImportLabels"] == 0.0
    assert calls["Mesh.Optimize"] == 0.0


def test_algorithm_enum_emits_its_integer_value() -> None:
    assert _capture(GmshOptions())["Mesh.Algorithm3D"] == 10.0  # HXT default (multi-threaded)
    assert _capture(GmshOptions(algorithm_3d=MeshAlgorithm3D.DELAUNAY))["Mesh.Algorithm3D"] == 1.0


def test_2d_algorithm_emits_its_integer_value() -> None:
    # Frontal-Delaunay by default: all-triangle, which HXT requires of the surface mesh and a 2D model uses for its bodies.
    assert _capture(GmshOptions())["Mesh.Algorithm"] == 6.0
    assert _capture(GmshOptions(algorithm_2d=MeshAlgorithm2D.DELAUNAY))["Mesh.Algorithm"] == 5.0


def test_deterministic_pins_meshing_to_one_thread() -> None:
    # deterministic=True must override any configured thread count — HXT's multithreading is the re-mesh nondeterminism source.
    from femforge.meshing.config import MeshConfig

    pinned = MeshConfig(deterministic=True, gmsh_options=GmshOptions(num_threads=16)).resolved_gmsh_options()
    assert _capture(pinned)["General.NumThreads"] == 1.0

    # The default leaves the configured options untouched (same object, no copy).
    config = MeshConfig(gmsh_options=GmshOptions(num_threads=16))
    assert config.resolved_gmsh_options() is config.gmsh_options


def test_num_threads_defaults_to_all_cores() -> None:
    # General.NumThreads defaults to the host core count (>=1), not gmsh's 1.
    from femforge.meshing.gmsh_options import _default_num_threads

    calls: dict[str, float | str] = _capture(GmshOptions())
    assert calls["General.NumThreads"] == float(_default_num_threads())
    # An explicit None inherits gmsh's own default (keyword omitted).
    assert "General.NumThreads" not in _capture(GmshOptions(num_threads=None))


def test_recombine_all_is_pinned_off() -> None:
    # Recombination must be explicitly disabled: the mesh must stay tetrahedral for Elmer's WhitneyAVSolver,
    # and recombining across conformal shared faces fails to mesh.
    assert _capture(GmshOptions())["Mesh.RecombineAll"] == 0.0


def test_none_fields_are_skipped() -> None:
    # The optional knobs default to None -> gmsh keeps its own default, so nothing is emitted for them.
    calls: dict[str, float | str] = _capture(GmshOptions())
    assert "Mesh.MeshSizeMin" not in calls
    assert "Mesh.MeshSizeMax" not in calls
    assert "Mesh.Smoothing" not in calls


def test_optional_numeric_fields_emit_when_set() -> None:
    calls: dict[str, float | str] = _capture(GmshOptions(mesh_size_min=0.1, mesh_size_max=2.5, smoothing=3, num_threads=8))
    assert calls["Mesh.MeshSizeMin"] == pytest.approx(0.1)
    assert calls["Mesh.MeshSizeMax"] == pytest.approx(2.5)
    assert calls["Mesh.Smoothing"] == 3.0
    assert calls["General.NumThreads"] == 8.0


def test_target_units_emits_via_set_string() -> None:
    # The OCC target unit is a string option (setString), defaulting to "M" so the whole pipeline is SI metres.
    assert _capture(GmshOptions())["Geometry.OCCTargetUnit"] == "M"
    # None inherits gmsh's default -> the keyword is omitted entirely.
    assert "Geometry.OCCTargetUnit" not in _capture(GmshOptions(target_units=None))


def test_apply_returns_none_and_stores_no_synthesized_dict() -> None:
    # The synthesized calls are ephemeral: apply() returns nothing and the model holds no emitted-dict attribute.
    opts: GmshOptions = GmshOptions()
    assert opts.apply(set_number=lambda _name, _value: None, set_string=lambda _name, _value: None) is None
    assert not hasattr(opts, "data")


def test_validate_assignment_rejects_wrong_type() -> None:
    from pydantic import ValidationError

    opts: GmshOptions = GmshOptions()
    with pytest.raises(ValidationError):
        opts.mesh_size_max = "huge"  # pyright: ignore[reportAttributeAccessIssue]  # exercising runtime validate_assignment


def test_round_trip_through_model_dump() -> None:
    opts: GmshOptions = GmshOptions(algorithm_3d=MeshAlgorithm3D.HXT, mesh_size_max=1.5)
    restored: GmshOptions = GmshOptions.model_validate(opts.model_dump())
    assert restored.algorithm_3d is MeshAlgorithm3D.HXT
    assert restored.mesh_size_max == pytest.approx(1.5)
