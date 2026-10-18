"""The ``physics`` area of a ``SimulationConfig``: which preset to solve and the authored overrides."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import WithJsonSchema, field_validator

from femforge.common.config import ConfigArea
from femforge.elmer.config.common.physics_config import PhysicsConfig
from femforge.elmer.physics import Physics


if TYPE_CHECKING:
    from femforge.frames import Frame


SELECTABLE_PHYSICS: list[str] = [physics.value for physics in Physics if physics is not Physics.MAGNETOSTATICS_2D]
"""The physics a user may select; ``MAGNETOSTATICS_2D`` is what ``MAGNETOSTATICS`` solves as under a 2D frame."""


class PhysicsSelection(ConfigArea):
    """Which physics the Elmer stage solves; ``SimulationConfig`` derives the solved stack from this selection and its frame."""

    selected: Annotated[Physics, WithJsonSchema({"type": "string", "enum": SELECTABLE_PHYSICS, "title": "Physics"})] = Physics.MAGNETOSTATICS
    """The physics as authored (magnetostatics / thermal / ...); the config's frame decides the form it solves as."""

    overrides: PhysicsConfig | None = None
    """The authored solver stack for this run (see ``SimulationConfig.customize_physics``); ``None`` solves the preset.
    Part of the config, so authored settings feed the run identity (``cache_key``) and the manifest."""

    @field_validator("selected")
    @classmethod
    def selectable(cls, selected: Physics) -> Physics:
        """Reject the internal 2D magnetostatic stack."""
        if selected is Physics.MAGNETOSTATICS_2D:
            raise ValueError(f"Physics '{selected.value}' is not selectable: select '{Physics.MAGNETOSTATICS.value}' with a 2D frame (Axisymmetric() or Cartesian2D()).")

        return selected

    def check_frame(self, frame: Frame) -> None:
        """Raise when ``selected`` has no form under ``frame`` or authored ``overrides`` disagree with it."""
        _ = frame.solves(self.selected)

        if self.overrides is not None:
            self.overrides.check_frame(frame)
