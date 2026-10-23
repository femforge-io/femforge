"""The render settings every 3D view shares, persisted once per user across workbenches."""

from collections.abc import Mapping
from enum import StrEnum
from typing import cast

from annotated_types import Ge, Le
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from femforge.common.log import log
from femforge.viz.field_display import PLAIN_SPECULAR, EdgeStyle, Shading
from femforge_workbench.persist import UiStateStore


SHARED = "_shared"
"""The workbench name of the documents every workbench of a user reads."""

RENDER_KEY = "render"
"""The persisted document of the render settings."""

PREFIX = "render_"
"""What every render setting's state variable starts with, before its field name."""


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


class Lighting(StrEnum):
    """The lights a renderer places."""

    LIGHT_KIT = "light_kit"
    THREE_LIGHTS = "three_lights"
    HEADLIGHT = "headlight"


class Projection(StrEnum):
    """How the camera projects the scene."""

    ORTHOGRAPHIC = "orthographic"
    PERSPECTIVE = "perspective"


class ToneMapping(StrEnum):
    """How the stream maps the rendered light onto the screen's range."""

    OFF = "off"
    REINHARD = "reinhard"
    EXPONENTIAL = "exponential"
    FILMIC = "filmic"


class RenderSettings(BaseModel):
    """How every 3D view draws: its antialiasing per renderer, its lights, shading, camera, tone mapping and lines."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    client_antialias: Antialias = Antialias.FXAA
    """The mirror's antialiasing, one of CLIENT_ANTIALIAS."""

    server_antialias: Antialias = Antialias.MSAA
    """The stream's antialiasing."""

    lighting: Lighting = Lighting.LIGHT_KIT
    """The lights every view places."""

    brightness: float = Field(default=1.0, ge=0.25, le=2.0)
    """`[-]`: The intensity of every light, relative to the preset's own."""

    ambient: float = Field(default=0.0, ge=0.0, le=0.5)
    """`[-]`: The share of a surface's color shown without light."""

    specular: float = Field(default=PLAIN_SPECULAR, ge=0.0, le=1.0)
    """`[-]`: The highlight strength of a body in a plain color."""

    smooth_shading: bool = True
    """Shade curved faces evenly; off shades every facet flat."""

    split_angle: float = Field(default=30.0, ge=5.0, le=90.0)
    """`[deg]`: The dihedral angle above which a smooth surface keeps its edge sharp."""

    projection: Projection = Projection.ORTHOGRAPHIC
    """How the camera projects the scene."""

    view_angle: float = Field(default=30.0, ge=10.0, le=90.0)
    """`[deg]`: The perspective camera's vertical field of view."""

    tone_mapping: ToneMapping = ToneMapping.OFF
    """The stream's tone mapping; the mirror has none."""

    exposure: float = Field(default=1.0, ge=0.25, le=4.0)
    """`[-]`: The light the tone mapping takes in, relative to the rendered."""

    outline_width: float = Field(default=2.0, ge=0.5, le=6.0)
    """`[px]`: The width of the feature-edge outline in CSS pixels."""

    feature_angle: float = Field(default=30.0, ge=5.0, le=90.0)
    """`[deg]`: The dihedral angle above which a surface edge counts as a feature edge."""

    highlight_width: float = Field(default=2.0, ge=0.5, le=6.0)
    """`[px]`: The width of a hover outline in CSS pixels; a selection draws wider in proportion."""

    line_width: float = Field(default=1.0, ge=0.5, le=6.0)
    """`[px]`: The width of a field line in CSS pixels."""

    @field_validator("client_antialias")
    @classmethod
    def drawable(cls, value: Antialias) -> Antialias:
        """Rejects an antialiasing the mirror cannot draw."""
        if value not in CLIENT_ANTIALIAS:
            raise ValueError(f"the mirror cannot draw {value}")

        return value

    def edges(self) -> EdgeStyle:
        """The outline and line widths and the feature angle as the scenes take them."""
        return EdgeStyle(width=self.outline_width, feature_angle=self.feature_angle, highlight_width=self.highlight_width, line_width=self.line_width)

    def shading(self) -> Shading:
        """The surface shading as the scenes take it."""
        return Shading(smooth=self.smooth_shading, split_angle=self.split_angle, ambient=self.ambient, specular=self.specular)


class RenderVar(StrEnum):
    """The shell's state variables of the render settings, one per field, named PREFIX and the field; a slider's holds its one thumb in a list."""

    CLIENT_ANTIALIAS = "render_client_antialias"
    SERVER_ANTIALIAS = "render_server_antialias"
    LIGHTING = "render_lighting"
    BRIGHTNESS = "render_brightness"
    AMBIENT = "render_ambient"
    SPECULAR = "render_specular"
    SMOOTH_SHADING = "render_smooth_shading"
    SPLIT_ANGLE = "render_split_angle"
    PROJECTION = "render_projection"
    VIEW_ANGLE = "render_view_angle"
    TONE_MAPPING = "render_tone_mapping"
    EXPOSURE = "render_exposure"
    OUTLINE_WIDTH = "render_outline_width"
    FEATURE_ANGLE = "render_feature_angle"
    HIGHLIGHT_WIDTH = "render_highlight_width"
    LINE_WIDTH = "render_line_width"

    @property
    def field(self) -> str:
        """The settings field the variable shows."""
        return self.value.removeprefix(PREFIX)


SLIDERS: tuple[RenderVar, ...] = tuple(var for var in RenderVar if RenderSettings.model_fields[var.field].annotation is float)
"""The settings a slider sets, which apply on its release."""

CHOICES: tuple[RenderVar, ...] = tuple(var for var in RenderVar if var not in SLIDERS)
"""The settings a toggle or a switch sets, which apply at once."""


def limits(var: RenderVar) -> tuple[float, float]:
    """The lowest and highest value a slider's setting validates."""
    metadata = cast("list[object]", RenderSettings.model_fields[var.field].metadata)
    low = next(float(cast("float", bound.ge)) for bound in metadata if isinstance(bound, Ge))
    high = next(float(cast("float", bound.le)) for bound in metadata if isinstance(bound, Le))

    return low, high


def render_state(settings: RenderSettings) -> dict[str, object]:
    """The state variables showing ``settings``."""
    values = settings.model_dump(mode="json")

    return {var: [values[var.field]] if var in SLIDERS else values[var.field] for var in RenderVar}


def settings_from_state(state: Mapping[str, object]) -> RenderSettings | None:
    """The settings the state variables show; None when they do not validate."""

    def thumb(var: RenderVar) -> object:
        value = state.get(var)
        if isinstance(value, list):
            thumbs = cast("list[object]", value)

            return thumbs[0] if thumbs else None

        return value

    try:
        return RenderSettings.model_validate({var.field: thumb(var) for var in RenderVar})
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
