"""Spot-check error metrics between two vector fields sampled at the same points."""

from __future__ import annotations

from typing import cast

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from femforge.common.vector import Vec3, Vec3Field


class FieldComparison(BaseModel):
    """Aggregate error of a test field versus a reference field sampled at the same points.

    Errors are on |B_test − B_ref| in tesla; the headline metric is the field-weighted `rel_rms_error`.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    n_points: int
    """Number of sample points compared."""

    rms_error: float
    """Root-mean-square of |B_test − B_ref| over the points (tesla)."""

    max_abs_error: float
    """Largest single |B_test − B_ref| (tesla)."""

    max_error_point: Vec3Field
    """The sample point where `max_abs_error` occurred (metres) — where to look first when the fields disagree."""

    ref_rms: float
    """RMS |B_ref| over the points (tesla): the reference field scale, so `rms_error` is interpretable."""

    test_rms: float
    """RMS |B_test| over the points (tesla)."""

    rel_rms_error: float | None = None
    """**The headline metric.** Field-weighted relative error rms(|ΔB|) / rms(|B_ref|); robust to low-field points. None if every reference point is zero."""

    median_relative_error: float | None = None
    """Median of |B_test − B_ref| / |B_ref| over points with non-zero reference, or None if every reference point is zero. The robust middle of the per-point distribution."""

    mean_relative_error: float | None = None
    """Mean of |B_test − B_ref| / |B_ref| over points with non-zero reference, or None if every reference point is zero. Outlier-sensitive — prefer `rel_rms_error`."""

    def summary(self) -> str:
        """A human-readable multi-line report for the spot-check script."""

        def pct(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.4%}"

        return (
            f"  number samples    : {self.n_points}\n"
            f"  reference RMS |B| : {self.ref_rms:.5f} T\n"
            f"  test RMS |B|      : {self.test_rms:.5f} T\n"
            f"  RMS error         : {self.rms_error:.5f} T\n"
            f"  max abs error     : {self.max_abs_error:.5f} T at {self.max_error_point:.5f}\n"
            f"  rel RMS error     : {pct(self.rel_rms_error)}  (primary metric, field-weighted)\n"
            f"  median rel error  : {pct(self.median_relative_error)}\n"
            f"  mean rel error    : {pct(self.mean_relative_error)}   (outlier-sensitive)"
        )


def _row_norms(rows: NDArray[np.float64]) -> NDArray[np.float64]:
    """Per-row Euclidean norm of an `(N, 3)` array — the single site where numpy's `Any`-typed `linalg.norm` is pinned back to a concrete type."""
    return cast("NDArray[np.float64]", np.linalg.norm(rows, axis=1))


def _rms(values: NDArray[np.float64]) -> float:
    """Root-mean-square of a 1-D array, as a plain `float` (the numpy reduction reads as `Any`, so it is cast + floated at this boundary)."""
    return float(cast("float", np.sqrt(np.mean(np.square(values)))))


def compare_fields(points: NDArray[np.float64], b_reference: NDArray[np.float64], b_test: NDArray[np.float64]) -> FieldComparison:
    """Compare two `(N, 3)` fields sampled at the same `points` and return a `FieldComparison`; raises `ValueError` on mismatched or empty inputs.

    Args:
        points: The `(N, 3)` sample points, in metres.
        b_reference: The reference field at `points`; points where it is exactly zero are left out of the relative errors.
        b_test: The field under test at `points`.
    """
    if not (len(points) == len(b_reference) == len(b_test)):
        raise ValueError(f"compare_fields needs equal-length inputs, got points={len(points)} reference={len(b_reference)} test={len(b_test)}")
    if len(points) == 0:
        raise ValueError("compare_fields needs at least one sample point")

    n: int = len(points)
    abs_error: NDArray[np.float64] = _row_norms(b_test - b_reference)
    ref_norm: NDArray[np.float64] = _row_norms(b_reference)

    max_index: int = int(np.argmax(abs_error))
    # Cast the row to a typed array first (numpy scalar indexing reads as Any), then `.tolist()` gives python floats.
    worst_row: NDArray[np.float64] = cast("NDArray[np.float64]", points[max_index])
    worst_x, worst_y, worst_z = cast("list[float]", worst_row.tolist())
    max_error_point: Vec3 = Vec3(worst_x, worst_y, worst_z)
    max_abs_error: float = float(cast("float", abs_error[max_index]))

    rms_error: float = _rms(abs_error)
    ref_rms: float = _rms(ref_norm)
    test_rms: float = _rms(_row_norms(b_test))

    # Relative metrics over points with a non-zero reference (a near-null |B_ref| has no meaningful relative error).
    positive: NDArray[np.bool_] = ref_norm > 0
    mean_relative_error: float | None = None
    median_relative_error: float | None = None
    if bool(positive.any()):
        relative: NDArray[np.float64] = cast("NDArray[np.float64]", abs_error[positive] / ref_norm[positive])
        mean_relative_error = float(cast("float", np.mean(relative)))
        median_relative_error = float(cast("float", np.median(relative)))
    rel_rms_error: float | None = rms_error / ref_rms if ref_rms > 0 else None

    return FieldComparison(
        n_points=n,
        rms_error=rms_error,
        max_abs_error=max_abs_error,
        max_error_point=max_error_point,
        ref_rms=ref_rms,
        test_rms=test_rms,
        rel_rms_error=rel_rms_error,
        median_relative_error=median_relative_error,
        mean_relative_error=mean_relative_error,
    )
