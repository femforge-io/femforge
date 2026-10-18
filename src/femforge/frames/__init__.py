"""Solve frames: how an authored model maps onto the coordinates the solve runs in."""

from typing import Annotated, cast, get_args

from pydantic import Field

from femforge.frames.axisymmetric import Axisymmetric
from femforge.frames.base import Axis, Frame, SolvePlane
from femforge.frames.solid import Solid


FrameUnion = Annotated[Solid | Axisymmetric, Field(discriminator="kind")]
"""Any shipped frame, told apart by its ``kind``; the type of every serialized frame field."""


def shipped_frames() -> tuple[type[Frame], ...]:
    """The frame classes ``FrameUnion`` admits, in its order."""
    parts: tuple[object, ...] = get_args(FrameUnion)

    return cast("tuple[type[Frame], ...]", get_args(parts[0]))


def default_frame(model_dim: int) -> Frame:
    """The first shipped frame, with its default fields, that solves on ``model_dim`` dimensions: the frame of a run whose bundle names none."""
    for kind in shipped_frames():
        frame: Frame = kind.model_validate({})
        if frame.model_dim == model_dim:
            return frame

    raise ValueError(f"no shipped frame solves on {model_dim} dimensions")


__all__ = ["Axis", "Axisymmetric", "Frame", "FrameUnion", "SolvePlane", "Solid", "default_frame", "shipped_frames"]
