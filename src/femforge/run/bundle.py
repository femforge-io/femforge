"""Read and write the ``runs/<run_id>/`` bundle: a content-addressed run directory holding the manifest, the typed result and the pinned geometry."""

from __future__ import annotations

import shutil
from pathlib import Path

from femforge.common.files import BundleFile, write_atomic
from femforge.run.manifest import RunManifest
from femforge.run.result import Result


def write_manifest(sim_dir: str | Path, manifest: RunManifest) -> Path:
    """Atomically write (or overwrite) ``manifest.json`` in ``sim_dir`` (created if needed) and return its path."""
    bundle: Path = Path(sim_dir)
    bundle.mkdir(parents=True, exist_ok=True)
    path: Path = bundle / BundleFile.MANIFEST
    write_atomic(path, manifest.model_dump_json(indent=2))
    return path


def write_bundle(sim_dir: str | Path, manifest: RunManifest, result: Result) -> Path:
    """Write ``manifest.json`` and ``result.json`` into ``sim_dir``, pin the source STEP as ``geometry.step`` unless the run already holds one, and return the bundle.

    Args:
        sim_dir: The run bundle directory, created when missing.
        manifest: The run's manifest.
        result: The run's typed result.
    """
    bundle: Path = Path(sim_dir)
    bundle.mkdir(parents=True, exist_ok=True)

    write_atomic(bundle / BundleFile.MANIFEST, manifest.model_dump_json(indent=2))
    write_atomic(bundle / BundleFile.RESULT, result.model_dump_json(indent=2))

    # Pin the exact geometry used, so the bundle is reproducible even if the source STEP later changes. A geometry.step
    # already there is the solve-frame STEP the run meshed (possibly a hardlink into the store), so it is never overwritten.
    pinned: Path = bundle / BundleFile.GEOMETRY_STEP
    step: Path = Path(manifest.config.mesh.step)
    if step.is_file() and not pinned.exists():
        shutil.copyfile(step, pinned)

    return bundle


def load_manifest(sim_dir: str | Path) -> RunManifest:
    """Read a bundle's ``manifest.json`` back into a typed [`RunManifest`][] — a delegate to [`RunManifest.load`][]."""
    return RunManifest.load(sim_dir)


def load_result(sim_dir: str | Path) -> Result:
    """Read a bundle's ``result.json`` back into a typed [`Result`][] — a delegate to [`Result.load`][]."""
    return Result.load(sim_dir)
