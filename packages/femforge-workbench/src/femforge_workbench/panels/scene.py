"""The scene panels: one off-screen pyvista plotter each, mirrored in the browser by VTK.wasm or streamed by trame-rca, under a preset toolbar."""

import asyncio
import copy
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import partial, wraps
from pathlib import Path
from typing import TYPE_CHECKING, cast

import platformdirs
import pyvista as pv
from trame.widgets import html, rca, react, vtklocal
from trame_rca.rca import window_wrapper
from trame_rca.schedulers import RcaImageRenderScheduler
from trame_rca.view_adapter import RcaViewAdapter
from trame_vtklocal.module.protocol import ObjectManagerAPI
from vtkmodules.vtkCommonDataModel import vtkCompositeDataSet, vtkDataSet, vtkPolyData
from vtkmodules.vtkCommonMath import vtkMatrix4x4
from vtkmodules.vtkFiltersCore import vtkCellDataToPointData
from vtkmodules.vtkFiltersGeometry import vtkDataSetSurfaceFilter
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
from vtkmodules.vtkRenderingCore import vtkActor, vtkCamera, vtkCompositePolyDataMapper, vtkDataSetMapper, vtkRenderer, vtkRenderWindow, vtkRenderWindowInteractor

from femforge.common.log import log
from femforge.common.utils import title
from femforge.viz.field_display import camera_needs_reset
from femforge.viz.scene import Renderable
from femforge_workbench.panels import Carry, Cls, PanelComponent
from femforge_workbench.persist import APP_NAME
from femforge_workbench.registry import WorkbenchSpec
from femforge_workbench.reload import Reloader, default_roots
from femforge_workbench.theme import hex_color
from femforge_workbench.widgets.button import Button
from femforge_workbench.widgets.scene import SceneSync
from femforge_workbench.widgets.toggle import ToggleGroup, ToggleGroupItem


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


STILL_SCALE = 2
TARGET_FPS = 30
INTERACTIVE_QUALITY = 60
STILL_QUALITY = 92
ENCODER = "jpeg"
INTERACTIVE_PIXELS = 2_000_000
"""The encoded pixel count while a button is held; the 2x still returns LIFT_DELAY after the release."""
LIFT_DELAY = 0.4
MULTI_SAMPLES = 8
SCREENSHOT_SCALE = 2
AXES_LINE_WIDTH = 3
THROTTLE_RATE = 20
CACHE_BYTES = 512 << 20
CLIENT_LIMIT_MB = 64
"""The geometry the mirror would fetch above which a scene streams instead."""
KILOBYTE = 1024
WASM_CONFIG = {"rendering": "webgl", "exec": "sync", "mode": "wasm32"}
VIEW_STYLE = {"flex": "1", "minHeight": "0", "position": "relative"}
NOTICE = "pointer-events-none absolute inset-0 flex items-center justify-center px-4 text-center text-body text-muted-foreground"
FILL_STYLE = {"width": "100%", "height": "100%"}
IMAGE_STYLE = {"width": "100%", "height": "100%", "objectFit": "contain"}
"""trame-rca sizes the frame by width alone, whose aspect then drives the host's height and loops the area's resize observer."""
TITLE_SEPARATOR = ": "
STORE_PREFIX = "view-"
AXES_LAYER = 1
AXES_VIEWPORT = (0.0, 0.0, 0.18, 0.22)
AXES_DISTANCE = 4.0
AXES_MARGIN = 1.3
"""Both in axis lengths: how far the corner camera stands off and how much of the turning triad it keeps in view."""
PRESS = "Press"
RELEASE = "Release"
WHEEL_START = "StartMouseWheel"
WHEEL_END = "EndMouseWheel"
MOUSE_MOVE = "MouseMove"
USE_CELL_DATA = 2
USE_CELL_FIELD_DATA = 4

type Vector = tuple[float, float, float]
type Event = Mapping[str, object]


class ViewMode(StrEnum):
    """Where the frames are rendered: mirrored in the browser by VTK.wasm, or streamed from the server."""

    CLIENT = "client"
    SERVER = "server"


class View(StrEnum):
    """The toolbar's camera presets."""

    FIT = "Fit"
    TOP = "Top"
    BOTTOM = "Bottom"
    FRONT = "Front"
    RIGHT = "Right"
    LEFT = "Left"
    PLANE = "2D"
    ISO = "Iso"


class StateVar(StrEnum):
    """The per-panel state variables the toolbar binds."""

    MODE = "mode"
    STATUS = "status"
    NOTICE = "notice"


class StoreKey(StrEnum):
    """The fields of the persisted view document."""

    MODE = "mode"


class CameraKey(StrEnum):
    """The fields of the camera state the mirror reports."""

    ID = "Id"
    POSITION = "Position"
    FOCAL_POINT = "FocalPoint"
    VIEW_UP = "ViewUp"
    VIEW_ANGLE = "ViewAngle"
    PARALLEL_SCALE = "ParallelScale"


class ProgressKey(StrEnum):
    """The fields of the mirror's sync progress event."""

    ACTIVE = "active"
    ELAPSED = "elapsed"


class StatsKey(StrEnum):
    """The fields of the stream's statistics event."""

    FPS = "fps"
    BPS = "bps"


class SizeKey(StrEnum):
    """The fields of the mirror's canvas size event."""

    WIDTH = "width"
    HEIGHT = "height"
    RATIO = "ratio"


