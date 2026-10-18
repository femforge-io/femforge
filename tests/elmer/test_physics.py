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
