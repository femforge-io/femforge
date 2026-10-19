"""The discoverable face of a run bundle: ``Run(sim_dir)`` opens typed results, named entities, components, and the VTU field query."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, cast

from femforge.common.files import BundleFile, RunFile
from femforge.config import RunMeta
from femforge.meshing.naming import name_fragment
from femforge.physical.units import Quantity, VectorQuantity, quantity
from femforge.run.electrical import electrode_currents as currents_from_raw, phase_currents as phases_from_currents, read_boundary_names
from femforge.run.entities import Bodies, Body, Boundaries, Entities, load_entities
from femforge.run.magnetic import read_component_names
from femforge.run.manifest import RunManifest
from femforge.run.parse import parse_result, result_vtu
from femforge.run.result import Result
from femforge.run.retention import CleanupRecord


if TYPE_CHECKING:
    from femforge.common.vector import Vec3
    from femforge.run.fields import FieldMesh


def _manifest_order(manifest_path: Path) -> tuple[float, float]:
    """Newest-first sort key: the manifest's ``created_at``, then its mtime (a purge rewrites the file without re-running anything)."""
    mtime: float = manifest_path.stat().st_mtime
    try:
        data = cast("dict[str, object]", json.loads(manifest_path.read_text(encoding="utf-8")))
        created_at: object = data.get("created_at")
        created: float = datetime.fromisoformat(created_at).timestamp() if isinstance(created_at, str) else mtime
    except Exception:  # noqa: BLE001 - an unreadable manifest sorts by mtime
        created = mtime
    return (created, mtime)


@dataclass(frozen=True)
class SifComponent:
    """One sif Component block with its lumped force and torque."""

    name: str
    """The Component name, as ``components.yml`` records it."""

    sif_index: int
    """The Component block number in the sif."""

    force: VectorQuantity | None = None
    """`F⃗` `[N]`: Lumped magnetic force on the Component; None when the solve exported none."""

    torque: VectorQuantity | None = None
    """`M⃗` `[N·m]`: Lumped magnetic torque on the Component; None when the solve exported none."""