SIDES: dict[View, tuple[Vector, Vector]] = {
    View.TOP: ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    View.BOTTOM: ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    View.FRONT: ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    View.RIGHT: ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    View.LEFT: ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    View.PLANE: ((0.0, 0.0, -1.0), (1.0, 0.0, 0.0)),
    View.ISO: ((1.0, -1.0, 1.0), (0.0, 0.0, 1.0)),
}
"""The camera direction and up vector of each preset; Fit keeps the current ones."""


@dataclass(frozen=True)
class Footprint:
    """What the mirror will fetch: the cells it draws and their memory."""

    cells: int
    megabytes: float


@dataclass(frozen=True)
class Drawn:
    """The outcome of a draw: whether the camera was kept, and the mirror's footprint (None for the stream)."""

    kept: bool
    footprint: Footprint | None


def vector(value: object) -> Vector:
    """A camera state field as a 3-tuple of floats."""
    x, y, z = cast("Sequence[float]", value)

    return (float(x), float(y), float(z))


def view_rotation(camera: vtkCamera) -> list[float]:
    """The rotation that shows the world axes as the camera sees them: rows right, up and back of the view basis, as a 4x4."""
    dx, dy, dz = camera.GetDirectionOfProjection()
    ux, uy, uz = camera.GetViewUp()
    along = ux * dx + uy * dy + uz * dz
    ux, uy, uz = ux - along * dx, uy - along * dy, uz - along * dz
    length = math.hypot(ux, uy, uz) or 1.0
    ux, uy, uz = ux / length, uy / length, uz / length
    rx, ry, rz = dy * uz - dz * uy, dz * ux - dx * uz, dx * uy - dy * ux

    return [rx, ry, rz, 0.0, ux, uy, uz, 0.0, -dx, -dy, -dz, 0.0, 0.0, 0.0, 0.0, 1.0]


def surface_with_point_data(dataset: vtkDataSet) -> vtkPolyData:
    """The skin of a dataset, its cell data averaged onto the points."""
    # The VTK stubs declare these overloaded methods twice without @overload, which pyright reads as partially unknown.
    skin = vtkDataSetSurfaceFilter()
    skin.SetInputData(dataset)  # pyright: ignore[reportUnknownMemberType]
    to_points = vtkCellDataToPointData()
    to_points.SetInputConnection(skin.GetOutputPort())
    to_points.Update()  # pyright: ignore[reportUnknownMemberType]

    return cast("vtkPolyData", to_points.GetPolyDataOutput())  # pyright: ignore[reportUnknownMemberType]


def mirror_friendly(plotter: pv.Plotter) -> Footprint:
    """Swaps every dataset but polydata that an actor maps for its surface with point scalars, gives the polydata 32-bit cells, turns depth peeling off, and sizes what the mirror will fetch."""
    # VTK.wasm renders through OpenGL ES 3, which has no built-in dual depth peeling: a peeling renderer logs an error per frame and sorts nothing.
    plotter.renderer.SetUseDepthPeeling(False)

    def narrowed(data: vtkDataSet) -> None:
        """Moves a polydata's cell arrays to 32-bit storage where its ids fit."""
        if not isinstance(data, vtkPolyData):
            return

        # VTK names a 64-bit vtkIdTypeArray by that class, and the wasm32 runtime reads it with its own 32-bit vtkIdType, so every
        # line or polygon is drawn from its first point to point 0.
        for cell_array in (data.GetVerts(), data.GetLines(), data.GetPolys(), data.GetStrips()):
            if cell_array.IsStorage64Bit() and cell_array.CanConvertTo32BitStorage():
                _ = cell_array.ConvertTo32BitStorage()

    def leaves(data: object) -> list[vtkDataSet]:
        """The datasets of a composite input, which a composite mapper offers in place of a single one."""
        if not isinstance(data, vtkCompositeDataSet):
            return []

        walk = data.NewIterator()
        walk.InitTraversal()
        found: list[vtkDataSet] = []
        while not walk.IsDoneWithTraversal():
            leaf = walk.GetCurrentDataObject()
            if isinstance(leaf, vtkDataSet):
                found.append(leaf)

            walk.GoToNextItem()

        return found

    def skinned(mapper: vtkDataSetMapper, dataset: vtkDataSet) -> vtkPolyData:
        surface = surface_with_point_data(dataset)
        mapper.SetInputData(surface)  # pyright: ignore[reportUnknownMemberType]  # overloaded twice in the VTK stubs without @overload
        if mapper.GetScalarMode() == USE_CELL_DATA:
            mapper.SetScalarModeToUsePointData()
        elif mapper.GetScalarMode() == USE_CELL_FIELD_DATA:
            mapper.SetScalarModeToUsePointFieldData()

        return surface

    cells = 0
    kilobytes = 0.0
    for prop in plotter.renderer.actors.values():
        if not isinstance(prop, vtkActor):
            continue

        # Only a vtkDataSetMapper can hold other datasets; a polydata mapper is counted as it is.
        mapper = prop.GetMapper()
        if mapper is None:
            continue

        # pyvista feeds add_mesh through an algorithm, whose output is empty until the pipeline runs.
        mapper.Update()  # pyright: ignore[reportUnknownMemberType]  # overloaded twice in the VTK stubs without @overload
        dataset = cast("vtkDataSet | None", mapper.GetInput())  # pyright: ignore[reportUnknownMemberType]  # GetInput is declared twice in the VTK stubs without @overload
        if dataset is None:
            # A composite mapper (the field scene's bodies) has no single input; pyvista has already made its leaves polydata.
            composite = cast("object", mapper.GetInputDataObject(0, 0))  # pyright: ignore[reportUnknownMemberType]  # GetInputDataObject is declared twice in the VTK stubs without @overload
            for leaf in leaves(composite):
                narrowed(leaf)
                cells += leaf.GetNumberOfCells()
                kilobytes += leaf.GetActualMemorySize()

            continue

        # A volume grid freezes the tab, and VTK serializes any other non-polydata input of a mapper that never rendered through a
        # vtkGeometryFilter it frees on return, so the mirror is sent objects that no longer exist and stalls.
        if isinstance(mapper, vtkDataSetMapper) and not isinstance(dataset, vtkPolyData):
            dataset = skinned(mapper, dataset)

        narrowed(dataset)
        cells += dataset.GetNumberOfCells()
        kilobytes += dataset.GetActualMemorySize()

    return Footprint(cells, kilobytes / KILOBYTE)


