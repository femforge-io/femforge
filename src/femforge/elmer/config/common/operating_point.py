"""The operating point material property functions are evaluated at when stripping to Elmer's SI floats."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from femforge.physical.units import Quantity, Temperature, quantity


class OperatingPoint(BaseModel):
    """
    The point at which a material's property functions are evaluated when stripping to Elmer's bare SI floats.
    ``Static`` properties ignore it; temperature-dependent properties consume it via [`as_mapping`][].
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True, use_attribute_docstrings=True)

    # default_factory, not a bare default: pydantic's JSON-schema `encode_default` can't serialize a bare pint Quantity
    # default (it bypasses the `Temperature` annotation's serializer) and would warn at schema registration.
    temperature: Temperature = Field(default_factory=lambda: quantity(300, "kelvin"))
    """`T` `[K]`: Evaluation temperature for material property functions."""

    def as_mapping(self) -> dict[str, Quantity]:
        """The ephemeral name->quantity mapping the property functions consume (``prop(**at)``)."""
        return {"temperature": self.temperature}