class Run:
    """One run bundle (``runs/<run_id>/``) opened for reading."""

    def __init__(self, sim_dir: str | Path) -> None:
        """Open the run bundle at ``sim_dir``."""
        self.path: Path = Path(sim_dir)
        if not self.path.is_dir():
            raise FileNotFoundError(f"no run bundle at {self.path}")

        self._mesh: FieldMesh | None = None

    def __repr__(self) -> str:
        return f"Run({str(self.path)!r})"

    @classmethod
    def from_result(cls, result: Result) -> Run:
        """Open the bundle a Result was loaded from or written to (the driver and ``Result.load`` stamp ``run_dir``)."""
        if result.run_dir is None:
            raise ValueError("result.run_dir is None; obtain the Result from Result.load or Driver.evaluate (a run directory removed by retention has none)")

        return cls(result.run_dir)

    @classmethod
    def latest(cls, runs_dir: str | Path) -> Run:
        """The most recently created bundle under a runs directory (by manifest ``created_at``, then mtime)."""
        manifests: list[Path] = sorted(Path(runs_dir).glob(f"*/{BundleFile.MANIFEST}"), key=_manifest_order)
        if not manifests:
            raise FileNotFoundError(f"no run bundles under {runs_dir}")

        return cls(manifests[-1].parent)

    @property
    def meta(self) -> RunMeta:
        """The run's searchable metadata (description + tags), as recorded in the manifest's config."""
        return self.manifest.config.meta

    @cached_property
    def result(self) -> Result:
        """The typed result: ``result.json`` when written, else parsed fresh from the Elmer outputs in the bundle."""
        if (self.path / BundleFile.RESULT).is_file():
            return Result.load(self.path)

        result: Result = parse_result(self.path)
        result.run_dir = self.path
        try:
            result.run_id = self.manifest.run_id
        except FileNotFoundError:
            pass
        return result

    @cached_property
    def manifest(self) -> RunManifest:
        """The run's provenance manifest."""
        return RunManifest.load(self.path)

    @property
    def cleanups(self) -> list[CleanupRecord]:
        """Every retention pass recorded on the manifest (empty when the manifest is missing or unreadable)."""
        try:
            return list(self.manifest.cleanups)
        except Exception:  # noqa: BLE001 - no manifest means no recorded passes
            return []

    def purged(self, file_class: RunFile) -> CleanupRecord | None:
        """The newest cleanup pass that removed ``file_class`` (or the whole run directory), else None."""
        for record in reversed(self.cleanups):
            if record.covers(file_class):
                return record

        return None

    @cached_property
    def entities(self) -> Entities:
        """Named bodies and boundaries from the richest available source (entities.json, else enriched mesh.names)."""
        try:
            manifest: RunManifest | None = self.manifest
        except Exception:
            # A missing or schema-drifted manifest only costs enrichment, never the load.
            manifest = None
        return load_entities(self.path, manifest=manifest)

    @property
    def bodies(self) -> Bodies:
        """The run's named bodies (``mesh_id`` == the VTU's ``GeometryIds`` values)."""
        return self.entities.bodies

    @property
    def boundaries(self) -> Boundaries:
        """The run's named boundaries, with ``between``/``external``/``axis`` lookups."""
        return self.entities.boundaries

    @cached_property
    def components(self) -> list[SifComponent]:
        """Every sif Component with its lumped force/torque, joined from ``components.yml`` and the result."""
        components_file: Path = self.path / BundleFile.COMPONENTS
        if not components_file.is_file():
            return []

        forces: dict[str, VectorQuantity] = self.component_forces
        torques: dict[str, VectorQuantity] = self.component_torques
        return [
            SifComponent(
                name=name,
                sif_index=index,
                force=forces.get(name),
                torque=torques.get(name),
            )
            for index, name in read_component_names(components_file).items()
        ]

    @property
    def component_forces(self) -> dict[str, VectorQuantity]:
        """Lumped magnetic force (N) per Component name."""
        return dict(self.result.component_forces)

    @property
    def component_torques(self) -> dict[str, VectorQuantity]:
        """Lumped magnetic torque (N·m) per Component name."""
        return dict(self.result.component_torques)

    @property
    def electrode_currents(self) -> dict[str, float]:
        """Terminal current (A) per named electrode boundary, from a conduction solve's flux columns."""
        boundaries_file: Path = self.path / BundleFile.BOUNDARIES
        if not boundaries_file.is_file():
            return {}

        return currents_from_raw(self.result.raw, read_boundary_names(boundaries_file))

    @property
    def phase_currents(self) -> dict[str, float]:
        """Terminal current (A) per phase index ("0", "1", ...), from the named electrode currents."""
        return phases_from_currents(self.electrode_currents)

    @property
    def vtu_path(self) -> Path | None:
        """The run's ParaView output (the partitioned master when one exists), or None when the solve wrote none."""
        return result_vtu(self.path)

    def mesh(self) -> FieldMesh:
        """The solved VTU as a fluent field query, body names attached (lazy pyvista import; cached per Run); names the retention pass when the VTU was purged."""
        if self._mesh is None:
            if self.vtu_path is None:
                record: CleanupRecord | None = self.purged(RunFile.FIELDS)
                if record is not None:
                    raise FileNotFoundError(f"{self.path}: fields purged per retention on {record.applied_at:%Y-%m-%d} by {record.trigger}; re-solve to restore them")

            from femforge.run.fields import FieldMesh

            self._mesh = FieldMesh.load(self.path, bodies=self.bodies, boundaries=self.boundaries)
        return self._mesh

    def strain_energy(self, *bodies: Body | str) -> Quantity:
        """`U` `[J]`: The elastic strain energy stored in the named bodies, or in the whole model when none are named."""
        from femforge.run.mechanical import strain_energy

        return quantity(strain_energy(self.mesh(), *bodies), "J")

    def clapeyron_energy(self, face: str, force: VectorQuantity) -> Quantity:
        """`U` `[J]`: ½ F⃗·ū of the authored whole-face ``force`` through the named face's mean displacement, per metre of depth under a Cartesian 2D frame."""
        from femforge.run.mechanical import clapeyron_energy

        share: Vec3 = self.result.frame.authored_share(force.m_as("N"))

        return quantity(clapeyron_energy(self.mesh(), name_fragment(face), share), "J")


def find_runs(runs_dir: str | Path, *, tags: Mapping[str, str] | None = None, text: str | None = None) -> list[Run]:
    """The bundles under ``runs_dir`` whose metadata matches, newest first; this reads every manifest, so prefer [`RunIndex`][femforge.telemetry.runs.RunIndex] over thousands of bundles.

    Args:
        runs_dir: The runs directory to search; bundles with unreadable manifests are skipped.
        tags: Tag pairs a run's metadata must all carry; None matches any tags.
        text: Text to find, ignoring case, in the run name, description or notes; None matches any text.
    """
    needle: str | None = text.casefold() if text is not None else None
    matches: list[tuple[tuple[float, float], Run]] = []

    for manifest_path in Path(runs_dir).glob(f"*/{BundleFile.MANIFEST}"):
        run = Run(manifest_path.parent)
        try:
            manifest: RunManifest = run.manifest
        except Exception:  # noqa: BLE001 - a foreign or schema-drifted bundle must not break the search
            continue

        meta: RunMeta = manifest.config.meta
        if tags is not None and not meta.matches(tags):
            continue

        if needle is not None and needle not in f"{manifest.run_id} {meta.description or ''} {manifest.notes}".casefold():
            continue

        matches.append((_manifest_order(manifest_path), run))

    return [run for _, run in sorted(matches, key=lambda entry: entry[0], reverse=True)]
