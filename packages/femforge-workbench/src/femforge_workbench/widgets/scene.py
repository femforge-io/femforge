"""The scene widgets: the browser-side companion of a mirrored view."""

from typing import Unpack

from femforge_workbench.widgets.element import Element, Event, Prop, Shared


class SceneSync(Element):
    """Turns the mirror's triad with its camera in the browser and reports the canvas size in device pixels through `on_resize`."""

    def __init__(self, *, view_ref: Prop[str] = None, camera_id: Prop[int] = None, matrix_id: Prop[int] = None, on_resize: Event = None, **kwargs: Unpack[Shared]) -> None:
        """Creates the companion; a prop left None keeps the component's default.

        Args:
            view_ref: The trame ref of the mirrored view whose camera turns the triad and whose canvas is measured.
            camera_id: The wasm id of the main renderer's camera.
            matrix_id: The wasm id of the matrix the triad's actor carries.
            on_resize: Receives the canvas's width, height and pixel ratio whenever they change.
            kwargs: Shared trame props and events, such as `classes`, `style` and `on_click`.
        """
        super().__init__("ff-scene-sync", locals(), None, **kwargs)
