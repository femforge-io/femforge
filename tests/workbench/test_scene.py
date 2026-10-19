"""The scene panel: the per-panel view mode and its persistence, the mirror pass and its size guard, the interactive cap, the camera round trip, the axes and the screenshot."""

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from femforge_workbench.persist import LocalJsonStore
from femforge_workbench.theme import hex_color
from tests.workbench.tree import find, prop, texts


pytest.importorskip("trame")
pytest.importorskip("pyvista")

import platformdirs  # noqa: E402
import pyvista as pv  # noqa: E402
from numpy.typing import NDArray  # noqa: E402
from PIL import Image  # noqa: E402
from trame.app import get_server  # noqa: E402
from trame_rca.schedulers import RcaImageRenderScheduler  # noqa: E402
from trame_vtklocal.module.protocol import ObjectManagerAPI  # noqa: E402
from vtkmodules.vtkCommonCore import vtkDataArray, vtkIdTypeArray  # noqa: E402
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkMultiBlockDataSet, vtkPolyData, vtkUnstructuredGrid  # noqa: E402
from vtkmodules.vtkRenderingCore import vtkActor, vtkDataSetMapper, vtkPolyDataMapper  # noqa: E402
from vtkmodules.vtkSerializationManager import vtkObjectManager  # noqa: E402

from femforge_workbench.app import StateVar as AppVar, Workbench  # noqa: E402
from femforge_workbench.dock import Panel as DockPanel, Split  # noqa: E402
from femforge_workbench.panels import Carry  # noqa: E402
from femforge_workbench.panels import scene as scene_module  # noqa: E402
from femforge_workbench.panels.scene import (  # noqa: E402
    IMAGE_STYLE,
    INTERACTIVE_PIXELS,
    INTERACTIVE_QUALITY,
    SCREENSHOT_SCALE,
    STILL_QUALITY,
    STILL_SCALE,
    STORE_PREFIX,
    CameraKey,
    InteractiveCap,
    ScenePanel,
    StateVar,
    StoreKey,
    View,
    ViewMode,
    empty_status,
    mirror_friendly,
    tolerate_dropped_objects,
    view_rotation,
)


LIFT = 0.05
PANEL_SIZE = (320, 480)
"""A portrait panel in device pixels: the aspect a mirror must fit and a screenshot must keep."""
SCALAR = "scalar"
GRID_DIMENSIONS = (4, 4, 4)
SURFACE_CELLS = 54
"""The quads on the skin of a 3x3x3-cell box."""
LINE_PATH = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 1.0]]
LINE_CLIP = (-1.0, 0.9, -1.0, 2.0, -1.0, 2.0)
LINE_CELLS = 2
"""The segments of LINE_PATH left inside LINE_CLIP."""
STALE_ID = 999_999
"""An object id the mirror's manager never had, like one of an actor the scene has dropped."""


def _shell(name: str, root: Path) -> Workbench:
    return Workbench(name, title="Scene", server=get_server(f"scene-{name}", client_type="react"), persist=LocalJsonStore(root), user="tester")


def _scene(app: Workbench, id: str, title: str) -> ScenePanel:
    return ScenePanel(app, id, title)


def _grid() -> pv.UnstructuredGrid:
    grid = pv.ImageData(dimensions=GRID_DIMENSIONS).cast_to_unstructured_grid()
    grid.cell_data[SCALAR] = np.arange(grid.n_cells, dtype=float)

    return grid


class Volume:
    """A Renderable: a small volume grid with cell scalars beside a sphere."""

    def render(self, plotter: pv.Plotter) -> None:
        plotter.clear()
        _ = plotter.add_mesh(_grid(), scalars=SCALAR, cmap="viridis")  # pyright: ignore[reportUnknownMemberType]  # add_mesh is untyped
        _ = plotter.add_mesh(pv.Sphere(center=(5.0, 0.0, 0.0)), color="white")  # pyright: ignore[reportUnknownMemberType]
        plotter.renderer.ResetCamera()


def _reported(camera_id: int, position: tuple[float, float, float]) -> dict[str, object]:
    """A camera state as the mirror reports it."""
    return {
        str(CameraKey.ID): camera_id,
        str(CameraKey.POSITION): list(position),
        str(CameraKey.FOCAL_POINT): [1.5, 1.5, 1.5],
        str(CameraKey.VIEW_UP): [0.0, 0.0, 1.0],
        str(CameraKey.VIEW_ANGLE): 30.0,
        str(CameraKey.PARALLEL_SCALE): 3.0,
    }


def _template(app: Workbench, panel: ScenePanel) -> dict[str, object]:
    return cast("dict[str, object]", app.state[f"trame__template_{panel.template_name}"])


