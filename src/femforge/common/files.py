"""The run-bundle filename vocabulary every writer and the retention sweep share, plus the file primitives they use."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import time
from enum import StrEnum
from pathlib import Path
from typing import Self


_ATOMIC_ATTEMPTS = 3
_ATOMIC_BACKOFF_S = 0.05
_HASH_CHUNK = 1 << 20


class BundleFile(StrEnum):
    """Every fixed filename written into a run bundle; writers and the retention sweep reference the same member."""

    MANIFEST = "manifest.json"
    RESULT = "result.json"
    GEOMETRY_STEP = "geometry.step"
    GEOMETRY_BOUNDARIES = "geometry.boundaries.json"
    GEOMETRY_MSH = "geometry.msh"
    ENTITIES = "entities.json"
    BOUNDARIES = "boundaries.yml"
    COMPONENTS = "components.yml"
    MESH_NAMES = "mesh.names"
    ENTITIES_SIF = "entities.sif"
    GROUPS = "groups.json"
    MESH_HEADER = "mesh.header"
    MESH_NODES = "mesh.nodes"
    MESH_ELEMENTS = "mesh.elements"
    MESH_BOUNDARY = "mesh.boundary"
    SIF = "case.sif"
    STARTINFO = "ELMERSOLVER_STARTINFO"
    ELMERGRID_LOG = "elmergrid.log"
    PARTITION_LOG = "elmergrid_partition.log"
    SOLVER_LOG = "elmersolver.log"
    CONVERGENCE = "convergence.jsonl"
    CONVERGENCE_TARGETS = "convergence_targets.json"
    RUN_LOG = "run.log"


MSH_SUFFIX = ".msh"
"""The gmsh mesh suffix the mesher appends to the STEP stem."""

PARTITION_PREFIX = "partitioning."
"""The prefix of the directory ElmerGrid writes a partitioned mesh into (``partitioning.<n>``)."""


def partition_dir_name(n_parts: int) -> str:
    """The directory name ElmerGrid gives an ``n_parts``-way partition."""
    return f"{PARTITION_PREFIX}{n_parts}"


class BundleGlob(StrEnum):
    """Patterns for bundle files whose names a tool derives from a stem or a rank."""

    VTU = "*.vtu"
    PVTU = "*.pvtu"
    MSH = "*" + MSH_SUFFIX
    DAT = "*.dat"
    DAT_NAMES = "*.dat.names"
    DAT_RANK = "*.dat.[0-9]*"
    PARTITIONING = PARTITION_PREFIX + "*"


class RunFile(StrEnum):
    """The retention classes of a bundle; each member carries the bundle-relative patterns it covers."""

    patterns: tuple[str, ...]

    def __new__(cls, value: str, *patterns: str) -> Self:
        member = str.__new__(cls, value)
        member._value_ = value
        member.patterns = patterns
        return member

    GEOMETRY = ("geometry", BundleFile.GEOMETRY_STEP, BundleFile.GEOMETRY_BOUNDARIES)
    GMSH_MESH = ("gmsh_mesh", BundleGlob.MSH)
    ELMER_MESH = ("elmer_mesh", BundleFile.MESH_HEADER, BundleFile.MESH_NODES, BundleFile.MESH_ELEMENTS, BundleFile.MESH_BOUNDARY)
    PARTITIONING = ("partitioning", BundleGlob.PARTITIONING, BundleFile.PARTITION_LOG)
    SIF = ("sif", BundleFile.SIF, BundleFile.STARTINFO)
    FIELDS = ("fields", BundleGlob.PVTU, BundleGlob.VTU)
    SCALARS = ("scalars", BundleGlob.DAT, BundleGlob.DAT_NAMES, BundleGlob.DAT_RANK)
    LOGS = ("logs", BundleFile.ELMERGRID_LOG, BundleFile.SOLVER_LOG, BundleFile.CONVERGENCE, BundleFile.CONVERGENCE_TARGETS, BundleFile.RUN_LOG)
    SIDECARS = ("sidecars", BundleFile.ENTITIES, BundleFile.BOUNDARIES, BundleFile.COMPONENTS, BundleFile.MESH_NAMES, BundleFile.ENTITIES_SIF, BundleFile.GROUPS)
    BUNDLE = ("bundle", BundleFile.MANIFEST, BundleFile.RESULT)
    """The bundle's identity; protected — no retention policy can name it."""
    RUN_DIR = ("run_dir",)
    """The whole directory."""


TMP_SUFFIX = ".tmp"
"""The suffix of the sibling an atomic replace stages its bytes in."""


def replace_atomic(tmp: Path, dst: Path) -> None:
    """
    Move a staged ``tmp`` file onto ``dst`` with ``os.replace``, retried briefly.

    The retry exists because viz polls these files, and a concurrently open handle on Windows fails the replace with a sharing violation.
    """
    for attempt in range(_ATOMIC_ATTEMPTS):
        try:
            os.replace(tmp, dst)
            return
        except OSError:
            if attempt == _ATOMIC_ATTEMPTS - 1:
                raise

            time.sleep(_ATOMIC_BACKOFF_S)


def write_atomic(path: Path, text: str) -> None:
    """Replace ``path`` atomically: write a ``.tmp`` sibling, then ``os.replace`` — a reader never sees a half-written file."""
    tmp: Path = path.with_name(path.name + TMP_SUFFIX)
    tmp.write_text(text, encoding="utf-8")

    replace_atomic(tmp, path)


def link_or_copy(src: Path, dst: Path) -> bool:
    """Hardlink ``src`` to ``dst``, copying instead when linking is impossible (another volume, a network share); returns True when linked."""
    try:
        os.link(src, dst)
    except OSError:
        _ = shutil.copy2(src, dst)
        return False

    return True


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's bytes, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clear_readonly(path: Path) -> None:  # pragma: no cover - reached only on Windows, where the read-only attribute blocks deletes
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)


def remove_path(path: Path) -> None:
    """Delete a file or a directory tree, clearing the Windows read-only attribute that otherwise blocks the delete."""

    def _on_error(_func: object, failed: str, _exc: BaseException) -> None:  # pragma: no cover - reached only on Windows, where the read-only attribute blocks deletes
        _clear_readonly(Path(failed))
        Path(failed).unlink() if Path(failed).is_file() else Path(failed).rmdir()

    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, onexc=_on_error)
        return

    try:
        path.unlink()
    except PermissionError:  # pragma: no cover - reached only on Windows, where the read-only attribute blocks deletes
        _clear_readonly(path)
        path.unlink()


def tree_size(path: Path) -> int:
    """Bytes under ``path``: the file's size, or the sum over every file below a directory."""
    if path.is_file():
        return path.stat().st_size

    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())
