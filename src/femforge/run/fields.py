"""Fluent numpy queries over a solved VTU's fields, keyed by body names, tags, and mesh ids."""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Mapping, cast

import numpy as np

from femforge.common.tags import TagKey
from femforge.elmer.variables import ElmerVariable, elemental
from femforge.run.entities import Bodies, Body, load_entities
from femforge.run.parse import result_vtu


if TYPE_CHECKING:
    import pyvista as pv


type FloatArray = np.typing.NDArray[np.float64]
type IntArray = np.typing.NDArray[np.int64]
type BoolArray = np.typing.NDArray[np.bool_]
type Bounds = tuple[float | None, float | None]

GEOMETRY_IDS_ARRAY = "GeometryIds"
"""The Int cell array ``ResultOutputSolver`` (Save Geometry Ids) writes: each cell's mesh id, bodies and boundaries alike."""


def select_array(names: Iterable[str], variable: ElmerVariable | str) -> str:
    """Resolve a VTU array name for a solver variable: exact (nodal), then the elemental ``"<name> e"``, then substring — VTU names come back lowercased."""
    available: list[str] = list(names)
    lowered: dict[str, str] = {name.lower(): name for name in available}
    want: str = str(variable).lower()

    for candidate in (want, elemental(want)):
        if candidate in lowered:
            return lowered[candidate]

    for name in available:
        if want in name.lower():
            return name
    raise KeyError(f"no array matching {str(variable)!r}; available: {sorted(available)}")


class FieldMesh:
    """A solved VTU as cell-space numpy arrays with named-body selection; open one via [`load`][] or ``Run.mesh()``."""

    def __init__(
        self,
        *,
        arrays: Mapping[str, FloatArray],
        measure: FloatArray,
        geometry_ids: IntArray | None = None,
        centers: FloatArray | None = None,
        bodies: Bodies | None = None,
        grid: pv.UnstructuredGrid | None = None,
    ) -> None:
        """Wrap cell-space arrays already extracted from a solved VTU.

        Args:
            arrays: Field array name -> per-cell values.
            measure: Per-cell measure: volume in 3D, area in 2D.
            geometry_ids: Per-cell ``GeometryIds`` mesh ids; None when the VTU carries none.
            centers: Per-cell center coordinates; None disables position filters and revolved integrals.
            bodies: The run's named bodies; None disables selection by body, material or tag.
            grid: The source pyvista grid; None disables extracting cells for plotting.
        """
        self.arrays: dict[str, FloatArray] = dict(arrays)
        self.measure: FloatArray = measure
        self.geometry_ids: IntArray | None = geometry_ids
        self.centers: FloatArray | None = centers
        self.bodies: Bodies | None = bodies
        self._grid: pv.UnstructuredGrid | None = grid

    @classmethod
    def load(cls, sim_dir: str | Path, bodies: Bodies | None = None) -> FieldMesh:  # pragma: no cover - needs pyvista (viz extra); proven by the gated tests
        """Read the bundle's newest VTU (via ``result_vtu``), attaching body names from the bundle's sidecars when not given."""
        import pyvista as pv

        vtu: Path | None = result_vtu(sim_dir)
        if vtu is None:
            raise FileNotFoundError(f"no .vtu/.pvtu output in {sim_dir}")
        if bodies is None:
            bodies = load_entities(sim_dir).bodies

        grid = cast("pv.UnstructuredGrid", pv.read(str(vtu)))
        return cls.from_grid(grid, bodies=bodies)

    @classmethod
    def from_grid(cls, grid: pv.UnstructuredGrid, bodies: Bodies | None = None) -> FieldMesh:  # pragma: no cover - needs pyvista (viz extra); proven by the gated tests
        """Extract every field array to cell space (nodal ones projected once), the cell measure, GeometryIds, and cell centers."""
        arrays: dict[str, FloatArray] = {}
        for name in grid.cell_data.keys():
            if name != GEOMETRY_IDS_ARRAY:
                arrays[name] = np.asarray(grid.cell_data[name], dtype=np.float64)

        converted: pv.DataSet | None = None
        for name in grid.point_data.keys():
            if name in arrays:
                continue
            if converted is None:
                converted = cast("pv.DataSet", grid.point_data_to_cell_data())
            arrays[name] = np.asarray(converted.cell_data[name], dtype=np.float64)

        # The cell measure is volume in a 3D model; a 2D (axisymmetric) model's body cells all have zero volume, so area steps in.
        sized: pv.DataSet = cast("pv.DataSet", grid.compute_cell_sizes(length=False, area=True, volume=True))
        volume: FloatArray = np.abs(np.asarray(sized.cell_data["Volume"], dtype=np.float64))
        measure: FloatArray = volume if bool(np.any(volume > 0.0)) else np.abs(np.asarray(sized.cell_data["Area"], dtype=np.float64))

        geometry_ids: IntArray | None = None
        if GEOMETRY_IDS_ARRAY in grid.cell_data:
            geometry_ids = np.asarray(grid.cell_data[GEOMETRY_IDS_ARRAY], dtype=np.int64)

        centers: FloatArray = np.asarray(cast("pv.DataSet", grid.cell_centers()).points, dtype=np.float64)
        return cls(arrays=arrays, measure=measure, geometry_ids=geometry_ids, centers=centers, bodies=bodies, grid=grid)

    @property
    def array_names(self) -> list[str]:
        """Every available field array name."""
        return list(self.arrays)

    def get(self, variable: ElmerVariable | str) -> FieldQuery:
        """Start a query over one field array; all cells are selected until filtered (boundary overlay cells included)."""
        name: str = select_array(self.arrays, variable)
        return FieldQuery(self, name, self.arrays[name], np.ones(len(self.measure), dtype=np.bool_))

    def extract_cells(self, mask: BoolArray) -> pv.UnstructuredGrid:  # pragma: no cover - needs pyvista (viz extra); proven by the gated tests
        """The masked cells as a pyvista subgrid (for plotting); requires construction from a live grid."""
        if self._grid is None:
            raise RuntimeError("no source grid attached; load via FieldMesh.load / FieldMesh.from_grid")
        return cast("pv.UnstructuredGrid", self._grid.extract_cells(np.flatnonzero(mask)))  # pyright: ignore[reportUnknownMemberType]  # pyvista's extract_cells index parameter is partially untyped


