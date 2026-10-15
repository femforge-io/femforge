"""Environment switches the workbench reads, each spelled once."""

import os
from enum import StrEnum


TRUE_VALUES = ("1", "true", "yes", "on")


class Setting(StrEnum):
    """The environment variables that switch optional workbench behaviour on."""

    OCP_VSCODE = "FEMFORGE_OCP_VSCODE"
    """Also push every CompoundRenderable to the OCP CAD Viewer in VS Code."""


def enabled(setting: Setting) -> bool:
    """Whether the switch is set to a true value."""
    return os.environ.get(setting, "").strip().lower() in TRUE_VALUES
