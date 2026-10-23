"""The render settings every 3D view shares, persisted once per user across workbenches."""

from collections.abc import Mapping
from enum import StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from femforge.common.log import log
from femforge.viz.field_display import EdgeStyle
from femforge_workbench.persist import UiStateStore


SHARED = "_shared"
"""The workbench name of the documents every workbench of a user reads."""

RENDER_KEY = "render"
"""The persisted document of the render settings."""


class Antialias(StrEnum):
    """How a renderer smooths the edges it draws."""

    OFF = "off"
    FXAA = "fxaa"
    MSAA = "msaa"
    SSAA = "ssaa"


CLIENT_ANTIALIAS: tuple[Antialias, ...] = (Antialias.OFF, Antialias.FXAA, Antialias.SSAA)
"""What the mirror can draw: VTK.wasm ignores the window's multisamples."""

SERVER_ANTIALIAS: tuple[Antialias, ...] = tuple(Antialias)
"""What the stream can draw."""


class RenderSettings(BaseModel):
    """How every 3D view draws: its antialiasing per renderer and the width and angle of its outlines."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    client_antialias: Antialias = Antialias.FXAA
    """The mirror's antialiasing, one of CLIENT_ANTIALIAS."""

    server_antialias: Antialias = Antialias.MSAA
    """The stream's antialiasing."""

    outline_width: float = Field(default=2.0, ge=0.5, le=6.0)
    """`[px]`: The width of the feature-edge outline in CSS pixels."""

    feature_angle: float = Field(default=30.0, ge=5.0, le=90.0)
    """`[deg]`: The dihedral angle above which a surface edge counts as a feature edge."""

    highlight_width: float = Field(default=2.0, ge=0.5, le=6.0)
    """`[px]`: The width of a hover outline in CSS pixels; a selection draws wider in proportion."""

    @field_validator("client_antialias")
    @classmethod
    def drawable(cls, value: Antialias) -> Antialias:
        """Rejects an antialiasing the mirror cannot draw."""
        if value not in CLIENT_ANTIALIAS:
            raise ValueError(f"the mirror cannot draw {value}")

        return value

    def edges(self) -> EdgeStyle:
        """The outline widths and angle as the scenes take them."""
        return EdgeStyle(width=self.outline_width, feature_angle=self.feature_angle, highlight_width=self.highlight_width)


class RenderVar(StrEnum):
    """The shell's state variables of the render settings, one per field; a slider's holds its one thumb in a list."""

    CLIENT_ANTIALIAS = "render_client_antialias"
    SERVER_ANTIALIAS = "render_server_antialias"
    OUTLINE_WIDTH = "render_outline_width"
    FEATURE_ANGLE = "render_feature_angle"
    HIGHLIGHT_WIDTH = "render_highlight_width"


SLIDERS: tuple[RenderVar, ...] = (RenderVar.OUTLINE_WIDTH, RenderVar.FEATURE_ANGLE, RenderVar.HIGHLIGHT_WIDTH)
"""The settings a slider sets."""


def render_state(settings: RenderSettings) -> dict[str, object]:
    """The state variables showing ``settings``."""
    return {
        RenderVar.CLIENT_ANTIALIAS: str(settings.client_antialias),
        RenderVar.SERVER_ANTIALIAS: str(settings.server_antialias),
        RenderVar.OUTLINE_WIDTH: [settings.outline_width],
        RenderVar.FEATURE_ANGLE: [settings.feature_angle],
        RenderVar.HIGHLIGHT_WIDTH: [settings.highlight_width],
    }


def settings_from_state(state: Mapping[str, object]) -> RenderSettings | None:
    """The settings the state variables show; None when they do not validate."""

    def thumb(var: RenderVar) -> object:
        value = state.get(var)
        if isinstance(value, list):
            thumbs = cast("list[object]", value)

            return thumbs[0] if thumbs else None

        return value

    fields: dict[str, object] = {
        "client_antialias": state.get(RenderVar.CLIENT_ANTIALIAS),
        "server_antialias": state.get(RenderVar.SERVER_ANTIALIAS),
        "outline_width": thumb(RenderVar.OUTLINE_WIDTH),
        "feature_angle": thumb(RenderVar.FEATURE_ANGLE),
        "highlight_width": thumb(RenderVar.HIGHLIGHT_WIDTH),
    }
    try:
        return RenderSettings.model_validate(fields)
    except ValidationError:
        return None


def load_render(store: UiStateStore, user: str) -> RenderSettings:
    """The user's saved render settings; the defaults when none are saved or the saved ones no longer validate."""
    saved = store.load((user, SHARED), RENDER_KEY)
    if saved is None:
        return RenderSettings()

    try:
        return RenderSettings.model_validate(saved)
    except ValidationError:
        log.bind(user=user).warning("Saved render settings are invalid; using the defaults.")

        return RenderSettings()


def save_render(store: UiStateStore, user: str, settings: RenderSettings) -> None:
    """Saves the user's render settings for every workbench.

    Args:
        store: The store the shell persists its UI state in.
        user: The user the settings belong to.
        settings: The settings to save.
    """
    store.save((user, SHARED), RENDER_KEY, settings.model_dump(mode="json"))
