"""FieldQuery mask algebra and terminals over prebuilt arrays, plus pyvista-gated FieldMesh extraction on tiny synthetic grids."""

from pathlib import Path

import numpy as np
import pytest

from femforge.common.tags import TagKey
from femforge.elmer.variables import ElmerVariable
from femforge.run.entities import Bodies, Body, Boundaries, Boundary, BoundaryKind
from femforge.run.fields import FieldMesh, select_array


def test_select_array_prefers_exact_then_elemental_then_substring() -> None:
    names = ["temperature", "temperature e", "magnetic flux density e", "GeometryIds"]
    assert select_array(names, ElmerVariable.TEMPERATURE) == "temperature"
    assert select_array(names, ElmerVariable.MAGNETIC_FLUX_DENSITY) == "magnetic flux density e"
    assert select_array(names, "flux density") == "magnetic flux density e"
    with pytest.raises(KeyError, match="available"):
        select_array(names, ElmerVariable.JOULE_HEATING)


def _mesh(**overrides: object) -> FieldMesh:
    # Six cells: four body cells (mesh ids 1,1,2,2) and two zero-measure boundary overlay cells (ids 3,4).
    kwargs: dict[str, object] = dict(
        arrays={
            "magnetic flux density e": np.array([[1.0, 0, 0], [0, 2.0, 0], [0, 0, 2.0], [1.0, 1.0, 0], [9.0, 9, 9], [5.0, 0, 0]], dtype=np.float64),
            "joule heating": np.array([1.0, 2.0, 3.0, 4.0, 100.0, 50.0]),
        },
        measure=np.array([1.0, 2.0, 3.0, 4.0, 0.0, 0.0]),
        geometry_ids=np.array([1, 1, 2, 2, 3, 4], dtype=np.int64),
        centers=np.array([[0.0, 0, 0], [1.0, 0, 0], [2.0, 0, 0], [3.0, 0, 0], [0.5, 0, 0], [2.5, 0, 0]], dtype=np.float64),
        bodies=Bodies(
            [
                Body(name="AIR", mesh_id=1, material="Air"),
                Body(name="N42_MAGNETIZATIONN", mesh_id=2, material="N42", tags={"Magnetization": "N"}),
            ]
        ),
    )
    kwargs.update(overrides)
    return FieldMesh(**kwargs)  # pyright: ignore[reportArgumentType]  # the overrides dict widens the kwarg types


def test_get_starts_with_every_cell_selected() -> None:
    query = _mesh().get(ElmerVariable.JOULE_HEATING)
    assert query.name == "joule heating"
    assert len(query) == 6
    assert query.values.shape == (6,)
    assert query.cell_indices.tolist() == [0, 1, 2, 3, 4, 5]
    with pytest.raises(KeyError, match="available"):
        _mesh().get("potential")


def test_where_body_keeps_only_the_bodies_own_cells() -> None:
    mesh = _mesh()
    query = mesh.get("joule heating").where_body("AIR")
    assert query.values.tolist() == [1.0, 2.0]

    assert mesh.bodies is not None
    by_object = mesh.get("joule heating").where_body(mesh.bodies["N42_MAGNETIZATIONN"])
    assert by_object.values.tolist() == [3.0, 4.0]

    with pytest.raises(KeyError, match="AIR"):
        mesh.get("joule heating").where_body("COPPER")


def test_where_material_and_tag_select_matching_bodies() -> None:
    mesh = _mesh()
    assert mesh.get("joule heating").where_material("n42").values.tolist() == [3.0, 4.0]
    assert mesh.get("joule heating").where_tag(TagKey.MAGNETIZATION, "N").values.tolist() == [3.0, 4.0]
    with pytest.raises(KeyError, match="no bodies with material"):
        mesh.get("joule heating").where_material("Steel")
    with pytest.raises(KeyError, match="no bodies tagged"):
        mesh.get("joule heating").where_tag("Magnetization", "S")


def test_where_ids_reaches_boundary_overlay_cells() -> None:
    query = _mesh().get("joule heating").where_ids([3, 4])
    assert query.values.tolist() == [100.0, 50.0]
    assert query.measures.tolist() == [0.0, 0.0]


def test_where_mask_composes_and_checks_shape() -> None:
    mesh = _mesh()
    picked = mesh.get("joule heating").where_mask(np.array([True, False, True, False, False, False]))
    assert picked.values.tolist() == [1.0, 3.0]
    with pytest.raises(ValueError, match="one flag per cell"):
        mesh.get("joule heating").where_mask(np.array([True, False]))


