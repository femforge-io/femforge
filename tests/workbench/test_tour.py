"""The tour wrapper: its serialized props and events and its entry in the dist's props.json."""

import json
from typing import cast

from trame.app import get_server
from trame.ui.html import DivLayout
from trame.widgets import react

from femforge_workbench.widgets import module
from femforge_workbench.widgets.tour import Tour, TourStep
from tests.workbench.tree import find, prop, props


STEPS: list[TourStep] = [
    {"target": "#tour-run", "title": "Run The Solve", "body": "Starts the solver.", "side": "right"},
    {"target": "#tour-results", "title": "Read The Results", "body": "Fields land here."},
]


def test_the_tour_serializes_its_steps_the_model_binding_and_its_events() -> None:
    server = get_server("widgets-tour", client_type="react")
    with DivLayout(server) as layout:
        _ = Tour(model="wb_tour", steps=STEPS, on_finish=react.Callback("tour_finished($event)"))
        _ = Tour(model="wb_tour", step=react.Bind("wb_step"), on_open_change=react.Callback("noop()"), on_step_change=react.Callback("seen($event)"))

    bound, explicit = find(layout.html, "ff-tour")
    assert prop(bound, "steps") == STEPS
    assert prop(bound, "open") == {"js": "wb_tour"}
    assert prop(bound, "onOpenChange") == {"callback": {"js": 'trame.state.set("wb_tour", $event)'}}
    assert prop(bound, "onFinish") == {"callback": {"js": "tour_finished($event)"}}
    assert "model" not in props(bound) and "step" not in props(bound)

    assert prop(explicit, "onOpenChange") == {"callback": {"js": "noop()"}}
    assert "open" not in props(explicit), "an explicit handler leaves the open flag to the caller"
    assert prop(explicit, "step") == {"js": "wb_step"}
    assert prop(explicit, "onStepChange") == {"callback": {"js": "seen($event)"}}


def test_the_dist_registers_ff_tour_with_its_props_and_the_side_literals() -> None:
    entry = cast("dict[str, object]", json.loads((module.SERVE_DIR / "props.json").read_text(encoding="utf-8"))["ff-tour"])

    assert {"steps", "open", "step"} <= set(cast("list[str]", entry["props"]))
    assert cast("dict[str, list[str]]", entry["literals"])["steps.side"] == ["bottom", "left", "right", "top"]
