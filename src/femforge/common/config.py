"""The shared base of the config area models."""
# A leaf module: it must import no other femforge package, so every package can inherit ConfigArea without an import cycle.

from pydantic import BaseModel, ConfigDict


class ConfigArea(BaseModel):
    """Shared base for the config area models: ``arbitrary_types_allowed`` plus ``use_attribute_docstrings``."""

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)
