"""Content-addressing keys and the provenance manifest.

Configs reference a non-existent STEP path, so step_digest falls back to hashing the path string and the keys stay deterministic without any real geometry file.
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest

import femforge.run.manifest as manifest_module
from femforge.frames import Axisymmetric
from femforge.elmer.config import PhysicsSelection
from femforge.elmer.physics import Physics
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.materials.neodymium import N42
from femforge.materials.properties import ElectricalProperties, MaterialProperties
from femforge.materials.steel import Steel_1080
from femforge.physical.property_functions import Static
from femforge.physical.units import Quantity, U, quantity
from femforge.run.manifest import RunManifest, cache_key, git_sha, mesh_key
from femforge.common.names import bundle_name
from femforge.run.result import MeshStats
from femforge.run.stages import Stage, StageEvent

FIXED_DT = datetime(2026, 6, 14, 10, 0, 0, tzinfo=timezone.utc)


def _config(
    *,
    global_mesh_size: Quantity = 1.0 * U.m,
    materials: list[MaterialProperties] | None = None,
    tags: list[EntityTag] | None = None,
    physics: Physics = Physics.MAGNETOSTATICS,
) -> SimulationConfig:
    return SimulationConfig(
        mesh=MeshConfig(step="data/does-not-exist.step", global_mesh_size=global_mesh_size),
        domain=DomainConfig(
            materials=materials if materials is not None else [],
            tags=tags if tags is not None else [],
        ),
        physics=PhysicsSelection(selected=physics),
    )


# --- cache_key (full-run identity) -----------------------------------------


def test_cache_key_is_stable_for_identical_configs() -> None:
    assert cache_key(_config()) == cache_key(_config())


def test_cache_key_changes_when_a_result_affecting_field_changes() -> None:
    coarse = cache_key(_config(global_mesh_size=1.0 * U.m))
    fine = cache_key(_config(global_mesh_size=0.5 * U.m))
    assert coarse != fine


def test_cache_key_changes_with_physics_but_not_with_the_code_version() -> None:
    # Physics lives inside the config, so varying it varies the serialized config the key hashes; the commit is provenance only.
    base = cache_key(_config())
    assert base != cache_key(_config(physics=Physics.THERMAL))
    assert (
        RunManifest.build(
            _config(),
            status="ok",
            code_sha="sha1",
        ).cache_key
        == RunManifest.build(
            _config(),
            status="ok",
            code_sha="sha2",
        ).cache_key
    )


def test_cache_key_changes_when_a_material_changes() -> None:
    plain = cache_key(_config(materials=[MaterialProperties(name="A")]))
    other = cache_key(_config(materials=[MaterialProperties(name="B")]))
    assert plain != other


def test_cache_key_handles_nonlinear_materials() -> None:
    # A Calibration-carrying steel + an Expression-carrying magnet must serialize into the key deterministically.
    a = cache_key(_config(materials=[Steel_1080(), N42()]))
    b = cache_key(_config(materials=[Steel_1080(), N42()]))
    assert a == b


def test_cache_key_of_a_fixed_config_is_pinned() -> None:
    """A fixed config hashes to a pinned key, so a change that moves run identities fails here first."""
    config = _config(materials=[Steel_1080(), N42()])

    assert cache_key(config) == "f0616e18fb37d79bec8ac18119a481913a4337e58d32bde236e1d360db86820f"


def test_cache_key_distinguishes_permeability_mode() -> None:
    # Nonlinear (H-B curve) vs linearized Static μr are result-affecting identities.
    nonlinear = cache_key(_config(materials=[Steel_1080()]))
    linearized = cache_key(_config(materials=[Steel_1080(rel_permeability=584.0)]))
    assert nonlinear != linearized


def test_cache_key_ignores_run_metadata() -> None:
    # meta is searchable provenance: describing/tagging a run must never re-identify it.
    labeled = _config()
    labeled.meta.description = "ripple sweep, position -3pi"
    labeled.meta.tags = {"experiment": "force_ripple", "scale": "0.25"}
    assert cache_key(labeled) == cache_key(_config())


# --- mesh_key (geometry-only identity) -------------------------------------


def test_mesh_key_is_invariant_to_non_geometry_changes() -> None:
    # Changing materials or physics must NOT change the mesh identity; that invariance makes mesh reuse safe.
    a = mesh_key(_config(materials=[MaterialProperties(name="A")]))
    b = mesh_key(_config(materials=[MaterialProperties(name="B")]))
    assert a == b


def test_mesh_key_changes_with_global_mesh_size() -> None:
    assert mesh_key(_config(global_mesh_size=1.0 * U.m)) != mesh_key(_config(global_mesh_size=0.5 * U.m))


def test_mesh_key_changes_with_per_region_mesh_size() -> None:
    coarse = _config(tags=[EntityTag(selector={"Region": "gap"}, mesh_size=0.2 * U.m)])
    fine = _config(tags=[EntityTag(selector={"Region": "gap"}, mesh_size=0.05 * U.m)])
    assert mesh_key(coarse) != mesh_key(fine)


# --- manifest ---------------------------------------------------------------


def test_manifest_run_id_is_the_named_cache_key() -> None:
    config = _config()
    manifest = RunManifest.build(
        config,
        status="ok",
        code_sha="sha1",
        created_at=FIXED_DT,
    )

    key = cache_key(config)
    assert manifest.cache_key == key
    assert manifest.run_id == bundle_name(key)
    assert manifest.mesh_key == mesh_key(config)
    assert manifest.mesh_name == bundle_name(manifest.mesh_key)
    assert manifest.status == "ok"

    payload = cast("dict[str, object]", json.loads(manifest.model_dump_json()))
    keys = list(payload)
    assert keys.index("mesh_name") == keys.index("mesh_key") + 1
    payload["mesh_name"] = "stale-name-00000000"
    assert RunManifest.model_validate(payload).mesh_name == manifest.mesh_name


def test_manifest_physics_is_read_from_config() -> None:
    # The recorded provenance physics is sourced from config.physics.selected.
    manifest = RunManifest.build(
        _config(physics=Physics.THERMAL),
        status="ok",
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    assert manifest.physics is Physics.THERMAL


def test_manifest_notes_do_not_change_run_id() -> None:
    # notes is provenance-only and must not change the run's content-addressed identity.
    config = _config()
    bare = RunManifest.build(
        config,
        status="ok",
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    noted = RunManifest.build(
        config,
        status="ok",
        code_sha="sha1",
        created_at=FIXED_DT,
        notes="a trial run",
    )
    assert bare.run_id == noted.run_id
    assert noted.notes == "a trial run"


def test_manifest_round_trips_through_json() -> None:
    config = _config(materials=[MaterialProperties(name="N52")], tags=[EntityTag(selector={"Region": "gap"}, mesh_size=0.1 * U.mm)])
    manifest = RunManifest.build(
        config,
        status="ok",
        mesh_stats=MeshStats(element_count=1234, min_element_size=0.05, max_element_size=1.0),
        code_sha="sha1",
        created_at=FIXED_DT,
    )

    restored = RunManifest.model_validate_json(manifest.model_dump_json())

    assert restored.run_id == manifest.run_id
    assert restored.mesh_key == manifest.mesh_key
    assert restored.created_at == FIXED_DT
    assert restored.mesh_stats is not None and restored.mesh_stats.element_count == 1234
    assert restored.config.domain.tags[0].mesh_size == 0.1 * U.mm


def test_manifest_round_trips_nonlinear_steel() -> None:
    # The steel's Calibration (H-B curve) must survive JSON: the revived material emits the same sif keywords, table included.
    steel = Steel_1080()
    manifest = RunManifest.build(
        _config(materials=[steel, N42()]),
        status="ok",
        code_sha="sha1",
        created_at=FIXED_DT,
    )

    restored = RunManifest.model_validate_json(manifest.model_dump_json())
    at = {"temperature": quantity(300, U.K)}
    revived = restored.config.domain.materials[0]

    assert revived.to_elmer(at=at) == steel.to_elmer(at=at)
    assert restored.run_id == manifest.run_id


def test_manifest_builds_running_with_an_injected_run_id() -> None:
    # The driver precomputes the bundle name once and injects it; build must take it verbatim instead of recomputing.
    config = _config()
    manifest = RunManifest.build(
        config,
        status="running",
        run_id="feedc0de",
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    assert manifest.status == "running"
    assert manifest.run_id == "feedc0de"
    # The content hash is still recorded, independent of the injected name.
    assert manifest.cache_key == cache_key(config)


def test_manifest_injected_run_id_matches_the_computed_name() -> None:
    config = _config()
    name = bundle_name(cache_key(config))
    injected = RunManifest.build(
        config,
        status="running",
        run_id=name,
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    computed = RunManifest.build(
        config,
        status="running",
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    assert injected.run_id == computed.run_id == name


def test_manifest_load_from_dir_file_and_missing(tmp_path: Path) -> None:
    from femforge.common.files import BundleFile

    manifest = RunManifest.build(
        _config(),
        status="ok",
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    (tmp_path / BundleFile.MANIFEST).write_text(manifest.model_dump_json(), encoding="utf-8")
    assert RunManifest.load(tmp_path).run_id == manifest.run_id
    assert RunManifest.load(tmp_path / BundleFile.MANIFEST).mesh_key == manifest.mesh_key
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        RunManifest.load(tmp_path / "not-a-bundle")


def test_manifest_without_stage_history_still_validates() -> None:
    # Bundles written before stage capture lack the field; they must load with an empty history.
    import json
    from typing import cast

    dumped = cast(
        "dict[str, object]",
        json.loads(
            RunManifest.build(
                _config(),
                status="ok",
                code_sha="sha1",
                created_at=FIXED_DT,
            ).model_dump_json()
        ),
    )
    del dumped["stage_history"]
    restored = RunManifest.model_validate(dumped)
    assert restored.stage_history == []
    assert restored.current_stage is None


def test_manifest_current_stage_is_the_first_unfinished_event() -> None:
    manifest = RunManifest.build(
        _config(),
        status="running",
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    assert manifest.current_stage is None

    manifest.stage_history.append(StageEvent(stage=Stage.MESH, started_at=FIXED_DT))
    assert manifest.current_stage is Stage.MESH

    # A cleanup that ran and finished after the crash does not hide the crashed stage.
    manifest.stage_history.append(StageEvent(stage=Stage.CLEANUP, started_at=FIXED_DT, finished_at=FIXED_DT))
    assert manifest.current_stage is Stage.MESH

    manifest.stage_history[0].finished_at = FIXED_DT
    assert manifest.current_stage is None


def test_manifest_without_cleanups_still_validates_and_has_no_cached_inputs() -> None:
    import json
    from typing import cast

    dumped = cast(
        "dict[str, object]",
        json.loads(
            RunManifest.build(
                _config(),
                status="ok",
                code_sha="sha1",
                created_at=FIXED_DT,
            ).model_dump_json()
        ),
    )
    del dumped["cleanups"]
    restored = RunManifest.model_validate(dumped)
    assert restored.cleanups == []
    assert restored.cached_inputs() == frozenset()


def test_manifest_stage_history_round_trips_through_json() -> None:
    manifest = RunManifest.build(
        _config(),
        status="ok",
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    manifest.stage_history.append(StageEvent(stage=Stage.PREPARE_GEOMETRY, started_at=FIXED_DT, finished_at=FIXED_DT))
    restored = RunManifest.model_validate_json(manifest.model_dump_json())
    assert restored.stage_history == manifest.stage_history
    assert restored.stage_history[0].stage is Stage.PREPARE_GEOMETRY


# --- git_sha (provenance) ---------------------------------------------------


def test_git_sha_strips_and_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    # Canned `git rev-parse HEAD` output exercises the strip plus short/full truncation without depending on the runner's cwd or git state.
    sha40: str = "0123456789abcdef0123456789abcdef01234567"

    def _ok(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = sha40 + "\n" if "rev-parse" in args else ""
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=stdout,
            stderr="",
        )

    monkeypatch.setattr(manifest_module.subprocess, "run", _ok)
    assert git_sha() == sha40[:12]  # short form: first 12 hex chars
    assert git_sha(short=False) == sha40  # full 40-char SHA-1


def test_git_sha_marks_a_dirty_working_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    sha40: str = "0123456789abcdef0123456789abcdef01234567"

    def _dirty(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = sha40 + "\n" if "rev-parse" in args else " M src/femforge/run/driver.py\n"
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=stdout,
            stderr="",
        )

    monkeypatch.setattr(manifest_module.subprocess, "run", _dirty)
    assert git_sha() == sha40[:12] + "-dirty"


def test_git_sha_returns_none_when_subprocess_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # git unavailable (any OSError/SubprocessError) yields None, so a run outside version control still completes.
    def _boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("git not found")

    monkeypatch.setattr(manifest_module.subprocess, "run", _boom)
    assert git_sha() is None


def test_git_sha_returns_none_when_output_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # Empty stdout (no commit yet) yields None rather than an empty-string sha.
    def _empty(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git", "rev-parse", "HEAD"],
            returncode=0,
            stdout="\n",
            stderr="",
        )

    monkeypatch.setattr(manifest_module.subprocess, "run", _empty)
    assert git_sha() is None


def test_mesh_key_distinguishes_parametric_geometries() -> None:
    """Parametric runs share a placeholder mesh.step, so the geometry params must enter the mesh identity."""
    from femforge.geometry.config import GeometryArea
    from femforge.run.manifest import mesh_key

    class _StubGeometry(GeometryArea):
        width: float = 1.0

    base = SimulationConfig(geometry=_StubGeometry())
    wider = SimulationConfig(geometry=_StubGeometry(width=99.0))
    assert mesh_key(base) != mesh_key(wider)
    assert mesh_key(base) == mesh_key(base.model_copy(deep=True))


# --- resolved physics config in the identity + manifest ----------------------


def test_cache_key_includes_the_resolved_solver_stack() -> None:
    # An authored override to any solver/simulation setting is result-affecting and must change the identity.
    tweaked = _config()
    tweaked.customize_physics().simulation.steady_state_max_iterations = 9
    assert cache_key(_config()) != cache_key(tweaked)


def test_cache_key_ignores_overrides_identical_to_the_preset() -> None:
    # Materializing the preset without editing it authors the same effective stack, so the identity must not move.
    explicit = _config()
    _ = explicit.customize_physics()
    assert cache_key(_config()) == cache_key(explicit)


def test_manifest_records_the_resolved_physics_config() -> None:
    from femforge.elmer.config import MagnetoDynamicsSolver

    config = _config()
    config.customize_physics().solver(MagnetoDynamicsSolver).linear_system_max_iterations = 123

    manifest = RunManifest.build(
        config,
        status="ok",
        code_sha="sha1",
        created_at=FIXED_DT,
    )
    assert manifest.physics_config.solver(MagnetoDynamicsSolver).linear_system_max_iterations == 123

    # The concrete solver types (not the bare base) survive the JSON round-trip via the kind discriminator.
    restored = RunManifest.model_validate_json(manifest.model_dump_json())
    assert restored.physics_config.solver(MagnetoDynamicsSolver).linear_system_max_iterations == 123


def test_mesh_key_changes_with_the_frame() -> None:
    # A 2D cross-section mesh and a 3D solid mesh of the same STEP must not share a mesh identity.
    flat = _config()
    flat.frame = Axisymmetric()
    assert mesh_key(_config()) != mesh_key(flat)


def test_cache_key_reads_material_properties_in_base_units() -> None:
    def copper(conductivity: Quantity) -> MaterialProperties:
        return MaterialProperties(name="Cu", electrical=ElectricalProperties(conductivity=Static(value=conductivity)))

    in_mega: str = cache_key(_config(materials=[copper(58 * U.MS / U.m)]))

    assert in_mega == cache_key(_config(materials=[copper(5.8e7 * U.S / U.m)]))
    assert in_mega != cache_key(_config(materials=[copper(59 * U.MS / U.m)]))
