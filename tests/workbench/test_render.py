"""The render settings: their validation, their state round trip, their per-user store and how the shell applies them."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from femforge.viz.field_display import EdgeStyle, Shading
from femforge_workbench.persist import LocalJsonStore
from femforge_workbench.render import (
    CHOICES,
    RENDER_KEY,
    SHARED,
    SLIDERS,
    Antialias,
    Lighting,
    RenderSettings,
    RenderVar,
    limits,
    load_render,
    render_state,
    save_render,
    settings_from_state,
)


pytest.importorskip("trame")

from trame.app import get_server  # noqa: E402

from femforge_workbench.app import Workbench  # noqa: E402
from femforge_workbench.panels import PanelComponent  # noqa: E402


def _shell(name: str, root: Path) -> Workbench:
    return Workbench(name, title="Render", server=get_server(f"render-{name}", client_type="react"), persist=LocalJsonStore(root), user="tester")


class Recorder(PanelComponent):
    """A panel that counts the restyles the shell asks of it."""

    def __init__(self, app: Workbench, id: str, title: str) -> None:
        self.restyled = 0
        super().__init__(app, id, title)

    def build(self) -> None:
        pass

    def restyle(self) -> None:
        self.restyled += 1


def test_the_mirror_takes_no_antialiasing_it_cannot_draw_and_the_scenes_take_the_line_and_shading_fields() -> None:
    settings = RenderSettings(outline_width=3.0, feature_angle=45.0, highlight_width=1.5, line_width=2.0, smooth_shading=False, split_angle=20.0, ambient=0.1, specular=0.5)

    assert settings.edges() == EdgeStyle(width=3.0, feature_angle=45.0, highlight_width=1.5, line_width=2.0)
    assert settings.shading() == Shading(smooth=False, split_angle=20.0, ambient=0.1, specular=0.5)
    with pytest.raises(ValidationError, match="the mirror cannot draw msaa"):
        _ = RenderSettings(client_antialias=Antialias.MSAA)


def test_the_state_round_trips_the_settings_and_rejects_what_does_not_validate() -> None:
    settings = RenderSettings(server_antialias=Antialias.SSAA, outline_width=4.5)
    state = render_state(settings)

    assert state[RenderVar.OUTLINE_WIDTH] == [4.5], "a slider holds its one thumb in a list"
    assert settings_from_state(state) == settings
    assert settings_from_state({**state, RenderVar.OUTLINE_WIDTH: 4.5}) == settings, "a bare number reads like a one-thumb list"
    assert settings_from_state({**state, RenderVar.FEATURE_ANGLE: []}) is None, "a slider with no thumb leaves the field unset"
    assert settings_from_state({**state, RenderVar.CLIENT_ANTIALIAS: "msaa"}) is None


def test_every_setting_has_one_variable_a_slider_or_a_choice_and_a_slider_spans_its_bounds() -> None:
    assert sorted(var.field for var in RenderVar) == sorted(RenderSettings.model_fields)
    assert set(SLIDERS) | set(CHOICES) == set(RenderVar) and not set(SLIDERS) & set(CHOICES)
    assert limits(RenderVar.BRIGHTNESS) == (0.25, 2.0) and limits(RenderVar.VIEW_ANGLE) == (10.0, 90.0)


def test_the_settings_persist_once_per_user_and_an_unreadable_save_falls_back_to_the_defaults(tmp_path: Path) -> None:
    store = LocalJsonStore(tmp_path, debounce=0.0)
    settings = RenderSettings(outline_width=1.0)

    assert load_render(store, "tester") == RenderSettings()

    save_render(store, "tester", settings)

    assert load_render(store, "tester") == settings
    assert store.load(("tester", SHARED), RENDER_KEY) == settings.model_dump(mode="json")

    store.save(("tester", SHARED), RENDER_KEY, {"outline_width": 99.0})

    assert load_render(store, "tester") == RenderSettings()


def test_the_shell_applies_saves_and_restyles_every_panel_once_and_puts_back_what_does_not_validate(tmp_path: Path) -> None:
    app = _shell("apply", tmp_path)
    panel = Recorder(app, "recorder", "Recorder")
    app.panels.append(panel)
    app.state.ready()

    with app.state:
        app.state[RenderVar.SERVER_ANTIALIAS] = str(Antialias.SSAA)

    assert app.render.server_antialias is Antialias.SSAA and panel.restyled == 1, "a chosen antialiasing applies at once"

    with app.state:
        app.state[RenderVar.LIGHTING] = str(Lighting.HEADLIGHT)

    assert app.render.lighting is Lighting.HEADLIGHT and panel.restyled == 2, "every choice applies at once"
    app.persist.flush()

    assert _shell("other", tmp_path).render.server_antialias is Antialias.SSAA, "another workbench of the user starts from the saved settings"

    app.apply_render()

    assert panel.restyled == 2, "unchanged settings redraw nothing"

    with app.state:
        app.state[RenderVar.OUTLINE_WIDTH] = [50.0]
    app.apply_render()

    assert app.state[RenderVar.OUTLINE_WIDTH] == [app.render.outline_width] and panel.restyled == 2, "a value out of range goes back"

    app.reset_render()

    assert app.render == RenderSettings() and panel.restyled == 3