def refresh_composites(plotter: pv.Plotter) -> None:
    """Hands every composite mapper whose block attributes changed since its input was set a new container over the same blocks."""
    for prop in plotter.renderer.actors.values():
        if not isinstance(prop, vtkActor):
            continue

        mapper = prop.GetMapper()
        if not isinstance(mapper, vtkCompositePolyDataMapper):
            continue

        attributes = mapper.GetCompositeDataDisplayAttributes()
        composite = cast("object", mapper.GetInputDataObject(0, 0))  # pyright: ignore[reportUnknownMemberType]  # GetInputDataObject is declared twice in the VTK stubs without @overload
        if attributes is None or not isinstance(composite, vtkCompositeDataSet) or attributes.GetMTime() <= composite.GetMTime():
            continue

        # VTK.wasm 9.7 writes a block's visibility into the attributes without Modified(), so its mapper keeps the blocks it last drew
        # until its input changes.
        fresh = composite.NewInstance()
        fresh.CompositeShallowCopy(composite)
        mapper.SetInputDataObject(fresh)  # pyright: ignore[reportUnknownMemberType]  # overloaded twice in the VTK stubs without @overload


_tolerated = False


def empty_status() -> dict[str, object]:
    """A fresh answer for a window the object manager no longer has: it holds nothing."""
    return {"ids": [], "hashes": {}, "ignore_ids": [], "cameras": [], "force_push": [], "interactor": 0}


def tolerate_dropped_objects() -> None:
    """Makes the mirror's status request answer for a window its manager no longer has instead of raising, once for the process."""
    global _tolerated
    if _tolerated:
        return

    _tolerated = True
    status = ObjectManagerAPI.get_status

    @wraps(status)
    def get_status(self: ObjectManagerAPI, obj_id: int) -> object:
        try:
            return cast("object", status(self, obj_id))
        # trame-vtklocal 1.7.1 reads `interactor` outside the `if renderWindow` that assigns it, so asking about the
        # window of a disposed panel raises before it can answer.
        except UnboundLocalError:
            log.bind(window=obj_id).debug("Mirror asked about a window the panel has released; answering an empty scene.")

            return empty_status()

    ObjectManagerAPI.get_status = get_status  # pyright: ignore[reportAttributeAccessIssue]  # the wslink RPC is untyped and its decorator is on the wrapped function


class Triad:
    """The mirror's orientation axes: a layer-1 corner renderer whose vtkAxesActor is turned by a user matrix the browser keeps aligned with its camera."""

    def __init__(self, window: vtkRenderWindow) -> None:
        """Adds the corner renderer and its hidden axes to `window`."""
        window.SetNumberOfLayers(AXES_LAYER + 1)
        self.renderer = vtkRenderer()
        self.renderer.SetLayer(AXES_LAYER)
        self.renderer.SetViewport(*AXES_VIEWPORT)
        self.renderer.InteractiveOff()
        self.matrix = vtkMatrix4x4()
        self.actor = vtkAxesActor()
        # The caption actors of the labels are 2D and never serialized for the mirror.
        self.actor.AxisLabelsOff()
        self.actor.SetUserMatrix(self.matrix)  # pyright: ignore[reportUnknownMemberType]  # overloaded twice in the VTK stubs without @overload
        self.actor.SetVisibility(False)
        self.renderer.AddActor(self.actor)
        window.AddRenderer(self.renderer)
        # The rotated arrows sweep a sphere around the origin, so the camera frames that sphere instead of the unturned actor's bounds.
        reach = max(self.actor.GetTotalLength())
        camera = self.renderer.GetActiveCamera()
        camera.ParallelProjectionOn()
        camera.SetPosition(0.0, 0.0, AXES_DISTANCE * reach)
        camera.SetFocalPoint(0.0, 0.0, 0.0)
        camera.SetViewUp(0.0, 1.0, 0.0)
        camera.SetParallelScale(AXES_MARGIN * reach)
        camera.SetClippingRange((AXES_DISTANCE - AXES_MARGIN) * reach, (AXES_DISTANCE + AXES_MARGIN) * reach)

    def align(self, camera: vtkCamera) -> None:
        """Turns the axes as the world looks from the camera."""
        self.matrix.DeepCopy(view_rotation(camera))

    def show(self, shown: bool) -> None:
        """Shows or hides the axes."""
        self.actor.SetVisibility(shown)


