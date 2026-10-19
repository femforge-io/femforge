"""Solve frames: how an authored model maps onto the coordinates the solve runs in."""

from typing import Annotated, cast, get_args

from pydantic import Field, ValidationError

from femforge.frames.axisymmetric import Axisymmetric
from femforge.frames.base import Axis, BodyMismatch, Frame, FrameCheck, FrameGeometryError, GeometryCheck, SolvePlane
from femforge.frames.cartesian_2d import Cartesian2D, PlaneState
from femforge.frames.solid import Solid


FrameUnion = Annotated[Solid | Axisymmetric | Cartesian2D, Field(discriminator="kind")]
"""Any shipped frame, told apart by its ``kind``; the type of every serialized frame field."""


def shipped_frames() -> tuple[type[Frame], ...]:
    """The frame classes ``FrameUnion`` admits, in its order."""
    parts: tuple[object, ...] = get_args(FrameUnion)

    return cast("tuple[type[Frame], ...]", get_args(parts[0]))


def default_frame(model_dim: int) -> Frame:
    """The first shipped frame, with its default fields, that solves on ``model_dim`` dimensions: the frame of a run whose bundle names none; frames with a required field are skipped."""
    for kind in shipped_frames():
        try:
            frame: Frame = kind.model_validate({})
        except ValidationError:
            continue

        if frame.model_dim == model_dim:
            return frame

    raise ValueError(f"no shipped frame solves on {model_dim} dimensions")


__all__ = [
    "Axis",
    "Axisymmetric",
    "BodyMismatch",
    "Cartesian2D",
    "Frame",
    "FrameCheck",
    "FrameGeometryError",
    "FrameUnion",
    "GeometryCheck",
    "PlaneState",
    "SolvePlane",
    "Solid",
    "default_frame",
    "shipped_frames",
]