def _volume_mapper(panel: ScenePanel) -> vtkDataSetMapper:
    """The mapper of the grid actor, whatever it holds now."""
    mappers = [cast("vtkActor", actor).GetMapper() for actor in panel.plotter.renderer.actors.values() if isinstance(actor, vtkActor)]
    grids = [mapper for mapper in mappers if isinstance(mapper, vtkDataSetMapper) and mapper.GetArrayName() == SCALAR]
    assert len(grids) == 1

    return grids[0]


def test_the_mode_defaults_to_the_mirror_and_a_persisted_choice_wins(tmp_path: Path) -> None:
    app = _shell("modes", tmp_path)
    app.persist.save(app.scope, f"{STORE_PREFIX}saved", {str(StoreKey.MODE): str(ViewMode.SERVER)})

    mirrored = ScenePanel(app, "fresh", "Fresh")
    streamed = ScenePanel(app, "explicit", "Explicit", mode=ViewMode.SERVER)
    saved = ScenePanel(app, "saved", "Saved")

    assert (mirrored.mode, streamed.mode, saved.mode) == (ViewMode.CLIENT, ViewMode.SERVER, ViewMode.SERVER)
    assert app.state[mirrored.key(StateVar.MODE)] == "client" and app.state[saved.key(StateVar.MODE)] == "server"
    tree = _template(app, mirrored)
    assert len(find(tree, "vtk-local")) == 1 and find(tree, "remote-controlled-area") == []
    sync = find(tree, "ff-scene-sync")
    assert mirrored.view is not None and len(sync) == 1
    assert prop(sync[0], "viewRef") == mirrored.view.ref_name
    assert prop(sync[0], "cameraId") == mirrored.camera_id > 0 and cast("int", prop(sync[0], "matrixId")) > 0
    toggle = find(tree, "ff-toggle-group")
    assert len(toggle) == 1 and prop(toggle[0], "value") == {"js": mirrored.key(StateVar.MODE)} and prop(toggle[0], "type") == "single"
    assert [texts(item) for item in find(tree, "ff-toggle-group-item")] == [["Client"], ["Server"]]
    assert len(find(_template(app, streamed), "remote-controlled-area")) == 1 and find(_template(app, streamed), "vtk-local") == []


def test_set_mode_persists_re_hosts_the_other_view_and_keeps_the_camera_the_mirror_reported(tmp_path: Path) -> None:
    app = _shell("switch", tmp_path)
    panel = ScenePanel(app, "scene", "Scene", renderable=Volume())
    assert isinstance(_volume_mapper(panel).GetInput(), vtkPolyData), "the mirror gets the skin"  # pyright: ignore[reportUnknownMemberType]  # vtkMapper.GetInput is untyped in the VTK stubs
    view = panel.view
    assert view is not None
    base = pv.global_theme.font.size

    panel.on_camera(_reported(panel.camera_id + 1, (9.0, 9.0, 9.0)))
    panel.on_camera(_reported(panel.camera_id, (5.0, 5.0, 5.0)))
    panel.set_mode("")
    assert panel.mode is ViewMode.CLIENT, "a deselected toggle changes nothing"

    panel.set_mode("server")

    assert panel.mode is ViewMode.SERVER and app.state[panel.key(StateVar.MODE)] == "server"
    assert app.persist.load(app.scope, panel.store_key) == {"mode": "server"}
    assert panel.plotter.camera.position == pytest.approx((5.0, 5.0, 5.0)) and panel.plotter.camera.parallel_scale == pytest.approx(3.0), "the main camera's report is adopted, the triad's ignored"
    assert isinstance(_volume_mapper(panel).GetInput(), vtkUnstructuredGrid), "the stream renders the volume again"  # pyright: ignore[reportUnknownMemberType]
    assert panel.plotter.theme.font.size == base * STILL_SCALE
    tree = _template(app, panel)
    area = find(tree, "remote-controlled-area")
    assert len(area) == 1 and find(tree, "vtk-local") == []
    assert prop(area[0], "imageStyle") == dict(IMAGE_STYLE), "the frame is fitted, so its aspect never drives the host height"
    assert panel.handler is not None and panel.handler.scale == STILL_SCALE

    panel.set_mode("client")

    assert panel.mode is ViewMode.CLIENT and panel.view is view, "the window registers with the mirror once, so the view is never rebuilt"
    assert isinstance(_volume_mapper(panel).GetInput(), vtkPolyData) and panel.plotter.theme.font.size == base  # pyright: ignore[reportUnknownMemberType]
    tree = _template(app, panel)
    assert len(find(tree, "vtk-local")) == 1 and len(find(tree, "ff-scene-sync")) == 1 and find(tree, "remote-controlled-area") == []
    assert panel.camera_id == view.get_wasm_id(panel.plotter.renderer.GetActiveCamera()), "two switches later the mirror's camera is still the one the client was told about"
    assert app.persist.load(app.scope, panel.store_key) == {"mode": "client"}