class InteractiveCap(RcaViewAdapter):
    """Caps the encoded pixels while a button is held and lifts the cap after the release, so a 4K stream interacts at INTERACTIVE_PIXELS and settles on one 2x still."""

    def __init__(self, window: object, name: str, *, scheduler: RcaImageRenderScheduler, sized: Callable[[], None] | None = None, pixels: int = INTERACTIVE_PIXELS, delay: float = LIFT_DELAY) -> None:
        """Sets the adapter up with the cap lifted.

        Args:
            window: The wrapped render window the stream encodes.
            name: The remote-controlled area the adapter serves.
            scheduler: The scheduler that renders and encodes the frames.
            sized: Called after every resize of the window; None calls nothing.
            pixels: The encoded pixel count while a button is held.
            delay: Seconds after the release before the cap lifts.
        """
        super().__init__(window, name, scheduler=scheduler, do_schedule_render_on_interaction=False)
        self.sized = sized
        self.pixels = pixels
        self.delay = delay
        self.capped = False
        self.pressed: set[str] = set()
        """The buttons held, tracked as the base adapter tracks them so the cap and its discard filter agree."""
        self.lift_handle: asyncio.TimerHandle | None = None

    def on_interaction(self, origin: object, event: Event) -> None:
        """Caps on a press, lifts after the release that ends it; the client also sends releases without a press, which the base adapter discards and which must not lift the cap."""
        kind = str(event.get("type", ""))
        press = kind.removesuffix(RELEASE) + PRESS
        if PRESS in kind:
            self.pressed.add(kind)
            self.cap()
        elif kind == WHEEL_START:
            self.cap()
        elif kind == WHEEL_END or (kind.endswith(RELEASE) and press in self.pressed):
            self.pressed.discard(press)
            self.schedule_lift()

        super().on_interaction(origin, event)
        if kind != MOUSE_MOVE or self.capped:
            self.schedule_render()

    def cap(self) -> None:
        """Caps the encoded pixels and the quality, cancelling a lift still due."""
        self.cancel_lift()
        if self.capped:
            return

        self.capped = True
        self.update_quality(INTERACTIVE_QUALITY, STILL_QUALITY)
        self.max_pixel_count = self.pixels

    def schedule_lift(self) -> None:
        """Lifts the cap after the delay, replacing a lift already due."""
        if not self.capped:
            return

        self.cancel_lift()
        self.lift_handle = asyncio.get_running_loop().call_later(self.delay, self.lift)

    def lift(self) -> None:
        """Restores the full 2x size; equal qualities make the scheduler render exactly one still frame."""
        self.lift_handle = None
        self.capped = False
        self.update_quality(STILL_QUALITY, STILL_QUALITY)
        self.max_pixel_count = 0

    def update_size(self, origin: object, size: Mapping[str, object]) -> None:
        """Sizes the window to the client's box, then tells the panel, which drew its scene before any viewer had a size."""
        super().update_size(origin, size)
        if self.sized is not None:
            self.sized()

    def cancel_lift(self) -> None:
        """Drops a lift still due, so it never resizes a window the panel has already closed."""
        if self.lift_handle is not None:
            self.lift_handle.cancel()
            self.lift_handle = None


