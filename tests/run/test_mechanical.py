"""Strain energy density from Voigt stress and tensorial strain, and the energies it integrates to."""

import numpy as np
import pytest

from femforge.common.vector import Vec3
from femforge.elmer.variables import ElmerVariable
from femforge.run.entities import Bodies, Body, Boundaries, Boundary, BoundaryKind
from femforge.run.fields import FieldMesh
from femforge.run.mechanical import VOIGT_COMPONENTS, clapeyron_energy, derived_arrays, strain_energy, strain_energy_density, tensor_array

E = 200e9
NU = 0.3
G = E / (2 * (1 + NU))


def test_uniaxial_stress_stores_sigma_squared_over_two_e() -> None:
    sigma = 1.0e8
    stress = np.array([[sigma, 0, 0, 0, 0, 0]], dtype=np.float64)
    strain = np.array([[sigma / E, -NU * sigma / E, -NU * sigma / E, 0, 0, 0]], dtype=np.float64)

    assert strain_energy_density(stress, strain).tolist() == pytest.approx([sigma**2 / (2 * E)])


@pytest.mark.parametrize("column", [3, 4, 5])
def test_pure_shear_with_tensorial_strain_stores_tau_squared_over_two_g(column: int) -> None:
    tau = 5.0e7
    stress = np.zeros((1, 6))
    strain = np.zeros((1, 6))
    stress[0, column] = tau
    strain[0, column] = tau / (2 * G)

    assert strain_energy_density(stress, strain).tolist() == pytest.approx([tau**2 / (2 * G)])


def test_a_2d_tensor_weighs_its_one_shear_column() -> None:
    stress = np.array([[1.0, 2.0, 3.0, 4.0]])
    strain = np.array([[1.0, 1.0, 1.0, 1.0]])

    assert strain_energy_density(stress, strain).tolist() == pytest.approx([0.5 * (1 + 2 + 3 + 2 * 4)])


@pytest.mark.parametrize(("stress_width", "strain_width"), [(6, 4), (3, 3), (9, 9)])
def test_mismatched_or_non_voigt_widths_raise(stress_width: int, strain_width: int) -> None:
    with pytest.raises(ValueError, match="width 4 or 6"):
        _ = strain_energy_density(np.zeros((2, stress_width)), np.zeros((2, strain_width)))


def test_derived_arrays_need_both_tensors_by_their_exact_names() -> None:
    tensor = np.ones((2, 6))

    assert derived_arrays({"stress": tensor}) == {}
    assert derived_arrays({"principal stress": np.ones((2, 3)), "strain": tensor}) == {}

    derived = derived_arrays({"stress": tensor, "strain": tensor})

    assert list(derived) == ["strain energy density"]
    assert derived["strain energy density"].tolist() == pytest.approx([4.5, 4.5])


@pytest.mark.parametrize("components", [VOIGT_COMPONENTS, VOIGT_COMPONENTS[:4]])
def test_split_tensor_columns_stack_into_whole_tensors(components: tuple[str, ...]) -> None:
    columns = {f"{variable}_{component}": np.full(2, float(index)) for variable in ("stress", "strain") for index, component in enumerate(components, 1)}

    derived = derived_arrays(columns)

    assert sorted(derived) == ["strain", "strain energy density", "stress"]
    assert derived["stress"].shape == (2, len(components))
    assert derived["stress"][0, :].tolist() == [float(index) for index in range(1, len(components) + 1)]


def test_an_incomplete_split_tensor_yields_nothing() -> None:
    columns = {f"{variable}_{component}": np.zeros(2) for variable in ("stress", "strain") for component in VOIGT_COMPONENTS[:3]}

    assert tensor_array(columns, ElmerVariable.STRESS) is None
    assert derived_arrays(columns) == {}


def _beam() -> FieldMesh:
    return FieldMesh(
        arrays={
            "strain energy density": np.array([2.0, 4.0, 0.0, 0.0]),
            "displacement": np.array([[0.0, 0, 0], [0.0, 0, 0], [0.0, 0, 0.001], [0.0, 0, 0.003]], dtype=np.float64),
        },
        measure=np.array([1.0, 0.5, 0.0, 0.0]),
        geometry_ids=np.array([1, 2, 101, 101], dtype=np.int64),
        bodies=Bodies([Body(name="BEAM", mesh_id=1), Body(name="CAP", mesh_id=2)]),
        area=np.array([0.0, 0.0, 1.0, 1.0]),
        boundaries=Boundaries([Boundary(name="TIP", mesh_id=3, bodies=("BEAM",), kind=BoundaryKind.EXTERNAL, sif_index=1)]),
    )


def test_strain_energy_integrates_the_density_over_the_named_bodies() -> None:
    assert strain_energy(_beam(), "BEAM") == pytest.approx(2.0)
    assert strain_energy(_beam()) == pytest.approx(4.0)


def test_clapeyron_energy_is_half_the_force_through_the_mean_face_displacement() -> None:
    assert clapeyron_energy(_beam(), "TIP", Vec3(0, 0, 100)) == pytest.approx(0.5 * 100 * 0.002)
