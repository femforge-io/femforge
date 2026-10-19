"""The pure geometry-fact helpers: weighted centroids, bounds, element area vectors and the mean outward normal."""

import math

import numpy as np
import pytest

from femforge.common.vector import Direction, Vec3
from femforge.meshing.geometry_utils import EntityGeometry
from femforge.meshing.groups import Bounds
from femforge.meshing.measure import NORMAL_FLOOR, area_vectors, bounds_of, combine, mean_normal


def _piece(x: float, mass: float) -> EntityGeometry:
    return EntityGeometry(dim=3, tag=1, bbox_min=(x, 0.0, 0.0), bbox_max=(x, 0.0, 0.0), center_of_mass=(x, 0.0, 0.0), volume=mass)


def test_combine_weights_each_centroid_by_its_measure() -> None:
    centroid, measure = combine([_piece(0.0, 1.0), _piece(4.0, 3.0)])

    assert centroid == Vec3(3.0, 0.0, 0.0)
    assert measure == 4.0


def test_combine_falls_back_to_the_mean_of_pieces_that_measure_nothing() -> None:
    centroid, measure = combine([_piece(0.0, 0.0), _piece(4.0, 0.0)])

    assert centroid == Vec3(2.0, 0.0, 0.0)
    assert measure == 0.0


def test_bounds_of_spans_every_point() -> None:
    bounds = bounds_of(np.array([[0.0, -1.0, 2.0], [3.0, 1.0, -2.0]]))

    assert bounds == Bounds(low=Vec3(0.0, -1.0, -2.0), high=Vec3(3.0, 1.0, 2.0))
    assert bounds.diagonal == pytest.approx(math.sqrt(9 + 4 + 16))


def test_a_counter_clockwise_triangle_points_up_with_half_its_box() -> None:
    triangle = np.array([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]])

    assert area_vectors(triangle).tolist() == [[0.0, 0.0, 0.5]]


def test_a_quad_sums_its_cross_products() -> None:
    quad = np.array([[[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 1.0, 0.0], [0.0, 1.0, 0.0]]])

    assert area_vectors(quad).tolist() == [[0.0, 0.0, 2.0]]


def test_a_segment_turns_its_chord_clockwise() -> None:
    segment = np.array([[[0.0, 0.0, 0.0], [0.0, 2.0, 0.0]]])

    assert area_vectors(segment).tolist() == [[2.0, 0.0, 0.0]]


def test_the_mean_normal_turns_each_entity_out_by_its_side() -> None:
    vectors = np.array([[-1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]])

    assert mean_normal(vectors, [-1, -1], 2.0) == Direction(1, 0, 0)


def test_faces_that_cancel_have_no_mean_normal() -> None:
    vectors = np.array([[1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]])

    assert mean_normal(vectors, [1, 1], 2.0) is None


def test_an_unknown_side_or_an_empty_group_has_no_mean_normal() -> None:
    vectors = np.array([[1.0, 0.0, 0.0]])

    assert mean_normal(vectors, [0], 1.0) is None
    assert mean_normal(vectors, [1], 0.0) is None


def test_a_shallow_arc_keeps_its_mean_normal() -> None:
    vectors = np.array([[0.952, 0.0, 0.0]])

    assert 0.952 > NORMAL_FLOOR
    assert mean_normal(vectors, [1], 1.0) == Direction(1, 0, 0)