def test_where_pos_bounds_cells_by_center() -> None:
    mesh = _mesh()
    assert mesh.get("joule heating").where_pos(x=(1.5, None)).values.tolist() == [3.0, 4.0, 50.0]
    assert mesh.get("joule heating").where_material("n42").where_pos(x=(None, 2.0)).values.tolist() == [3.0]
    assert mesh.get("joule heating").where_pos(y=(-0.5, 0.5), z=(-0.5, 0.5)).values.tolist() == [1.0, 2.0, 3.0, 4.0, 100.0, 50.0]


def test_magnitude_turns_vectors_into_row_norms_and_scalars_into_abs() -> None:
    mesh = _mesh()
    flux = mesh.get(ElmerVariable.MAGNETIC_FLUX_DENSITY).where_body("AIR").magnitude()
    assert flux.values.tolist() == pytest.approx([1.0, 2.0])
    assert flux.max() == pytest.approx(2.0)

    negated = FieldMesh(arrays={"s": np.array([-3.0, 1.0])}, measure=np.array([1.0, 1.0]))
    assert negated.get("s").magnitude().values.tolist() == [3.0, 1.0]


def test_sum_max_min_shapes_and_empty_errors() -> None:
    mesh = _mesh()
    assert mesh.get("joule heating").sum() == pytest.approx(160.0)

    vector_sum = mesh.get(ElmerVariable.MAGNETIC_FLUX_DENSITY).where_body("AIR").sum()
    assert isinstance(vector_sum, np.ndarray)
    assert vector_sum.tolist() == pytest.approx([1.0, 2.0, 0.0])

    assert mesh.get("joule heating").where_body("AIR").min() == pytest.approx(1.0)
    empty = mesh.get("joule heating").where_ids([99])
    assert len(empty) == 0
    assert empty.sum() == 0.0
    with pytest.raises(ValueError, match="matched no cells"):
        empty.max()
    with pytest.raises(ValueError, match="matched no cells"):
        empty.min()
    with pytest.raises(ValueError, match="matched no cells"):
        empty.mean()


def test_mean_is_measure_weighted() -> None:
    mesh = _mesh()
    assert mesh.get("joule heating").where_body("AIR").mean() == pytest.approx((1.0 * 1.0 + 2.0 * 2.0) / 3.0)

    vector_mean = mesh.get(ElmerVariable.MAGNETIC_FLUX_DENSITY).where_body("AIR").mean()
    assert isinstance(vector_mean, np.ndarray)
    assert vector_mean.tolist() == pytest.approx([1.0 / 3.0, 4.0 / 3.0, 0.0])

    with pytest.raises(ValueError, match="zero total measure"):
        mesh.get("joule heating").where_ids([3]).mean()


def test_integrate_weights_by_measure() -> None:
    mesh = _mesh()
    assert mesh.get("joule heating").where_material("N42").integrate() == pytest.approx(3.0 * 3.0 + 4.0 * 4.0)
    assert mesh.get("joule heating").where_material("N42").total_measure() == pytest.approx(7.0)

    vector_integral = mesh.get(ElmerVariable.MAGNETIC_FLUX_DENSITY).where_body("AIR").integrate()
    assert isinstance(vector_integral, np.ndarray)
    assert vector_integral.tolist() == pytest.approx([1.0, 4.0, 0.0])


def test_integrate_revolved_weights_by_tau_times_radius() -> None:
    import math

    mesh = _mesh()
    # AIR cells: values [1, 2], area [1, 2], radial centers x = [0, 1] -> sum(v * area * tau * r) = 2 * 2 * tau.
    assert mesh.get("joule heating").where_body("AIR").integrate_revolved() == pytest.approx(4.0 * math.tau)

    vector_integral = mesh.get(ElmerVariable.MAGNETIC_FLUX_DENSITY).where_body("AIR").integrate_revolved()
    assert isinstance(vector_integral, np.ndarray)
    assert vector_integral.tolist() == pytest.approx([0.0, 4.0 * math.tau, 0.0])

    with pytest.raises(RuntimeError, match="no cell centers"):
        _mesh(centers=None).get("joule heating").integrate_revolved()


