"""The field-comparison metrics: hand-built `(N, 3)` numpy fields drive `compare_fields` across identical/offset fields, worst-point pick, zero-reference handling, and length/empty guards."""

from __future__ import annotations

import math

import numpy as np
import pytest
from numpy.typing import NDArray

from femforge.validation.compare import FieldComparison, compare_fields


def _arr(*rows: tuple[float, float, float]) -> NDArray[np.float64]:
    """Build an (N, 3) float array from row tuples, the shape the samplers hand `compare_fields`."""
    return np.array(rows, dtype=np.float64)


def test_identical_fields_have_zero_error() -> None:
    points = _arr((0, 0, 0), (1, 0, 0))
    field = _arr((0, 1, 0), (0, 2, 0))
    cmp = compare_fields(points, field, field)
    assert cmp.n_points == 2
    assert cmp.rms_error == pytest.approx(0.0)
    assert cmp.max_abs_error == pytest.approx(0.0)
    assert cmp.mean_relative_error == pytest.approx(0.0)  # every reference is non-zero, every error is zero
    assert cmp.rel_rms_error == pytest.approx(0.0)
    assert cmp.median_relative_error == pytest.approx(0.0)
    assert cmp.ref_rms == pytest.approx(cmp.test_rms)


def test_uniform_offset_gives_known_rms_and_relative() -> None:
    points = _arr((0, 0, 0), (1, 0, 0))
    reference = _arr((0, 1, 0), (0, 1, 0))  # |B_ref| = 1
    test = _arr((0, 1.1, 0), (0, 1.1, 0))  # |Δ| = 0.1
    cmp = compare_fields(points, reference, test)
    assert cmp.rms_error == pytest.approx(0.1)
    assert cmp.max_abs_error == pytest.approx(0.1)
    assert cmp.mean_relative_error == pytest.approx(0.1)
    assert cmp.rel_rms_error == pytest.approx(0.1)  # rms(|ΔB|) / rms(|B_ref|) = 0.1 / 1.0
    assert cmp.median_relative_error == pytest.approx(0.1)
    assert cmp.ref_rms == pytest.approx(1.0)
    assert cmp.test_rms == pytest.approx(1.1)


def test_max_error_point_is_the_worst_location() -> None:
    points = _arr((7, 7, 7), (9, 9, 9))
    reference = _arr((1, 0, 0), (1, 0, 0))
    test = _arr((1, 0, 0), (3, 0, 0))  # errors 0, 2 -> worst at points[1]
    cmp = compare_fields(points, reference, test)
    assert cmp.max_abs_error == pytest.approx(2.0)
    assert (cmp.max_error_point.x, cmp.max_error_point.y, cmp.max_error_point.z) == (9, 9, 9)


def test_zero_reference_yields_none_relative_error() -> None:
    points = _arr((0, 0, 0))
    zero = _arr((0, 0, 0))
    cmp = compare_fields(points, zero, zero)
    assert cmp.mean_relative_error is None  # no point has a defined relative error
    assert cmp.median_relative_error is None
    assert cmp.rel_rms_error is None  # ref_rms is zero -> no field-weighted relative error either
    assert cmp.ref_rms == pytest.approx(0.0)
    assert cmp.rms_error == pytest.approx(0.0)


def test_relative_error_skips_zero_reference_points() -> None:
    points = _arr((0, 0, 0), (1, 0, 0))
    reference = _arr((0, 0, 0), (2, 0, 0))  # first point has no defined relative error
    test = _arr((1, 0, 0), (2, 0, 0))  # errors: 1, 0
    cmp = compare_fields(points, reference, test)
    assert cmp.rms_error == pytest.approx(math.sqrt((1.0**2 + 0.0**2) / 2))
    assert cmp.max_abs_error == pytest.approx(1.0)
    assert (cmp.max_error_point.x, cmp.max_error_point.y, cmp.max_error_point.z) == (0, 0, 0)
    assert cmp.mean_relative_error == pytest.approx(0.0)  # only the second point counts: 0/2
    assert cmp.median_relative_error == pytest.approx(0.0)
    assert cmp.rel_rms_error == pytest.approx(0.5)  # rms_error sqrt(0.5) / ref_rms sqrt(2) = 0.5


@pytest.mark.parametrize(
    "points, reference, test",
    [
        (_arr((0, 0, 0)), _arr((0, 0, 0)), np.empty((0, 3), dtype=np.float64)),  # test shorter
        (_arr((0, 0, 0), (1, 0, 0)), _arr((0, 0, 0)), _arr((0, 0, 0))),  # reference shorter
    ],
)
def test_length_mismatch_raises(points: NDArray[np.float64], reference: NDArray[np.float64], test: NDArray[np.float64]) -> None:
    with pytest.raises(ValueError, match="equal-length"):
        _ = compare_fields(points, reference, test)


def test_empty_input_raises() -> None:
    empty = np.empty((0, 3), dtype=np.float64)
    with pytest.raises(ValueError, match="at least one sample point"):
        _ = compare_fields(empty, empty, empty)


def test_relative_rms_is_robust_to_low_field_outliers() -> None:
    # Bulk: three strong-field points at a clean 10% error; one near-null point with the same absolute error.
    points = _arr((0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0))
    reference = _arr((1.0, 0, 0), (1.0, 0, 0), (1.0, 0, 0), (0.001, 0, 0))
    test = _arr((1.1, 0, 0), (1.1, 0, 0), (1.1, 0, 0), (0.101, 0, 0))
    cmp = compare_fields(points, reference, test)
    # The near-null point's relative error is ~100x, so the MEAN explodes...
    assert cmp.mean_relative_error is not None and cmp.mean_relative_error > 1.0
    # ...but the field-weighted rel RMS stays ~10% (the strong points dominate), and the median is the robust ~10%.
    assert cmp.rel_rms_error is not None and cmp.rel_rms_error < 0.2
    assert cmp.median_relative_error == pytest.approx(0.1)


def test_summary_renders_metrics_and_na_branch() -> None:
    with_rel = compare_fields(_arr((1, 0, 0)), _arr((2, 0, 0)), _arr((2.2, 0, 0)))
    text = with_rel.summary()
    assert "number samples    : 1" in text
    assert "rel RMS error     : 10.0000%" in text  # 0.2 / 2.0
    assert "median rel error  : 10.0000%" in text
    assert "mean rel error    : 10.0000%" in text

    no_rel = compare_fields(_arr((0, 0, 0)), _arr((0, 0, 0)), _arr((0, 0, 0)))
    na = no_rel.summary()
    assert "rel RMS error     : n/a" in na
    assert "median rel error  : n/a" in na
    assert "mean rel error    : n/a" in na


def test_comparison_round_trips_through_pydantic() -> None:
    cmp = compare_fields(_arr((0, 0, 0)), _arr((0, 1, 0)), _arr((0, 1.5, 0)))
    again = FieldComparison.model_validate(cmp.model_dump())
    assert again.rms_error == pytest.approx(0.5)
    assert again.max_error_point.x == pytest.approx(0.0)