class ScenePanel(PanelComponent):
    """Owns an off-screen plotter and renders Renderables into it, mirrored in the browser or streamed, keeping the camera when it still frames the model.

    Only one panel of a workbench mirrors at a time, and a scene too large for the mirror streams without persisting that choice.
    """

    def __init__(self, app: "Workbench", id: str, title: str, *, renderable: Renderable | None = None, mode: ViewMode = ViewMode.CLIENT) -> None:
        """Sets up the plotter and its interactor, takes the saved or given mode, and draws `renderable` when given.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            renderable: The scene drawn at once; None starts empty.
            mode: Where the frames render while the store keeps no choice for the panel.
        """
        self.plotter = pv.Plotter(off_screen=True)
        self.plotter.background_color = pv.Color(hex_color(app.palette.paper))
        self.plotter.parallel_projection = True
        self.plotter.enable_anti_aliasing("msaa", multi_samples=MULTI_SAMPLES)
        self.base_font = copy.deepcopy(self.plotter.theme.font)
        # pyvista's document theme writes black text, which a dark palette's background swallows.
        self.base_font.color = hex_color(app.palette.text)
        # pyvista installs a vtkGenericRenderWindowInteractor with a Python-subclassed style, which the mirror instantiates verbatim and never binds to browser events.
        self.interactor = vtkRenderWindowInteractor()
        self.interactor.SetRenderWindow(self.window())
        self.interactor.SetInteractorStyle(vtkInteractorStyleTrackballCamera())
        self.triad = Triad(self.window())

        self.mode = self.saved_mode(app, id) or mode
        if self.mode is ViewMode.CLIENT:
            self.claim_mirror(app)

        self.scale_fonts(STILL_SCALE if self.mode is ViewMode.SERVER else 1)
        self.renderable: Renderable | None = None
        self.view: vtklocal.LocalView | None = None
        self.sync: SceneSync | None = None
        self.area: rca.RemoteControlledArea | None = None
        self.handler: InteractiveCap | None = None
        self.host: html.Div | None = None
        self.shown = False
        self.canvas: tuple[int, int] | None = None
        """The mirror's canvas in device pixels, None until the browser has reported it."""
        self.framed = False
        """Whether the camera has been fitted against a viewer's size; the scene is drawn before any viewer has one."""
        self.pixel_ratio = 1.0
        self.closing: asyncio.Task[None] | None = None
        """The stream's close, held while it runs."""
        self.axes_shown = False
        self.camera_id = 0
        """The wasm id of the main camera, whose reports alone are kept."""
        self.last_camera: Mapping[str, object] | None = None
        """The camera the mirror last reported, adopted by the plotter before it is read or pushed."""
        self.footprint: Footprint | None = None

        super().__init__(app, id, title)

        if renderable is not None:
            self.render(renderable, keep_camera=False)

    def claim_mirror(self, app: "Workbench") -> None:
        """Streams every other panel that mirrors: VTK.wasm hands browser events to the last view mounted on a page, so an older mirror would only look interactive."""
        for panel in [built for built in app.panels if built is not self and isinstance(built, ScenePanel) and built.mode is ViewMode.CLIENT]:
            panel.switch(ViewMode.SERVER, persist=False)
            panel.status_line("a newer panel mirrors; streaming")
            log.bind(panel=panel.id).info("A newer panel took the mirror; streaming this scene.")

    @staticmethod
    def saved_mode(app: "Workbench", id: str) -> ViewMode | None:
        """The mode the store keeps for the panel, None when it has none."""
        saved = app.persist.load(app.scope, f"{STORE_PREFIX}{id}") or {}
        value = saved.get(StoreKey.MODE)

        return ViewMode(value) if value in ViewMode else None

    @property
    def store_key(self) -> str:
        """The persisted document of this panel's view mode."""
        return f"{STORE_PREFIX}{self.id}"

    def scale_fonts(self, factor: int) -> None:
        """Scales a copy of the theme's fonts by `factor`: text actors are sized in window pixels, and the theme shares its font object with pyvista's global theme."""
        font = copy.deepcopy(self.base_font)
        font.size = self.base_font.size * factor
        font.title_size = self.base_font.title_size * factor
        font.label_size = self.base_font.label_size * factor
        self.plotter.theme.font = font

    def build(self) -> None:
        """Builds the camera presets, the mode toggle and the axes and screenshot buttons over the view."""
        self.state[self.key(StateVar.MODE)] = str(self.mode)
        self.state[self.key(StateVar.STATUS)] = ""
        self.state[self.key(StateVar.NOTICE)] = ""
        with html.Div(classes=str(Cls.TOOLBAR)):
            for view in View:
                Button(str(view), size="xs", variant="ghost", on_click=self.callback(partial(self.look, view)))

            html.Span(classes=str(Cls.SPACER))
            html.Span([react.Bind(self.key(StateVar.STATUS))], classes=str(Cls.CAPTION))
            with ToggleGroup(
                type_="single",
                variant="outline",
                size="sm",
                value=react.Bind(self.key(StateVar.MODE)),
                on_value_change=self.callback(self.set_mode, args="[$event]"),
                title="Where the frames are rendered",
            ):
                for mode in ViewMode:
                    ToggleGroupItem(title(str(mode)), value=str(mode), classes=str(Cls.TOGGLE))

            Button("Axes", size="xs", variant="ghost", title="Toggle the orientation axes", on_click=self.callback(self.toggle_axes))
            Button("Screenshot", size="xs", variant="ghost", title="Save a 2x PNG", on_click=self.callback(self.screenshot))

        self.host = html.Div(style=VIEW_STYLE)
        with self.host:
            self.build_view()
            self.build_notice()

    def build_notice(self) -> None:
        """The notice centered over the view, shown while it holds text."""
        with react.If(self.key(StateVar.NOTICE)):
            html.Div([react.Bind(self.key(StateVar.NOTICE))], classes=NOTICE)

    def window(self) -> vtkRenderWindow:
        """The plotter's render window; RuntimeError when it has none."""
        window = self.plotter.render_window
        if window is None:
            raise RuntimeError("the plotter has no render window")

        return window

    def build_view(self) -> None:
        """Hosts the view of the current mode; each transport is built once for the panel's life, since the window registers with the mirror only once."""
        if self.mode is ViewMode.SERVER:
            self.host_server()
        else:
            self.host_client()

    def host_server(self) -> None:
        """Hosts the stream, building it on first use."""
        if self.area is None:
            self.area, self.handler = self.build_server()
            return

        if self.host is not None:
            self.host.add_child(self.area)

    def host_client(self) -> None:
        """Hosts the mirror and its companion, building them on first use."""
        if self.view is None:
            self.view, self.sync = self.build_client()
            return

        if self.host is not None:
            self.host.add_child(self.view)
            self.host.add_child(self.sync)

    def build_server(self) -> tuple[rca.RemoteControlledArea, InteractiveCap]:
        """A jpeg stream of the server's frames: capped while interacting, a 2x still at STILL_QUALITY after."""
        name = self.key("view")
        area = rca.RemoteControlledArea(
            name=name,
            display="image",
            send_mouse_move=True,
            monitor=1,
            style=FILL_STYLE,
            image_style=IMAGE_STYLE,
            on_stats=self.callback(self.on_stats, args="[$event]"),
        )
        # trame-rca declares the event under its Vue name only.
        area._event_names.append(("on_stats", "onStats"))
        window = window_wrapper(self.window())
        scheduler = RcaImageRenderScheduler(
            window,
            target_fps=TARGET_FPS,
            interactive_quality=INTERACTIVE_QUALITY,
            still_quality=STILL_QUALITY,
            rca_encoder=ENCODER,
        )
        handler = InteractiveCap(window, name, scheduler=scheduler, sized=self.on_stream_size)
        handler.scale = STILL_SCALE
        area.add_view_handler(handler)

        return area, handler

    def build_client(self) -> tuple[vtklocal.LocalView, SceneSync]:
        """A VTK.wasm mirror of the render window, interacting locally, with the triad turned in the browser."""
        tolerate_dropped_objects()
        view = vtklocal.LocalView(
            self.window(),
            throttle_rate=THROTTLE_RATE,
            cache_size=CACHE_BYTES,
            config=WASM_CONFIG,
            style=FILL_STYLE,
            on_camera=self.callback(self.on_camera, args="[$event]"),
            on_progress=self.callback(self.on_progress, args="[$event]"),
        )
        self.camera_id = view.get_wasm_id(self.plotter.renderer.GetActiveCamera())
        sync = SceneSync(
            view_ref=view.ref_name,
            camera_id=self.camera_id,
            matrix_id=view.get_wasm_id(self.triad.matrix),
            on_resize=self.callback(self.on_client_size, args="[$event]"),
        )

        return view, sync

    def set_mode(self, value: object) -> None:
        """The toolbar's choice: persisted, then applied; the empty value of a deselected toggle is ignored."""
        if value not in ViewMode:
            return

        self.take(ViewMode(value), persist=True)

    def take(self, mode: ViewMode, *, persist: bool) -> None:
        """Switches, taking the page's one mirror from any older panel first, so no two panels ever mirror."""
        if mode is ViewMode.CLIENT:
            self.claim_mirror(self.app)

        self.switch(mode, persist=persist)

    def switch(self, mode: ViewMode, *, persist: bool) -> None:
        """Re-hosts the other transport's view and redraws the scene for it (surface or volume, font scale), the camera kept."""
        if mode is self.mode:
            return

        self.adopt_camera()
        self.mode = mode
        self.scale_fonts(STILL_SCALE if mode is ViewMode.SERVER else 1)
        self.match_canvas()
        with self.state:
            self.state[self.key(StateVar.MODE)] = str(mode)

        if persist:
            self.app.persist.save(self.app.scope, self.store_key, {str(StoreKey.MODE): str(mode)})

        drawn = self.draw(self.renderable, keep_camera=True) if self.renderable is not None else None
        if drawn is not None and self.oversized(drawn.footprint):
            self.fall_back(drawn.footprint)
            return

        self.show_axes(self.axes_shown)
        self.rehost()
        self.push()
        log.bind(panel=self.id, mode=str(mode), persisted=persist).info("View mode set.")

    def rehost(self) -> None:
        """
        Replaces the hosted view with the current mode's and pushes the template.

        The other transport is unmounted, not hidden: a `vtk-local` mounted into a `display: none` host has no canvas to make its render window from and fails to start.
        """
        if self.host is None:
            return

        self.host.clear()
        with self.host:
            self.build_view()
            self.build_notice()

        self.refresh()

    def render(self, renderable: Renderable, *, keep_camera: bool = True) -> None:
        """Draws the renderable; the camera survives when asked and it still frames the new bounds, else the view is re-fitted."""
        self.renderable = renderable
        drawn = self.draw(renderable, keep_camera=keep_camera)
        if self.oversized(drawn.footprint):
            self.fall_back(drawn.footprint)
            return

        self.push()

    def draw(self, renderable: Renderable, *, keep_camera: bool) -> Drawn:
        """Renders into the plotter for the current mode: the mirror gets surfaces with point scalars, the stream the scene as drawn."""
        self.adopt_camera()
        camera = self.plotter.camera.copy()

        renderable.render(self.plotter)

        keep = keep_camera and not camera_needs_reset(camera.position, camera.focal_point, self.plotter.bounds)
        if keep:
            self.restore_camera(camera)
        else:
            self.plotter.renderer.ResetCamera()

        if self.mode is not ViewMode.CLIENT:
            self.footprint = None
            return Drawn(keep, None)

        started = time.perf_counter()
        self.footprint = mirror_friendly(self.plotter)
        seconds = time.perf_counter() - started
        log.bind(
            panel=self.id,
            cells=self.footprint.cells,
            megabytes=round(self.footprint.megabytes, 1),
            seconds=round(seconds, 3),
        ).info("Scene prepared for the mirror.")
        self.status_line(f"{self.footprint.cells:,} cells, {self.footprint.megabytes:.1f} MB")

        return Drawn(keep, self.footprint)

    @staticmethod
    def oversized(footprint: Footprint | None) -> bool:
        """Whether the mirror would fetch more than CLIENT_LIMIT_MB."""
        return footprint is not None and footprint.megabytes > CLIENT_LIMIT_MB

    def fall_back(self, footprint: Footprint | None) -> None:
        """Streams a scene the mirror could not hold, without persisting the choice."""
        megabytes = footprint.megabytes if footprint is not None else 0.0
        log.bind(panel=self.id, megabytes=round(megabytes, 1), limit=CLIENT_LIMIT_MB).warning("Scene too large for the mirror; streaming.")
        self.status(f"{megabytes:.0f} MB exceeds the mirror's {CLIENT_LIMIT_MB} MB; streaming instead")
        self.switch(ViewMode.SERVER, persist=False)

    def push(self) -> None:
        """Sends the current scene: a re-render for the stream, an object sync for the mirror."""
        self.adopt_camera()
        self.triad.align(self.plotter.renderer.GetActiveCamera())
        if self.mode is ViewMode.SERVER and self.handler is not None:
            self.handler.schedule_render()
            return

        if self.mode is ViewMode.CLIENT and self.view is not None:
            # A renderable may add actors after the draw, as the field viewer adds its lines.
            self.footprint = mirror_friendly(self.plotter)
            refresh_composites(self.plotter)
            self.view.update(push_camera=True)

    def refit(self) -> None:
        """Fits the camera to the model, against the window the viewer actually sees."""
        self.plotter.renderer.ResetCamera()
        self.push()

    def look(self, view: View) -> None:
        """Puts the camera on the preset's side of the focal point with its up vector upright, then re-fits the model."""
        self.adopt_camera()
        side = SIDES.get(view)
        if side is not None:
            direction, up = side
            camera = self.plotter.camera
            focal = camera.focal_point
            camera.position = (focal[0] + direction[0], focal[1] + direction[1], focal[2] + direction[2])
            camera.up = up

        self.refit()

    def fit(self) -> None:
        """Fits the camera to the model from its current side."""
        self.look(View.FIT)

    def restore_camera(self, saved: pv.Camera) -> None:
        """Copies a saved camera into the live one; assigning one to the plotter would make a different object active, which the mirror registers under an id the client never saw."""
        self.plotter.renderer.GetActiveCamera().DeepCopy(saved)

    def adopt_camera(self) -> None:
        """Makes the camera the mirror last reported the plotter's, so a redraw, a still or a successor starts from what the user saw."""
        reported = self.last_camera
        if reported is None:
            return

        self.last_camera = None
        camera = self.plotter.camera
        camera.position = vector(reported[CameraKey.POSITION])
        camera.focal_point = vector(reported[CameraKey.FOCAL_POINT])
        camera.up = vector(reported[CameraKey.VIEW_UP])
        camera.view_angle = float(cast("float", reported[CameraKey.VIEW_ANGLE]))
        camera.parallel_scale = float(cast("float", reported[CameraKey.PARALLEL_SCALE]))

    def on_camera(self, payload: object) -> None:
        """Keeps the main camera's reports; the triad's camera reports through the same event and is ignored, and so does a report in flight when the panel began to stream."""
        if not isinstance(payload, Mapping) or self.mode is not ViewMode.CLIENT:
            return

        state = cast("Mapping[str, object]", payload)
        if state.get(CameraKey.ID) != self.camera_id:
            return

        self.last_camera = state

    def on_client_size(self, payload: object) -> None:
        """
        Takes the mirror's canvas as the off-screen window, so a fit frames what the browser shows.

        Nothing else sizes that window in CLIENT mode, and VTK.wasm's own reset ignores an aspect below one, so the first size also re-fits; later ones keep the camera, as a
        resized stream does.
        """
        if not isinstance(payload, Mapping):
            return

        size = cast("Mapping[str, object]", payload)
        width = int(cast("float", size.get(SizeKey.WIDTH, 0)))
        height = int(cast("float", size.get(SizeKey.HEIGHT, 0)))
        if width < 1 or height < 1 or (width, height) == self.canvas:
            return

        self.canvas = (width, height)
        self.pixel_ratio = float(cast("float", size.get(SizeKey.RATIO, 1.0)))
        self.match_canvas()
        log.bind(panel=self.id, width=width, height=height, ratio=self.pixel_ratio, framed=self.framed).info("Mirror canvas sized.")
        if self.framed:
            self.push()
            return

        self.framed = True
        self.refit()

    def on_stream_size(self) -> None:
        """The stream sizes the window itself; the first size still has to frame the scene, drawn before any viewer had one."""
        if self.framed:
            return

        self.framed = True
        self.refit()

    def match_canvas(self) -> None:
        """Sizes the off-screen window like the mirror's canvas; the stream sizes it itself."""
        if self.mode is ViewMode.CLIENT and self.canvas is not None:
            self.plotter.window_size = list(self.canvas)

    def on_progress(self, payload: object) -> None:
        """Logs a completed sync with what it carried; a sync still in flight when the panel began to stream would report an empty scene over the stream's own line."""
        if not isinstance(payload, Mapping) or self.mode is not ViewMode.CLIENT:
            return

        progress = cast("Mapping[str, object]", payload)
        if progress.get(ProgressKey.ACTIVE) is not False:
            return

        elapsed = float(cast("float", progress.get(ProgressKey.ELAPSED, 0.0)))
        footprint = self.footprint if self.footprint is not None else Footprint(0, 0.0)
        log.bind(
            panel=self.id,
            cells=footprint.cells,
            megabytes=round(footprint.megabytes, 1),
            seconds=round(elapsed / 1000.0, 3),
        ).info("Scene synced.")
        self.status_line(f"{footprint.cells:,} cells, {footprint.megabytes:.1f} MB, synced in {elapsed / 1000.0:.2f} s")

    def on_stats(self, payload: object) -> None:
        """Shows the stream's delivered frame rate and bandwidth."""
        if not isinstance(payload, Mapping):
            return

        stats = cast("Mapping[str, object]", payload)
        fps = cast("float", stats.get(StatsKey.FPS, 0))
        bps = cast("float", stats.get(StatsKey.BPS, 0))
        self.status_line(f"{fps:.0f} fps, {bps / 1000.0:.0f} KB/s")

    def status_line(self, text: str) -> None:
        """The toolbar's caption, flushed so it also lands from outside a handler."""
        with self.state:
            self.state[self.key(StateVar.STATUS)] = text

    def notice(self, text: str) -> None:
        """Centers `text` over the view, for a view with nothing to show; empty text removes it."""
        with self.state:
            self.state[self.key(StateVar.NOTICE)] = text

    def toggle_axes(self) -> None:
        """Shows or hides the orientation axes."""
        self.axes_shown = not self.axes_shown
        self.show_axes(self.axes_shown)
        self.push()

    def show_axes(self, shown: bool) -> None:
        """The mirror's triad or the stream's orientation marker, whose labels take the palette text colour (pyvista would default them to the global theme, black)."""
        self.triad.show(shown and self.mode is ViewMode.CLIENT)
        renderer = self.plotter.renderer
        streamed = shown and self.mode is ViewMode.SERVER
        if streamed and not renderer.axes_enabled:
            renderer.add_axes(color=hex_color(self.app.palette.text), line_width=AXES_LINE_WIDTH)  # pyright: ignore[reportUnknownMemberType]  # pyvista's add_axes parameters are untyped
        elif not streamed and renderer.axes_enabled:
            renderer.hide_axes()

    def window_scale(self) -> float:
        """How many window pixels the panel already has per CSS pixel: the stream renders at STILL_SCALE, the mirror at the device ratio."""
        return STILL_SCALE if self.mode is ViewMode.SERVER else self.pixel_ratio

    def screenshot(self) -> None:
        """Saves a PNG of SCREENSHOT_SCALE times the panel, whatever the window is already scaled by, under the user's pictures directory and names it in the status line."""
        self.adopt_camera()
        self.triad.align(self.plotter.renderer.GetActiveCamera())
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = Path(platformdirs.user_pictures_dir()) / APP_NAME / f"{self.id}-{stamp}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        scale = max(1, round(SCREENSHOT_SCALE / self.window_scale()))

        _ = self.plotter.screenshot(path, scale=scale)

        log.bind(path=str(path)).info("Screenshot saved.")
        self.status(f"screenshot {path}")

    def on_visible(self, visible: bool) -> None:
        """Records that the panel has been on screen, which a successor inherits."""
        self.shown = self.shown or visible

    def release_views(self) -> None:
        """Takes the stream off the server and the window out of the mirror's object manager."""
        if self.handler is not None:
            self.handler.cancel_lift()
            unregister = self.ctrl["rc_area_unregister"]
            if unregister.exists():
                _ = unregister(self.handler.area_name)

            if self.app.loop is not None:
                # Held until it finishes: a task only weakly referenced can be collected mid-close.
                self.closing = self.app.loop.create_task(self.handler.close())

        if self.view is not None:
            _ = self.view.object_manager.UnRegisterObject(self.view.get_wasm_id(self.window()))

    def dispose(self) -> None:
        """Frees the render window with the panel, leaving the camera the user saw for a successor."""
        self.adopt_camera()
        self.legacy[Carry.CAMERA] = self.plotter.camera.copy()
        self.legacy[Carry.SHOWN] = self.shown
        self.legacy[Carry.MODE] = str(self.mode)
        self.release_views()
        self.plotter.close()
        super().dispose()

    def succeed(self, previous: PanelComponent) -> None:
        """Keeps the closed panel's camera and transport, so a rebuild does not undo a fallback the store never saw; the classes are never compared, since a reload replaces them."""
        carried = previous.legacy.get(Carry.MODE)
        if carried in ViewMode:
            self.take(ViewMode(cast("str", carried)), persist=False)

        camera = previous.legacy.get(Carry.CAMERA)
        if isinstance(camera, pv.Camera) and not camera_needs_reset(camera.position, camera.focal_point, self.plotter.bounds):
            self.restore_camera(camera)
            self.push()

        self.shown = bool(previous.legacy.get(Carry.SHOWN))


