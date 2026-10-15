"""The Physics enum: a StrEnum usable as the physics string."""

import pytest

from femforge.elmer.physics import Physics


def test_every_physics_has_a_config():
    # The per-physics factory populates a typed stack for every Physics member.
    from femforge.elmer.config import PhysicsConfig, physics_config

    for physics in Physics:
        config = physics_config(physics)
        assert isinstance(config, PhysicsConfig)
        assert config.solvers  # every physics wires at least one solver


def test_enum_member_is_a_str():
    # StrEnum: comparable to / usable as a plain string.
    assert Physics.MAGNETOSTATICS == "magnetostatics"


def test_out_of_vocabulary_value_rejected():
    with pytest.raises(ValueError):
        Physics("nonsense")


def test_linear_elasticity_member_present() -> None:
    assert Physics.LINEAR_ELASTICITY == "linear_elasticity"
    assert "linear_elasticity" in {p.value for p in Physics}


def test_in_frame_maps_each_physics_to_its_solved_form_or_raises() -> None:
    from femforge.common.frame import Axisymmetric, Solid

    assert Physics.MAGNETOSTATICS.in_frame(Solid()) is Physics.MAGNETOSTATICS
    assert Physics.MAGNETOSTATICS.in_frame(Axisymmetric()) is Physics.MAGNETOSTATICS_2D
    assert Physics.MAGNETOSTATICS_2D.in_frame(Axisymmetric()) is Physics.MAGNETOSTATICS_2D
    assert Physics.THERMAL.in_frame(Axisymmetric()) is Physics.THERMAL

    solid_only = set(Physics) - {Physics.MAGNETOSTATICS, Physics.MAGNETOSTATICS_2D, Physics.THERMAL}
    for physics in solid_only:
        assert physics.in_frame(Solid()) is physics

        with pytest.raises(ValueError, match="no axisymmetric form"):
            _ = physics.in_frame(Axisymmetric())

    with pytest.raises(ValueError, match="frame=Axisymmetric"):
        _ = Physics.MAGNETOSTATICS_2D.in_frame(Solid())