def test_missing_capabilities_raise_pointed_errors() -> None:
    without_bodies = _mesh(bodies=None)
    with pytest.raises(RuntimeError, match="no body names"):
        without_bodies.get("joule heating").where_body("AIR")
    with pytest.raises(RuntimeError, match="no body names"):
        _mesh(bodies=Bodies(())).get("joule heating").where_material("Air")

    without_ids = _mesh(geometry_ids=None)
    with pytest.raises(RuntimeError, match="GeometryIds"):
        without_ids.get("joule heating").where_ids([1])

    without_centers = _mesh(centers=None)
    with pytest.raises(RuntimeError, match="no cell centers"):
        without_centers.get("joule heating").where_pos(x=(0.0, 1.0))

    with pytest.raises(RuntimeError, match="no source grid"):
        _mesh().get("joule heating").cells()

    assert _mesh().array_names == ["magnetic flux density e", "joule heating"]


# --- pyvista-gated: real grid extraction ------------------------------------


def _synthetic_grid():  # pragma: no cover - helper for the pyvista-gated tests below
    import pyvista as pv

    cube1 = [(0.0, 0, 0), (1.0, 0, 0), (1.0, 1, 0), (0.0, 1, 0), (0.0, 0, 1), (1.0, 0, 1), (1.0, 1, 1), (0.0, 1, 1)]
    cube2 = [(1.0, 0, 0), (2.0, 0, 0), (2.0, 1, 0), (1.0, 1, 0), (1.0, 0, 1), (2.0, 0, 1), (2.0, 1, 1), (1.0, 1, 1)]
    points = np.array(cube1 + cube2, dtype=np.float64)

    cells = np.array([8, *range(0, 8), 8, *range(8, 16), 4, 0, 3, 7, 4])
    celltypes = np.array([pv.CellType.HEXAHEDRON, pv.CellType.HEXAHEDRON, pv.CellType.QUAD])
    grid = pv.UnstructuredGrid(cells, celltypes, points)
    grid.cell_data["GeometryIds"] = np.array([1, 2, 3], dtype=np.int32)
    grid.cell_data["joule heating"] = np.array([2.0, 3.0, 7.0])
    grid.point_data["potential"] = points[:, 0]
    return grid


def test_from_grid_extracts_measure_ids_and_projects_nodal_arrays() -> None:
    pytest.importorskip("pyvista")
    bodies = Bodies([Body(name="LEFT", mesh_id=1), Body(name="RIGHT", mesh_id=2)])
    mesh = FieldMesh.from_grid(_synthetic_grid(), bodies=bodies)

    assert mesh.geometry_ids is not None and np.array_equal(mesh.geometry_ids, [1, 2, 3])
    assert mesh.measure.tolist() == pytest.approx([1.0, 1.0, 0.0])
    assert mesh.centers is not None and mesh.centers[1:2].flatten().tolist() == pytest.approx([1.5, 0.5, 0.5])

    # The nodal potential (== x) projects to per-cell averages; the boundary quad sits at x=0.
    potential = mesh.get("potential")
    assert potential.values.tolist() == pytest.approx([0.5, 1.5, 0.0])

    assert mesh.get("joule heating").where_body("LEFT").integrate() == pytest.approx(2.0)
    assert mesh.get("joule heating").where_ids([3]).values.tolist() == [7.0]

    subgrid = mesh.get("joule heating").where_body("RIGHT").cells()
    assert subgrid.n_cells == 1


