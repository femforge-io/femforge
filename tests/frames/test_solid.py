"""The solid frame: every hook passes the authored 3D model through."""

from pathlib import Path

import pytest

from femforge.common.vector import Direction, Vec3
from femforge.elmer.config import CoordinateSystem
from femforge.elmer.physics import Physics
from femforge.frames import Axis, Solid


def test_a_solid_frame_is_a_whole_3d_cartesian_solve() -> None:
    frame = Solid()

    assert frame.label == "solid"
    assert frame.model_dim == 3
    assert frame.coordinate_system is CoordinateSystem.CARTESIAN
    assert frame.plane is None
    assert frame.rewrites_geometry is False
    assert frame.lumps_torque is True


def test_a_solid_frame_meshes_the_authored_step(tmp_path: Path) -> None:
    authored = tmp_path / "authored.step"

    assert Solid().prepare_geometry(authored, tmp_path / "out.step") == authored
    assert not (tmp_path / "out.step").exists()


def test_every_physics_but_the_axisymmetric_stack_solves_as_itself() -> None:
    for physics in set(Physics) - {Physics.MAGNETOSTATICS_2D}:
        assert Solid().solves(physics) is physics
        assert Solid().supports(physics)

    with pytest.raises(ValueError, match="frame=Axisymmetric"):
        _ = Solid().solves(Physics.MAGNETOSTATICS_2D)

    assert not Solid().supports(Physics.MAGNETOSTATICS_2D)


def test_vectors_keep_their_authored_components_and_totals() -> None:
    vector = Vec3(1.5, -2, 3)

    assert Solid().solve_vector(vector) == (1.5, -2, 3)
    assert Solid().total(vector) == vector


def test_along_projects_onto_the_given_axis_and_needs_one() -> None:
    assert Solid().along(Vec3(3, 4, 5), Axis(direction=Direction(0, 0, -2))) == pytest.approx(-5.0)

    with pytest.raises(ValueError, match="no symmetry axis"):
        _ = Solid().along(Vec3(3, 4, 5), None)