def test_the_mirror_reports_its_canvas_so_the_window_and_the_fit_follow_the_panel(tmp_path: Path) -> None:
    app = _shell("canvas", tmp_path)
    panel = ScenePanel(app, "scene", "Scene", renderable=Volume())
    assert panel.canvas is None
    square = panel.plotter.camera.parallel_scale

    panel.on_client_size({"width": PANEL_SIZE[0], "height": PANEL_SIZE[1], "ratio": 2})

    assert panel.canvas == PANEL_SIZE and panel.pixel_ratio == 2
    assert tuple(panel.window().GetSize()) == PANEL_SIZE, "the off-screen window is the canvas, so a fit frames what the browser shows"
    assert panel.plotter.camera.parallel_scale > square, "a portrait panel needs more of the model above and below than a square one"

    panel.look(View.TOP)
    framed = panel.plotter.camera.parallel_scale
    panel.on_client_size({"width": PANEL_SIZE[0], "height": PANEL_SIZE[1], "ratio": 2})
    assert panel.plotter.camera.parallel_scale == framed, "the same size again changes nothing"

    panel.on_client_size({"width": PANEL_SIZE[1], "height": PANEL_SIZE[0], "ratio": 2})
    assert tuple(panel.window().GetSize()) == (PANEL_SIZE[1], PANEL_SIZE[0])
    assert panel.plotter.camera.parallel_scale == framed, "a resize keeps the camera, as a resized stream does"