def test_load_reads_the_vtu_from_a_bundle_dir(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")
    _synthetic_grid().save(str(tmp_path / "case_t0001.vtu"))  # pyright: ignore[reportUnknownMemberType]  # pyvista's save signature is partially untyped

    mesh = FieldMesh.load(tmp_path)
    assert mesh.bodies is not None and len(mesh.bodies) == 0
    assert mesh.get("joule heating").where_ids([1, 2]).sum() == pytest.approx(5.0)

    with pytest.raises(FileNotFoundError, match="no .vtu"):
        FieldMesh.load(tmp_path / "empty")


def test_2d_grid_falls_back_to_area_measure() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    points = np.array([(0.0, 0, 0), (2.0, 0, 0), (2.0, 1, 0), (0.0, 1, 0)], dtype=np.float64)
    grid = pv.UnstructuredGrid(np.array([4, 0, 1, 2, 3]), np.array([pv.CellType.QUAD]), points)
    grid.cell_data["joule heating"] = np.array([5.0])

    mesh = FieldMesh.from_grid(grid)
    assert mesh.measure.tolist() == pytest.approx([2.0])
    assert mesh.get("joule heating").integrate() == pytest.approx(10.0)


# --- boundary selection and surface means -------------------------------------


def _faces() -> Boundaries:
    return Boundaries(
        [
            Boundary(name="AIR__EXT", mesh_id=3, bodies=("AIR",), kind=BoundaryKind.EXTERNAL, sif_index=1),
            Boundary(name="AIR__N42_MAGNETIZATIONN", mesh_id=4, bodies=("AIR", "N42_MAGNETIZATIONN"), kind=BoundaryKind.INTERFACE, sif_index=2),
            Boundary(name="N42_MAGNETIZATIONN__EXT", mesh_id=5, bodies=("N42_MAGNETIZATIONN",), kind=BoundaryKind.EXTERNAL),
        ]
    )


def _faced(**overrides: object) -> FieldMesh:
    return _mesh(boundaries=_faces(), geometry_ids=np.array([1, 1, 2, 2, 101, 102], dtype=np.int64), **overrides)


def test_where_boundary_keeps_the_zero_volume_overlay_cells() -> None:
    mesh = _faced()

    assert mesh.get("joule heating").where_boundary("AIR__EXT").values.tolist() == [100.0]
    assert mesh.get("joule heating").where_boundary(_faces()["AIR__N42_MAGNETIZATIONN"], "AIR__EXT").values.tolist() == [100.0, 50.0]


def test_surface_mean_weighs_by_area() -> None:
    mesh = _faced(area=np.array([0.0, 0.0, 0.0, 0.0, 1.0, 3.0]))
    both = mesh.get("joule heating").where_boundary("AIR__EXT", "AIR__N42_MAGNETIZATIONN")

    assert both.surface_mean() == pytest.approx((100.0 * 1.0 + 50.0 * 3.0) / 4.0)
    flux = mesh.get("magnetic flux density e").where_boundary("AIR__EXT", "AIR__N42_MAGNETIZATIONN")
    assert np.asarray(flux.surface_mean()).tolist() == pytest.approx([6.0, 2.25, 2.25])

    with pytest.raises(ValueError, match="zero total area"):
        mesh.get("joule heating").where_body("AIR").surface_mean()


def test_boundary_queries_name_what_the_mesh_lacks() -> None:
    with pytest.raises(RuntimeError, match="no boundary names"):
        _mesh().get("joule heating").where_boundary("AIR__EXT")

    with pytest.raises(KeyError, match="AIR__EXT"):
        _faced().get("joule heating").where_boundary("TIP")

    with pytest.raises(ValueError, match="no Boundary Condition block"):
        _faced().get("joule heating").where_boundary("N42_MAGNETIZATIONN__EXT")

    with pytest.raises(RuntimeError, match="no cell areas"):
        _faced().get("joule heating").where_boundary("AIR__EXT").surface_mean()


def test_the_boundary_id_offset_grows_past_the_largest_body_id() -> None:
    mesh = _mesh(boundaries=_faces(), geometry_ids=np.array([100, 100, 2, 2, 1001, 101], dtype=np.int64))

    assert mesh.get("joule heating").where_boundary("AIR__EXT").values.tolist() == [100.0]


def test_from_grid_carries_face_areas_boundaries_and_the_strain_energy_density() -> None:
    pytest.importorskip("pyvista")
    grid = _synthetic_grid()
    grid.point_data["stress"] = np.tile([2.0, 0.0, 0.0, 0.0, 0.0, 0.0], (grid.n_points, 1))
    grid.point_data["strain"] = np.tile([0.5, 0.0, 0.0, 0.0, 0.0, 0.0], (grid.n_points, 1))
    grid.cell_data["GeometryIds"] = np.array([1, 2, 101], dtype=np.int32)
    faces = Boundaries([Boundary(name="LEFT__EXT", mesh_id=3, bodies=("LEFT",), kind=BoundaryKind.EXTERNAL, sif_index=1)])

    mesh = FieldMesh.from_grid(grid, boundaries=faces)

    assert mesh.area is not None and mesh.area.tolist() == pytest.approx([0.0, 0.0, 1.0])
    assert mesh.get("potential").where_boundary("LEFT__EXT").surface_mean() == pytest.approx(0.0)
    assert mesh.get(ElmerVariable.STRAIN_ENERGY_DENSITY).values.tolist() == pytest.approx([0.5, 0.5, 0.5])


def test_2d_grid_measures_its_boundary_edges_by_length() -> None:
    pytest.importorskip("pyvista")
    import pyvista as pv

    points = np.array([(0.0, 0, 0), (2.0, 0, 0), (2.0, 1, 0), (0.0, 1, 0)], dtype=np.float64)
    grid = pv.UnstructuredGrid(np.array([4, 0, 1, 2, 3, 2, 1, 2]), np.array([pv.CellType.QUAD, pv.CellType.LINE]), points)

    mesh = FieldMesh.from_grid(grid)

    assert mesh.area is not None and mesh.area.tolist() == pytest.approx([0.0, 1.0])
