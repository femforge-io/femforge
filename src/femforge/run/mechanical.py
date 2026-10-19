"""Elasticity results Elmer does not write: the strain energy density from the stress and strain tensors, and the energies it integrates to."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, cast

import numpy as np

from femforge.elmer.variables import ElmerVariable


if TYPE_CHECKING:
    import pyvista as pv

    from femforge.common.vector import Vec3
    from femforge.run.entities import Body, Boundary
    from femforge.run.fields import FieldMesh, FloatArray


_SHEAR_WEIGHT = 2.0
"""`[-]`: The weight of each shear column in σ:ε, since Elmer writes the tensorial (not engineering) shear strain."""

_TENSOR_WIDTHS = (4, 6)
"""The Voigt widths of a stress or strain tensor: xx yy zz xy in 2D, plus yz xz in 3D."""

VOIGT_COMPONENTS = ("xx", "yy", "zz", "xy", "yz", "xz")
"""The Voigt component suffixes, in the order Elmer splits a tensor into ``stress_xx`` and the like."""


def strain_energy_density(stress: FloatArray, strain: FloatArray) -> FloatArray:
    """`W` `[J/m³]`: ½ σ:ε per row of Voigt stress and tensorial strain arrays of the same width."""
    shape: tuple[int, ...] = stress.shape
    width: int = shape[-1]

    if stress.shape != strain.shape or width not in _TENSOR_WIDTHS:
        raise ValueError(f"stress {stress.shape} and strain {strain.shape} must be equal arrays of width {' or '.join(str(w) for w in _TENSOR_WIDTHS)}")

    weights: FloatArray = np.where(np.arange(width) < 3, 1.0, _SHEAR_WEIGHT)
    products: FloatArray = stress * strain * weights

    return cast("FloatArray", 0.5 * products.sum(axis=-1))


def tensor_array(arrays: Mapping[str, FloatArray], variable: ElmerVariable) -> FloatArray | None:
    """The Voigt tensor of ``variable``: its whole array by exact VTU name, else its ``<name>_xx``... columns stacked; None when neither is there."""
    name: str = variable.lower()
    whole: FloatArray | None = arrays.get(name)

    if whole is not None:
        return np.asarray(whole, dtype=np.float64)

    columns: list[FloatArray] = []

    for component in VOIGT_COMPONENTS:
        column: FloatArray | None = arrays.get(f"{name}_{component}")

        if column is None:
            break

        columns.append(np.asarray(column, dtype=np.float64))

    if len(columns) not in _TENSOR_WIDTHS:
        return None

    return np.column_stack(columns)


def derived_arrays(arrays: Mapping[str, FloatArray]) -> dict[str, FloatArray]:
    """The arrays a set of same-association VTU arrays yields beyond its own, by VTU name: the stacked ``stress`` and ``strain`` tensors and the strain energy density.

    Empty without both a stress and a strain tensor.
    """
    stress: FloatArray | None = tensor_array(arrays, ElmerVariable.STRESS)
    strain: FloatArray | None = tensor_array(arrays, ElmerVariable.STRAIN)

    if stress is None or strain is None:
        return {}

    tensors: dict[str, FloatArray] = {ElmerVariable.STRESS.lower(): stress, ElmerVariable.STRAIN.lower(): strain}
    stacked: dict[str, FloatArray] = {name: values for name, values in tensors.items() if name not in arrays}

    return {**stacked, ElmerVariable.STRAIN_ENERGY_DENSITY.lower(): strain_energy_density(stress, strain)}


def add_derived_fields(grid: pv.DataSet) -> None:  # pragma: no cover - needs pyvista (viz extra); proven by the gated tests
    """Add the derived arrays to ``grid`` in place, point data from point data and cell data from cell data."""
    for data in (grid.point_data, grid.cell_data):
        arrays: dict[str, FloatArray] = {name: np.asarray(data[name]) for name in data.keys()}

        for name, values in derived_arrays(arrays).items():
            data[name] = values


def strain_energy(mesh: FieldMesh, *bodies: Body | str) -> float:
    """`U` `[J]`: ∫ W dV over the named bodies, or over the whole mesh when none are named."""
    query = mesh.get(ElmerVariable.STRAIN_ENERGY_DENSITY)

    if bodies:
        query = query.where_body(*bodies)

    return float(query.integrate())


def clapeyron_energy(mesh: FieldMesh, boundary: Boundary | str, force: Vec3) -> float:
    """`U` `[J]`: ½ F⃗·ū, the work a uniform traction does through the face's mean displacement ū.

    Args:
        mesh: The solved field mesh, its boundaries attached.
        boundary: The loaded face's boundary.
        force: The solve domain's share of the face's force in N, in solve coordinates; [`Run.clapeyron_energy`][femforge.run.Run.clapeyron_energy] takes the authored force.
    """
    displacement = mesh.get(ElmerVariable.DISPLACEMENT).where_boundary(boundary).surface_mean()
    mean: list[float] = cast("list[float]", np.asarray(displacement, dtype=np.float64).tolist())
    work: float = sum(component * value for component, value in zip(force.to_list(), mean, strict=True))

    return 0.5 * work