class SceneViewerPanel(ScenePanel):
    """One renderable at a time: `show` hands its target to a Reloader, `reload` rebuilds it now, `stop` ends the watch."""

    def __init__(self, app: "Workbench", id: str, title: str, *, watch: Sequence[Path] = (), mode: ViewMode = ViewMode.CLIENT) -> None:
        """Builds the panel empty; `show` hands it a renderable card.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
            watch: More directories whose .py saves rebuild the shown renderable, beside its card's roots.
            mode: Where the frames render while the store keeps no choice for the panel.
        """
        self.watch = [Path(directory) for directory in watch]
        self.spec: WorkbenchSpec | None = None
        self.reloader: Reloader[Renderable] | None = None
        self.fresh = True

        super().__init__(app, id, title, mode=mode)

    def show(self, spec: WorkbenchSpec) -> None:
        """Builds the spec's Renderable on the reloader's thread now and again after every save under its roots."""
        self.stop()
        self.spec = spec
        self.fresh = True
        roots = [*default_roots(spec.module), *self.watch]

        def apply(result: Renderable, seconds: float) -> None:
            self.render(result, keep_camera=not self.fresh)
            self.fresh = False
            self.status(f"{spec.title} rendered in {seconds:.1f} s")

        def failed(error: Exception) -> None:
            self.status(f"{type(error).__name__}: {error}")

        self.reloader = Reloader(spec.target, roots, apply, kind=Renderable, marshal=self.app.marshal, failed=failed)
        self.reloader.start()
        self.reloader.request()
        self.retitle(spec.title)
        log.bind(panel=self.id, workbench=spec.name).info("Scene shown.")

    def reload(self) -> None:
        """Purges and rebuilds the shown renderable now."""
        if self.reloader is None:
            return

        self.reloader.request()

    def stop(self) -> None:
        """Stops rebuilding the shown renderable."""
        if self.reloader is None:
            return

        self.reloader.stop()
        self.reloader = None

    def dispose(self) -> None:
        """Stops the reloader, then frees the view."""
        self.stop()
        super().dispose()

    def retitle(self, subject: str) -> None:
        """Names the dock tab after what it shows."""
        if self.app.dock is not None:
            self.app.dock.set_panel_title(self.id, f"{self.title}{TITLE_SEPARATOR}{subject}")