def test_the_first_size_the_stream_reports_frames_the_scene_too(tmp_path: Path) -> None:
    app = _shell("streamed-fit", tmp_path)
    panel = ScenePanel(app, "scene", "Scene", renderable=Volume(), mode=ViewMode.SERVER)
    assert panel.handler is not None and not panel.framed
    square = panel.plotter.camera.parallel_scale

    panel.handler.update_size("client", {"w": PANEL_SIZE[0] // STILL_SCALE, "h": PANEL_SIZE[1] // STILL_SCALE, "p": 1})

    assert panel.framed and tuple(panel.window().GetSize()) == PANEL_SIZE, "the stream renders at STILL_SCALE of the box it reports"
    assert panel.plotter.camera.parallel_scale > square, "the scene was drawn before any viewer had a size, so the first one frames it"

    framed = panel.plotter.camera.parallel_scale
    panel.handler.update_size("client", {"w": PANEL_SIZE[1] // STILL_SCALE, "h": PANEL_SIZE[0] // STILL_SCALE, "p": 1})
    assert panel.plotter.camera.parallel_scale == framed, "a later resize keeps the camera"


def test_text_scales_with_the_window_pixels_per_css_pixel_in_both_modes(tmp_path: Path) -> None:
    app = _shell("dense", tmp_path)
    base = pv.global_theme.font.size
    streamed = ScenePanel(app, "streamed", "Streamed", renderable=Volume(), mode=ViewMode.SERVER)
    assert streamed.handler is not None

    streamed.handler.update_size("client", {"w": PANEL_SIZE[0] // 4, "h": PANEL_SIZE[1] // 4, "p": 2})

    assert streamed.window_scale() == 4 and streamed.stream_ratio == 2
    assert streamed.plotter.theme.font.size == base * 4, "a 2x display doubles the still, so its text doubles too"
    assert tuple(streamed.window().GetSize()) == PANEL_SIZE

    mirrored = ScenePanel(app, "mirrored", "Mirrored", renderable=Volume())
    mirrored.on_client_size({"width": PANEL_SIZE[0], "height": PANEL_SIZE[1], "ratio": 2})

    assert mirrored.window_scale() == 2 and mirrored.plotter.theme.font.size == base * 2, "the mirror's window is in device pixels"

    mirrored.on_client_size({"width": PANEL_SIZE[0], "height": PANEL_SIZE[1], "ratio": 1})

    assert mirrored.window_scale() == 1 and mirrored.plotter.theme.font.size == base, "a new ratio at the same size rescales the text"


def test_the_newest_panel_takes_the_mirror_and_the_older_ones_stream(tmp_path: Path) -> None:
    app = _shell("single", tmp_path)

    app.add(Split([DockPanel("First", _scene, id="first"), DockPanel("Second", _scene, id="second")]))

    first = app.panel("first", ScenePanel)
    second = app.panel("second", ScenePanel)
    assert (first.mode, second.mode) == (ViewMode.SERVER, ViewMode.CLIENT), "VTK.wasm gives browser events to the last view mounted, so the newest panel mirrors"
    assert "mirrors" in str(app.state[first.key(StateVar.STATUS)])
    assert app.persist.load(app.scope, first.store_key) is None, "being streamed is not the user's choice"

    first.set_mode("client")

    assert (first.mode, second.mode) == (ViewMode.CLIENT, ViewMode.SERVER), "an explicit toggle takes the mirror in turn"
    assert app.persist.load(app.scope, first.store_key) == {"mode": "client"}

    app.retire(["second"])
    assert first.mode is ViewMode.CLIENT and second.mode is ViewMode.SERVER, "a closed panel hands nothing back; the streamed one waits for a toggle"


def test_a_rebuild_that_restores_the_mirror_takes_it_from_the_panel_holding_it(tmp_path: Path) -> None:
    app = _shell("rebuilt", tmp_path)
    app.add(Split([DockPanel("First", _scene, id="first"), DockPanel("Second", _scene, id="second")]))
    first = app.panel("first", ScenePanel)
    second = app.panel("second", ScenePanel)
    app.retire(["second"])
    first.set_mode("client")
    assert second.legacy[Carry.MODE] == "client" and first.mode is ViewMode.CLIENT

    rebuilt = ScenePanel(app, "second", "Second", mode=ViewMode.SERVER)
    rebuilt.succeed(second)

    assert (rebuilt.mode, first.mode) == (ViewMode.CLIENT, ViewMode.SERVER), "the carried mirror is claimed like any other, so a rebuild never leaves two"


def test_mirror_friendly_swaps_volume_grids_for_their_skin_with_point_scalars(tmp_path: Path) -> None:
    plotter = pv.Plotter(off_screen=True)
    sphere = pv.Sphere()  # pyright: ignore[reportUnknownMemberType]  # pyvista geometry sources are untyped
    grid_actor = plotter.add_mesh(_grid(), scalars=SCALAR, cmap="viridis")  # pyright: ignore[reportUnknownMemberType]  # add_mesh is untyped
    sphere_actor = plotter.add_mesh(sphere, color="white")  # pyright: ignore[reportUnknownMemberType]
    sphere_input = cast("object", sphere_actor.GetMapper().GetInput())  # pyright: ignore[reportUnknownMemberType]  # vtkMapper.GetInput is untyped in the VTK stubs
    # A renderable is free to build its own actor; the lab previews map polydata through a plain vtkPolyDataMapper.
    cube = pv.Cube()  # pyright: ignore[reportUnknownMemberType]
    plain = vtkPolyDataMapper()
    plain.SetInputData(cube)  # pyright: ignore[reportUnknownMemberType]  # overloaded twice in the VTK stubs without @overload
    plain_actor = vtkActor()
    plain_actor.SetMapper(plain)
    plotter.renderer.AddActor(plain_actor)

    plotter.renderer.SetUseDepthPeeling(True)
    footprint = mirror_friendly(plotter)

    assert not plotter.renderer.GetUseDepthPeeling(), "VTK.wasm has no dual depth peeling"
    mapper = grid_actor.GetMapper()
    skin = cast("object", mapper.GetInput())  # pyright: ignore[reportUnknownMemberType]
    assert isinstance(skin, vtkPolyData) and skin.GetNumberOfCells() == SURFACE_CELLS
    scalars = cast("vtkDataArray | None", skin.GetPointData().GetScalars())  # pyright: ignore[reportUnknownMemberType]
    assert scalars is not None and scalars.GetName() == SCALAR and skin.GetCellData().GetNumberOfArrays() == 0
    assert mapper.GetScalarMode() == 1, "the mapper colours by the point scalars now"
    assert cast("object", sphere_actor.GetMapper().GetInput()) is sphere_input, "polydata is left alone"  # pyright: ignore[reportUnknownMemberType]
    assert footprint.cells == SURFACE_CELLS + sphere.n_cells + cube.n_cells, "every mapper counts, not only the ones that could hold a volume"
    assert footprint.megabytes > 0

    again = mirror_friendly(plotter)
    assert again == footprint
    plotter.close()


def test_mirror_friendly_counts_the_leaves_of_a_composite_mapper() -> None:
    plotter = pv.Plotter(off_screen=True)
    sphere = pv.Sphere()  # pyright: ignore[reportUnknownMemberType]  # pyvista geometry sources are untyped
    cube = pv.Cube()  # pyright: ignore[reportUnknownMemberType]
    blocks = pv.MultiBlock([sphere, cube])
    # The field viewer draws every body through one composite actor, whose GetInput is None: without the leaves the size guard would see nothing.
    _ = plotter.add_composite(blocks)  # pyright: ignore[reportUnknownMemberType]  # add_composite is untyped

    footprint = mirror_friendly(plotter)

    assert footprint.cells == sphere.n_cells + cube.n_cells
    assert footprint.megabytes > 0
    plotter.close()


def test_actors_added_after_the_draw_reach_the_mirror_as_polydata_with_every_state_it_fetches(tmp_path: Path) -> None:
    app = _shell("late", tmp_path)
    panel = ScenePanel(app, "scene", "Scene", renderable=Volume())
    assert panel.view is not None and panel.footprint is not None
    drawn = panel.footprint.cells
    # An unstructured grid of line cells added after the draw, as the field viewer adds its lines.
    path = pv.MultipleLines(points=LINE_PATH)  # pyright: ignore[reportUnknownMemberType]  # pyvista geometry sources are untyped
    clipped = cast("pv.PolyData", path.clip_box(LINE_CLIP, invert=False))  # pyright: ignore[reportUnknownMemberType]  # clip_box is untyped
    lines = clipped.cast_to_unstructured_grid()
    line_actor = panel.plotter.add_mesh(lines)  # pyright: ignore[reportUnknownMemberType]  # add_mesh is untyped
    image_actor = panel.plotter.add_mesh(pv.ImageData(dimensions=GRID_DIMENSIONS))  # pyright: ignore[reportUnknownMemberType]

    panel.push()

    handed = cast("object", line_actor.GetMapper().GetInput())  # pyright: ignore[reportUnknownMemberType]  # vtkMapper.GetInput is untyped in the VTK stubs
    assert isinstance(handed, vtkPolyData) and handed.GetNumberOfLines() == lines.n_cells == LINE_CELLS
    assert isinstance(image_actor.GetMapper().GetInput(), vtkPolyData), "any dataset but polydata takes the same path through VTK's serializer"  # pyright: ignore[reportUnknownMemberType]
    manager = cast("vtkObjectManager", panel.view.object_manager)
    sent = manager.GetAllDependencies(panel.view.get_wasm_id(panel.window()))
    assert [obj_id for obj_id in sent if not manager.GetState(obj_id)] == [], "a dataset mapper that never rendered serializes other inputs through a filter it frees"
    assert panel.footprint.cells == drawn + LINE_CELLS + SURFACE_CELLS, "the size the mirror reports counts what came after the draw"


def test_the_mirror_gets_cell_arrays_its_32_bit_runtime_reads(tmp_path: Path) -> None:
    app = _shell("narrow", tmp_path)
    panel = ScenePanel(app, "scene", "Scene", renderable=Volume())
    assert panel.view is not None

    def id_type_lines() -> pv.PolyData:
        """LINE_PATH as line cells in vtkIdTypeArray storage, as VTK's filters leave them."""
        offsets, connectivity = vtkIdTypeArray(), vtkIdTypeArray()
        segments = len(LINE_PATH) - 1
        for cell in range(segments + 1):
            _ = offsets.InsertNextValue(2 * cell)

        for cell in range(segments):
            _ = connectivity.InsertNextValue(cell)
            _ = connectivity.InsertNextValue(cell + 1)

        cells = vtkCellArray()
        cells.SetData(offsets, connectivity)  # pyright: ignore[reportUnknownMemberType]  # overloaded twice in the VTK stubs without @overload
        lines = pv.PolyData()
        lines.points = np.array(LINE_PATH)
        lines.SetLines(cells)

        return lines

    lines = id_type_lines()
    assert lines.GetLines().GetConnectivityArray().GetClassName() == "vtkIdTypeArray"  # pyright: ignore[reportUnknownMemberType]  # GetConnectivityArray is untyped in the VTK stubs
    _ = panel.plotter.add_mesh(lines)  # pyright: ignore[reportUnknownMemberType]  # add_mesh is untyped

    panel.push()

    manager = cast("vtkObjectManager", panel.view.object_manager)
    sent = [cast("dict[str, object]", json.loads(manager.GetState(obj_id))) for obj_id in manager.GetAllDependencies(panel.view.get_wasm_id(panel.window()))]
    arrays = [cast("dict[str, int]", state[key])["Id"] for state in sent if state["ClassName"] == "vtkCellArray" for key in ("Offsets", "Connectivity") if key in state]
    classes = {cast("dict[str, str]", json.loads(manager.GetState(array)))["ClassName"] for array in arrays}
    assert arrays and "vtkIdTypeArray" not in classes, "the wasm32 runtime reads a vtkIdTypeArray with its own 32-bit vtkIdType and draws every line to point 0"
    assert not lines.GetLines().IsStorage64Bit() and lines.n_lines == len(LINE_PATH) - 1


def test_a_changed_block_visibility_reaches_the_mirror_through_a_new_container_over_the_same_blocks(tmp_path: Path) -> None:
    class Bodies:
        """A Renderable: two bodies under one composite actor, as the field scene draws them."""

        def render(self, plotter: pv.Plotter) -> None:
            plotter.clear()
            _ = plotter.add_mesh(pv.MultiBlock([pv.Sphere(), pv.Cube(center=(3.0, 0.0, 0.0))]))  # pyright: ignore[reportUnknownMemberType]  # add_mesh and the geometry sources are untyped

    app = _shell("blocks", tmp_path)
    panel = ScenePanel(app, "scene", "Scene", renderable=Bodies())
    mappers = [cast("vtkActor", actor).GetMapper() for actor in panel.plotter.renderer.actors.values() if isinstance(actor, vtkActor)]
    mapper = next(found for found in mappers if isinstance(found, pv.CompositePolyDataMapper))
    drawn = cast("vtkMultiBlockDataSet", mapper.GetInputDataObject(0, 0))  # pyright: ignore[reportUnknownMemberType]  # GetInputDataObject is declared twice in the VTK stubs without @overload

    panel.push()
    assert mapper.GetInputDataObject(0, 0) is drawn, "unchanged attributes keep the container"  # pyright: ignore[reportUnknownMemberType]

    mapper.block_attr[2].visible = False
    panel.push()

    fresh = cast("vtkMultiBlockDataSet", mapper.GetInputDataObject(0, 0))  # pyright: ignore[reportUnknownMemberType]
    assert fresh is not drawn, "VTK.wasm 9.7 applies a block's visibility only when its mapper's input changes"
    assert [fresh.GetBlock(index) for index in range(2)] == [drawn.GetBlock(index) for index in range(2)], "the blocks the attributes are keyed by stay the same objects"
    panel.push()
    assert mapper.GetInputDataObject(0, 0) is fresh  # pyright: ignore[reportUnknownMemberType]


def test_the_mirror_is_answered_for_a_window_the_panel_released() -> None:
    api = ObjectManagerAPI(addon_serdes_registrars=[])
    uris = ObjectManagerAPI.get_status.__dict__.get("_wslinkuris")

    tolerate_dropped_objects()
    tolerate_dropped_objects()

    assert api.get_status(STALE_ID) == empty_status(), "trame-vtklocal would raise UnboundLocalError for a released window"
    answer = cast("dict[str, object]", api.get_status(STALE_ID))
    answer["ids"] = [STALE_ID]  # wslink serializes the answer it is handed, so a mutated one must not reach the next request
    assert api.get_status(STALE_ID) == empty_status(), "every answer is its own dict"
    assert ObjectManagerAPI.get_status.__dict__.get("_wslinkuris") == uris, "wslink still registers the RPC"
    stale: dict[str, object] = {"states": [], "hashes": {}, "next": False, "skip": True}
    assert api.get_batch([STALE_ID], []) == stale, "VTK 9.7 states a freed id as null, which trame-vtklocal skips by itself"


def test_a_scene_too_large_for_the_mirror_streams_without_persisting_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scene_module, "CLIENT_LIMIT_MB", 0)
    app = _shell("guard", tmp_path)

    panel = ScenePanel(app, "big", "Big", renderable=Volume())

    assert panel.mode is ViewMode.SERVER and app.state[panel.key(StateVar.MODE)] == "server"
    assert "MB" in str(app.state[AppVar.STATUS]) and "streaming" in str(app.state[AppVar.STATUS])
    assert app.persist.load(app.scope, panel.store_key) is None
    assert isinstance(_volume_mapper(panel).GetInput(), vtkUnstructuredGrid)  # pyright: ignore[reportUnknownMemberType]  # vtkMapper.GetInput is untyped in the VTK stubs
    tree = _template(app, panel)
    assert len(find(tree, "remote-controlled-area")) == 1 and find(tree, "vtk-local") == []

    panel.set_mode("client")
    assert panel.mode is ViewMode.SERVER, "a switch back to the mirror falls back again"
    assert app.persist.load(app.scope, panel.store_key) == {"mode": "client"}, "the user's choice is kept for a scene that fits later"


class _Window:
    """A streamed area recording the sizes and events the adapter hands it."""

    def __init__(self) -> None:
        self.sizes: list[tuple[int, int]] = []
        self.events: list[str] = []

    @property
    def img_cols_rows(self) -> tuple[NDArray[np.uint8], int, int]:
        return np.zeros((2, 2, 3), dtype=np.uint8), 2, 2

    def process_resize_event(self, width: int, height: int) -> None:
        self.sizes.append((width, height))

    def process_interaction_event(self, event: Mapping[str, object]) -> None:
        self.events.append(str(event["type"]))


class _Scheduler(RcaImageRenderScheduler):
    """Records the quality pairs the adapter sets."""

    def __init__(self, window: _Window) -> None:
        super().__init__(window, target_fps=1000, interactive_quality=INTERACTIVE_QUALITY, still_quality=STILL_QUALITY, rca_encoder="jpeg")
        self.qualities: list[tuple[int, int]] = []

    def update_quality(self, interactive: int, still: int) -> None:
        self.qualities.append((interactive, still))
        super().update_quality(interactive, still)


def test_the_interactive_cap_shrinks_frames_while_a_button_is_held_and_restores_the_2x_still_after_the_release() -> None:
    async def run() -> None:
        window = _Window()
        scheduler = _Scheduler(window)
        cap = InteractiveCap(window, "area", scheduler=scheduler, delay=LIFT)
        cap.scale = STILL_SCALE
        cap.update_size("client", {"w": 3000, "h": 2000, "p": 1})
        full = window.sizes[-1]
        assert full == (6000, 4000)

        cap.on_interaction("client", {"type": "LeftButtonPress"})
        capped = window.sizes[-1]
        assert capped[0] * capped[1] <= INTERACTIVE_PIXELS and capped[0] / capped[1] == pytest.approx(1.5, rel=0.01)
        assert cap.capped and cap.max_pixel_count == INTERACTIVE_PIXELS

        cap.on_interaction("client", {"type": "MouseMove"})
        cap.on_interaction("client", {"type": "LeftButtonRelease"})
        await asyncio.sleep(LIFT / 2)
        assert window.sizes[-1] == capped, "the lift waits for the delay"

        cap.on_interaction("client", {"type": "LeftButtonPress"})
        await asyncio.sleep(LIFT * 2)
        assert window.sizes[-1] == capped and cap.capped, "a press within the delay cancels the lift"

        cap.on_interaction("client", {"type": "LeftButtonRelease"})
        await asyncio.sleep(LIFT * 3)
        assert window.sizes[-1] == full and not cap.capped and cap.max_pixel_count == 0
        assert scheduler.qualities[-1] == (STILL_QUALITY, STILL_QUALITY), "equal qualities make the scheduler render one full-size still"

        cap.on_interaction("client", {"type": "StartMouseWheel"})
        assert cap.capped and scheduler.qualities[-1] == (INTERACTIVE_QUALITY, STILL_QUALITY)
        cap.on_interaction("client", {"type": "EndMouseWheel"})
        await asyncio.sleep(LIFT * 3)
        assert not cap.capped
        assert window.events == ["LeftButtonPress", "MouseMove", "LeftButtonRelease", "LeftButtonPress", "LeftButtonRelease", "StartMouseWheel", "EndMouseWheel"]

        await cap.close()

    asyncio.run(run())


def test_a_release_the_adapter_discards_leaves_the_cap_on_and_a_closed_stream_drops_its_lift() -> None:
    async def run() -> None:
        window = _Window()
        cap = InteractiveCap(window, "area", scheduler=_Scheduler(window), delay=LIFT)
        cap.update_size("client", {"w": 1000, "h": 1000, "p": 1})

        cap.on_interaction("client", {"type": "LeftButtonPress"})
        cap.on_interaction("client", {"type": "RightButtonRelease"})
        await asyncio.sleep(LIFT * 3)

        assert cap.capped and cap.lift_handle is None, "the base adapter discards a release it saw no press for, and so does the cap"
        assert window.events == ["LeftButtonPress"]

        cap.on_interaction("client", {"type": "LeftButtonRelease"})
        assert cap.lift_handle is not None
        cap.cancel_lift()
        await asyncio.sleep(LIFT * 3)

        assert cap.capped, "a cancelled lift never resizes the window the panel closed"
        await cap.close()

    asyncio.run(run())


def test_closing_a_streaming_panel_within_the_lift_delay_cancels_it(tmp_path: Path) -> None:
    async def run() -> None:
        app = _shell("closing", tmp_path)
        panel = ScenePanel(app, "scene", "Scene", mode=ViewMode.SERVER)
        handler = panel.handler
        assert handler is not None
        handler.delay = LIFT
        handler.update_size("client", {"w": 200, "h": 200, "p": 1})
        handler.on_interaction("client", {"type": "LeftButtonPress"})
        handler.on_interaction("client", {"type": "LeftButtonRelease"})
        assert handler.lift_handle is not None

        panel.dispose()

        assert handler.lift_handle is None
        await asyncio.sleep(LIFT * 3)

    asyncio.run(run())


def test_the_camera_the_mirror_reported_travels_through_legacy_to_a_successor(tmp_path: Path) -> None:
    app = _shell("legacy", tmp_path)
    panel = ScenePanel(app, "scene", "Scene", renderable=Volume())
    panel.on_visible(True)
    panel.on_camera(_reported(panel.camera_id, (5.0, 5.0, 5.0)))

    panel.dispose()

    carried = panel.legacy[Carry.CAMERA]
    assert isinstance(carried, pv.Camera) and carried.position == pytest.approx((5.0, 5.0, 5.0))
    assert panel.legacy[Carry.MODE] == "client" and panel.legacy[Carry.SHOWN] is True

    successor = ScenePanel(app, "scene", "Scene", renderable=Volume())
    successor.succeed(panel)

    assert successor.plotter.camera.position == pytest.approx((5.0, 5.0, 5.0)) and successor.shown

    streamed = ScenePanel(app, "streamed", "Streamed", mode=ViewMode.SERVER)
    streamed.dispose()
    rebuilt = ScenePanel(app, "streamed", "Streamed")
    rebuilt.succeed(streamed)

    assert rebuilt.mode is ViewMode.SERVER, "a rebuild keeps the transport, so a fallback the store never saw survives it"
    assert app.persist.load(app.scope, rebuilt.store_key) is None


def test_the_screenshot_adopts_the_mirror_camera_and_is_twice_the_panel_in_either_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platformdirs, "user_pictures_dir", lambda: str(tmp_path / "pictures"))
    app = _shell("shot", tmp_path)
    panel = ScenePanel(app, "scene", "Scene", renderable=Volume())
    panel.on_client_size({"width": PANEL_SIZE[0], "height": PANEL_SIZE[1], "ratio": 1})
    panel.on_camera(_reported(panel.camera_id, (5.0, 5.0, 5.0)))

    panel.screenshot()

    status = str(app.state[AppVar.STATUS])
    assert status.startswith("screenshot ")
    path = Path(status.removeprefix("screenshot "))
    assert path.exists() and path.parent == tmp_path / "pictures" / "femforge-workbench"
    with Image.open(path) as image:
        assert image.size == tuple(side * SCREENSHOT_SCALE for side in PANEL_SIZE), "the mirror renders at the device ratio, so the file is scaled twice"

    assert panel.plotter.camera.position == pytest.approx((5.0, 5.0, 5.0))

    panel.set_mode("server")
    assert panel.handler is not None
    panel.handler.update_size("client", {"w": PANEL_SIZE[0], "h": PANEL_SIZE[1], "p": 1})

    panel.screenshot()

    streamed = Path(str(app.state[AppVar.STATUS]).removeprefix("screenshot "))
    with Image.open(streamed) as image:
        assert image.size == tuple(side * SCREENSHOT_SCALE for side in PANEL_SIZE), "the stream already renders at STILL_SCALE, so the file is not scaled twice again"


def test_the_axes_are_a_mirrored_triad_in_client_mode_and_the_labelled_marker_in_server_mode(tmp_path: Path) -> None:
    app = _shell("axes", tmp_path)
    panel = ScenePanel(app, "scene", "Scene", renderable=Volume())
    renderer = panel.plotter.renderer

    panel.toggle_axes()

    assert panel.triad.actor.GetVisibility() and not renderer.axes_enabled
    assert panel.triad.renderer.GetLayer() == 1 and panel.triad.renderer.GetViewport() == (0.0, 0.0, 0.18, 0.22)
    corner = panel.triad.renderer.GetActiveCamera()
    reach = max(panel.triad.actor.GetTotalLength())
    assert corner.GetFocalPoint() == (0.0, 0.0, 0.0) and corner.GetParallelScale() > reach, "the turning arrows stay framed around the origin, not around the unturned bounds"
    assert tuple(panel.triad.matrix.GetData()) == pytest.approx(view_rotation(renderer.GetActiveCamera()))

    panel.set_mode("server")

    assert not panel.triad.actor.GetVisibility() and renderer.axes_enabled
    actor = renderer.axes_actor
    assert actor is not None
    label = cast("tuple[float, float, float]", actor.GetXAxisCaptionActor2D().GetCaptionTextProperty().GetColor())  # pyright: ignore[reportUnknownMemberType]  # vtk caption actors ship no types
    assert label == pytest.approx(pv.Color(hex_color(app.palette.text)).float_rgb)

    panel.toggle_axes()
    assert not renderer.axes_enabled and not panel.triad.actor.GetVisibility()

    panel.look(View.RIGHT)
    assert tuple(panel.triad.matrix.GetData())[:12] == pytest.approx((0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0)), "looking down -x: right is +y, up is +z, back is +x"