class FieldQuery:
    """One field array under an immutable cell mask: filters return refined copies, terminals return numpy values."""

    def __init__(self, mesh: FieldMesh, name: str, values: FloatArray, mask: BoolArray) -> None:
        """Select the cells ``mask`` flags from one field array; [`FieldMesh.get`][femforge.run.fields.FieldMesh.get] starts a query.

        Args:
            mesh: The field mesh the array belongs to.
            name: The VTU array name.
            values: The array's per-cell values over the full grid.
            mask: One flag per cell of the full grid; True keeps the cell.
        """
        self._mesh = mesh
        self.name = name
        self._values = values
        self._mask = mask

    def __len__(self) -> int:
        return int(np.count_nonzero(self._mask))

    def _refine(self, mask: BoolArray) -> FieldQuery:
        return FieldQuery(self._mesh, self.name, self._values, self._mask & mask)

    def _catalog(self) -> Bodies:
        bodies: Bodies | None = self._mesh.bodies
        if bodies is None or len(bodies) == 0:
            raise RuntimeError("no body names attached; open via Run(...).mesh() or pass bodies= to FieldMesh.load")
        return bodies

    def _geometry_ids(self) -> IntArray:
        geometry_ids: IntArray | None = self._mesh.geometry_ids
        if geometry_ids is None:
            raise RuntimeError(f"the VTU carries no {GEOMETRY_IDS_ARRAY} cell array (ResultOutputSolver.save_geometry_ids was off)")
        return geometry_ids

    def _body_cells(self, bodies: list[Body]) -> FieldQuery:
        ids: list[int] = [body.mesh_id for body in bodies]
        return self._refine(np.isin(self._geometry_ids(), ids) & (self._mesh.measure > 0.0))

    # --- filters ------------------------------------------------------------

    def where_body(self, *bodies: Body | str) -> FieldQuery:
        """Keep the named bodies' own cells (boundary overlay cells excluded)."""
        resolved: list[Body] = [body if isinstance(body, Body) else self._catalog()[body] for body in bodies]
        return self._body_cells(resolved)

    def where_material(self, tag: str) -> FieldQuery:
        """Keep the cells of every body with this material tag (case-insensitive)."""
        matched: list[Body] = self._catalog().with_material(tag)
        if not matched:
            raise KeyError(f"no bodies with material {tag!r}; bodies: {sorted(body.name for body in self._catalog())}")
        return self._body_cells(matched)

    def where_tag(self, key: TagKey | str, value: str) -> FieldQuery:
        """Keep the cells of every body tagged ``key=value``."""
        matched: list[Body] = self._catalog().with_tag(key, value)
        if not matched:
            raise KeyError(f"no bodies tagged {key}={value}; bodies: {sorted(body.name for body in self._catalog())}")
        return self._body_cells(matched)

    def where_ids(self, ids: Iterable[int]) -> FieldQuery:
        """Keep cells whose ``GeometryIds`` value is one of ``ids`` — raw mesh ids, so boundary cells are reachable too."""
        return self._refine(np.isin(self._geometry_ids(), list(ids)))

    def where_mask(self, mask: BoolArray) -> FieldQuery:
        """AND an arbitrary cell mask into the selection (one flag per cell of the full grid)."""
        incoming: BoolArray = np.asarray(mask, dtype=np.bool_)
        if incoming.shape != self._mask.shape:
            raise ValueError(f"mask has shape {incoming.shape}; expected {self._mask.shape} (one flag per cell of the full grid)")
        return self._refine(incoming)

    def where_pos(self, x: Bounds | None = None, y: Bounds | None = None, z: Bounds | None = None) -> FieldQuery:
        """Keep cells whose center lies inside the inclusive per-axis ``(min, max)`` bounds.

        Args:
            x: The ``(min, max)`` bounds on x; None leaves x unbounded, and a None bound leaves its side open.
            y: The bounds on y, as for ``x``.
            z: The bounds on z, as for ``x``.
        """
        centers: FloatArray | None = self._mesh.centers
        if centers is None:
            raise RuntimeError("no cell centers available; load via FieldMesh.load / FieldMesh.from_grid")

        mask: BoolArray = np.ones(len(centers), dtype=np.bool_)
        for axis, bounds in enumerate((x, y, z)):
            if bounds is None:
                continue
            low, high = bounds
            if low is not None:
                mask &= centers[:, axis] >= low
            if high is not None:
                mask &= centers[:, axis] <= high
        return self._refine(mask)

    def magnitude(self) -> FieldQuery:
        """A scalar query of per-cell |value|: the row norm of a vector field, the absolute value of a scalar one."""
        magnitudes: FloatArray = np.linalg.norm(self._values, axis=1) if self._values.ndim == 2 else np.abs(self._values)
        return FieldQuery(self._mesh, self.name, magnitudes, self._mask)

    # --- terminals ----------------------------------------------------------

    @property
    def values(self) -> FloatArray:
        """The selected cells' values, in cell order."""
        return self._values[self._mask]

    @property
    def measures(self) -> FloatArray:
        """The selected cells' measure (volume in 3D, area in 2D); boundary overlay cells measure 0."""
        return self._mesh.measure[self._mask]

    @property
    def cell_indices(self) -> IntArray:
        """The selected cells' indices into the full grid."""
        return np.flatnonzero(self._mask)

    def _require_values(self, operation: str) -> FloatArray:
        values: FloatArray = self.values
        if len(values) == 0:
            raise ValueError(f"{operation}() on a query of {self.name!r} that matched no cells")
        return values

    def sum(self) -> float | FloatArray:
        """Plain sum of the selected values: a float for scalars, a per-component vector otherwise."""
        total = self.values.sum(axis=0)
        return float(total) if np.ndim(total) == 0 else cast("FloatArray", total)

    def max(self) -> float | FloatArray:
        """Component-wise maximum over the selection; raises when nothing matched."""
        largest = self._require_values("max").max(axis=0)
        return float(largest) if np.ndim(largest) == 0 else cast("FloatArray", largest)

    def min(self) -> float | FloatArray:
        """Component-wise minimum over the selection; raises when nothing matched."""
        smallest = self._require_values("min").min(axis=0)
        return float(smallest) if np.ndim(smallest) == 0 else cast("FloatArray", smallest)

    def mean(self) -> float | FloatArray:
        """Measure-weighted mean over the selection (so cell size doesn't bias it; boundary overlay cells weigh 0)."""
        values: FloatArray = self._require_values("mean")
        weights: FloatArray = self.measures
        total_weight: float = float(weights.sum())
        if total_weight <= 0.0:
            raise ValueError(f"mean() of {self.name!r} over cells with zero total measure")
        if values.ndim == 2:
            return cast("FloatArray", (values * weights[:, None]).sum(axis=0) / total_weight)
        return float((values * weights).sum() / total_weight)

    def integrate(self) -> float | FloatArray:
        """∫ value · dV over the selection (in a 2D model: the plane-slice ∫ value · dA — see [`integrate_revolved`][] for the axisymmetric volume integral)."""
        values: FloatArray = self.values
        weights: FloatArray = self.measures
        if values.ndim == 2:
            return cast("FloatArray", (values * weights[:, None]).sum(axis=0))
        return float((values * weights).sum())

    def integrate_revolved(self) -> float | FloatArray:
        """∫ value · 2π·r dA over a 2D axisymmetric selection — the full-revolution volume integral, with r the radial (x) cell-center coordinate."""
        centers: FloatArray | None = self._mesh.centers
        if centers is None:
            raise RuntimeError("no cell centers available; load via FieldMesh.load / FieldMesh.from_grid")

        values: FloatArray = self.values
        weights: FloatArray = self.measures * (math.tau * centers[self._mask, 0])
        if values.ndim == 2:
            return cast("FloatArray", (values * weights[:, None]).sum(axis=0))
        return float((values * weights).sum())

    def total_measure(self) -> float:
        """The selection's total volume (3D) or area (2D)."""
        return float(self.measures.sum())

    def cells(self) -> pv.UnstructuredGrid:
        """The selected cells as a pyvista subgrid, for plotting."""
        return self._mesh.extract_cells(self._mask)
