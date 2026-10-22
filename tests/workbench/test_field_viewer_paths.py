"""The field viewer's guard paths: stale opens, empty controllers, failed reads and the camera edge cases."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from femforge.common.files import RunFile
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.run.bundle import write_manifest
from femforge.run.inspect import Run
from femforge.run.manifest import RunManifest
from femforge.run.result import RunStatus
from femforge.run.retention import Cleanup, CleanupRecord, CleanupTrigger, RemovedFile, RetentionPolicy
from femforge.telemetry.paths import Telemetry
from femforge.viz.field_display import BodyPaint, DisplaySpec
from femforge.viz.field_lines import SeedPlacement, StreamlineActor
from femforge.viz.glyphs import EntityKind, EntityRef, GlyphHost, GlyphLayer
from femforge.viz.symmetry import Expansion
from tests.workbench.test_field_viewer import _bundle, _controller, _glyph_bundle, _hex_grid, _index, _live_shell, _quad_grid, _shell


pytest.importorskip("trame")
pytest.importorskip("pyvista")

from femforge_workbench.panels import Shared  # noqa: E402
from femforge_workbench.panels import field_viewer as field_viewer_module  # noqa: E402
from femforge_workbench.panels.field_viewer import FieldPanel, FieldVar, FieldViewerPanel, highlightable, missing_fields, only  # noqa: E402


def test_a_run_whose_fields_were_purged_says_when_and_by_whom(tmp_path: Path) -> None:
    bundle = tmp_path / "purged"
    manifest = RunManifest.build(SimulationConfig(mesh=MeshConfig(step="data/does-not-exist.step")), status=RunStatus.OK, run_id="purged")
    removed = RemovedFile(path="case_t0001.vtu", size=10, file_class=RunFile.FIELDS, reason=Cleanup.ON_SUCCESS)
    record = CleanupRecord(applied_at=datetime(2026, 10, 2, tzinfo=timezone.utc), trigger=CleanupTrigger.PURGE, status=RunStatus.OK, policy=RetentionPolicy.minimal(), removed=[removed])
    manifest.cleanups.append(record)
    _ = write_manifest(bundle, manifest)

    assert missing_fields(Run(bundle)) == "Fields purged on 2026-10-02 by purge; re-solve to restore them"


def test_group_cells_that_fail_to_read_cost_the_highlight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-cells", tmp_path, monkeypatch)
        controller = _controller(app)

        await controller.load(_glyph_bundle(tmp_path / "tipped"))
        await controller.settle()
        scene = controller.scene
        assert scene is not None and scene.glyphs is not None

        def broken(*_: object) -> None:
            raise ValueError("unreadable cells")

        monkeypatch.setattr(GlyphLayer, "group_cells", broken)

        assert highlightable(scene.glyphs, scene, Run(tmp_path / "tipped")) is None

    asyncio.run(run())


def test_a_one_thumb_reading_takes_the_fallback_for_any_other_count() -> None:
    assert only([1.0, 2.0], 5.0) == 5.0
    assert only([], 5.0) == 5.0
    assert only([3.0], 5.0) == 3.0


def test_an_open_overtaken_by_a_newer_one_shows_only_the_newer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-overtaken", tmp_path, monkeypatch)
        controller = _controller(app)
        older = _bundle(tmp_path / "hex", _hex_grid())
        newer = _bundle(tmp_path / "quad", _quad_grid())

        _ = await asyncio.gather(controller.load(older), controller.load(newer))
        await controller.settle()

        assert controller.scene is not None and controller.scene.run_id == "quad"
        assert app.state[FieldVar.BUSY] is False

    asyncio.run(run())


def test_an_open_overtaken_while_its_scene_reads_draws_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-late", tmp_path, monkeypatch)
        controller = _controller(app)
        real = field_viewer_module.highlightable

        def newer(layer: GlyphLayer | None, host: GlyphHost | None, run: Run) -> frozenset[str] | None:
            controller.generation += 1

            return real(layer, host, run)

        monkeypatch.setattr(field_viewer_module, "highlightable", newer)

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))

        assert controller.scene is None and controller.drawn is None

    asyncio.run(run())


def test_a_wait_for_fields_ends_once_a_newer_open_came(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(field_viewer_module, "FIELDS_PERIOD", 0.01)

    async def run() -> list[object]:
        app = _shell("paths-wait", tmp_path, monkeypatch)
        controller = _controller(app)
        bundle = _bundle(tmp_path / "hex", _hex_grid())
        opened: list[object] = []
        monkeypatch.setattr(controller, "open", opened.append)

        waiting = asyncio.create_task(controller.await_fields(bundle, bundle, controller.generation))
        await asyncio.sleep(0)
        controller.generation += 1
        await waiting

        return opened

    assert asyncio.run(run()) == []


def test_mesh_groups_read_for_an_older_open_are_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-groups", tmp_path, monkeypatch)
        controller = _controller(app)
        bundle = _glyph_bundle(tmp_path / "meshed", fields=False)

        await controller.adopt_groups(Run(bundle), controller.generation - 1)

        assert controller.groups is None and controller.highlightable is None

    asyncio.run(run())


def test_framing_a_body_centres_the_camera_on_it_and_nothing_shown_leaves_the_camera(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-frame", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)
        camera = viewer.plotter.camera
        before = tuple(camera.focal_point)

        controller.frame_entity(EntityRef(EntityKind.BODY, "LEFT"))

        assert tuple(camera.focal_point) == before

        await controller.load(_glyph_bundle(tmp_path / "tipped"))
        await controller.settle()
        app.state[FieldVar.SETUP_SHOWN] = True
        controller.on_glyphs()
        await controller.settle()
        scene = controller.scene
        assert scene is not None and scene.glyphs is not None
        left = EntityRef(EntityKind.BODY, "LEFT")
        bounds = scene.glyphs.bounds_of(left)
        assert bounds is not None
        centre = tuple((bounds[axis] + bounds[axis + 1]) / 2.0 for axis in (0, 2, 4))

        controller.frame_entity(left)

        assert tuple(camera.focal_point) == pytest.approx(centre)

    asyncio.run(run())


def test_a_camera_looking_along_its_up_fits_the_scene_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-fit", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)

        await controller.load(_glyph_bundle(tmp_path / "tipped"))
        await controller.settle()
        app.state[FieldVar.SETUP_SHOWN] = True
        controller.on_glyphs()
        await controller.settle()
        camera = viewer.plotter.camera
        camera.position = (0.0, 0.0, 10.0)
        camera.focal_point = (0.0, 0.0, 0.0)
        camera.up = (0.0, 0.0, 1.0)

        assert controller.fit_bounds() is None

    asyncio.run(run())


def test_resolve_takes_a_run_as_is_and_names_a_run_id_the_index_lacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _live_shell("paths-resolve", tmp_path, monkeypatch)
    controller = _controller(app)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _index(Telemetry(tmp_path / "telemetry"), runs_dir, "solved", 0, "ok", has_vtu=True)
    run = Run(_bundle(runs_dir / "solved", _hex_grid()))

    assert controller.resolve(run) is run
    with pytest.raises(FileNotFoundError, match="no run 'ghost' in the run index"):
        _ = controller.resolve("ghost")


def test_a_second_body_colormap_pick_only_changes_the_colormap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-colormap", tmp_path, monkeypatch)
        controller = _controller(app)

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        app.state[FieldVar.VISIBLE_BODIES] = ["LEFT"]
        controller.on_body_colormap_pick("gray")
        controller.on_body_colormap_pick("viridis")

        assert app.state[FieldVar.BODY_COLORMAP] == "viridis"
        assert [controller.look(name).paint for name in ("LEFT", "RIGHT")] == [BodyPaint.COLORMAP, BodyPaint.FIELD], "the second pick takes no more bodies"

    asyncio.run(run())


def test_the_spec_falls_back_to_the_first_field_and_an_unmappable_range_to_automatic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-spec", tmp_path, monkeypatch)
        controller = _controller(app)

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        scene = controller.scene
        assert scene is not None

        assert controller.fit(DisplaySpec(field="no such field"), scene).field == "joule heating"

        app.state[FieldVar.AUTO_CLIM] = False
        app.state[FieldVar.CLIM] = [3.0, 1.0]

        assert controller.spec_from_state().clim is None

    asyncio.run(run())


def test_a_flat_display_seeds_the_center_plane_in_place_of_a_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("paths-flat-seeds", tmp_path, monkeypatch)
    controller = _controller(app)
    app.state[FieldVar.SEED_PLACEMENT] = str(SeedPlacement.VOLUME)

    assert controller.streamline_options(2).seed_placement is SeedPlacement.CENTER_PLANE


def test_an_empty_controller_ignores_every_draw_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-empty", tmp_path, monkeypatch)
        controller = _controller(app)
        app.state[FieldVar.EXPANSION] = str(Expansion.MIRROR)

        assert controller.vector_field() is None

        controller.settle_expansion()
        await controller.redraw(DisplaySpec())
        controller.publish_seeds()
        controller.on_commit()
        controller.on_clim_commit()
        controller.follow_selection()

        assert app.state[FieldVar.EXPANSION] == str(Expansion.MIRROR) and app.state[FieldVar.AUTO_CLIM] is True
        assert controller.task is None and controller.load_task is None and controller.drawn is None

    asyncio.run(run())


def test_a_draw_without_a_view_or_after_the_scene_left_draws_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-no-draw", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = controller.viewer

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        scene = controller.scene
        assert scene is not None
        spec = controller.spec_from_state()
        controller.drawn = None

        controller.viewer = None
        await controller.draw_plotter(scene, spec)

        assert controller.drawn is None

        async def leaving(*_: object) -> None:
            controller.scene = None

        controller.viewer = viewer
        monkeypatch.setattr(controller, "expand", leaving)
        await controller.draw_plotter(scene, spec)

        assert controller.drawn is None

    asyncio.run(run())


def test_a_reframed_scene_too_large_for_the_mirror_streams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> list[object]:
        app = _shell("paths-oversized", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)

        await controller.load(_bundle(tmp_path / "quad", _quad_grid()))
        await controller.settle()
        fallen: list[object] = []

        def oversized(_footprint: object) -> bool:
            return True

        monkeypatch.setattr(viewer, "oversized", oversized)
        monkeypatch.setattr(viewer, "fall_back", fallen.append)
        app.state[FieldVar.EXPANSION] = str(Expansion.SECTION)
        controller.request_redraw()
        await controller.settle()

        return fallen

    assert len(asyncio.run(run())) == 1


def test_field_lines_integrated_after_the_scene_left_are_not_drawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> bool:
        app = _shell("paths-lines-late", tmp_path, monkeypatch)
        controller = _controller(app)
        viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        scene = controller.scene
        assert scene is not None
        app.state[FieldVar.STREAMLINES] = False
        controller.request_redraw()
        await controller.settle()
        assert str(StreamlineActor.LINES) not in viewer.plotter.renderer.actors
        integrate = scene.integrate_streamlines

        def leaving(options: object) -> object:
            actors = integrate(options)  # pyright: ignore[reportArgumentType]  # the patched method receives the FieldLineOptions the scene passes
            controller.drawn = None

            return actors

        monkeypatch.setattr(scene, "integrate_streamlines", leaving)
        app.state[FieldVar.FIELD] = "magnetic flux density"
        app.state[FieldVar.STREAMLINES] = True
        app.state[FieldVar.LINE_COUNT] = [2]
        controller.request_redraw()
        await controller.settle()

        return str(StreamlineActor.LINES) in viewer.plotter.renderer.actors

    assert asyncio.run(run()) is False


def test_the_shown_run_selected_again_by_id_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-same", tmp_path, monkeypatch)
        controller = _controller(app)

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        app.state[Shared.SELECTED_RUN_ID] = "hex"
        controller.follow_selection()

        assert controller.load_task is None

    asyncio.run(run())


def test_an_unknown_field_keeps_the_offered_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-unknown-field", tmp_path, monkeypatch)
        controller = _controller(app)

        await controller.load(_bundle(tmp_path / "hex", _hex_grid()))
        await controller.settle()
        modes = app.state[FieldVar.MODES]
        app.state[FieldVar.FIELD] = "no such field"
        controller.on_field()

        assert app.state[FieldVar.MODES] == modes

    asyncio.run(run())


def test_a_location_set_in_the_run_picker_opens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = _shell("paths-picker", tmp_path, monkeypatch)
        controller = _controller(app)
        bundle = _bundle(tmp_path / "hex", _hex_grid())

        app.state[FieldVar.RUN] = str(bundle)
        controller.on_run()
        await controller.settle()

        assert controller.scene is not None and controller.scene.run_id == "hex"

    asyncio.run(run())


def test_the_view_tells_the_controller_when_it_is_on_screen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _shell("paths-visible", tmp_path, monkeypatch)
    controller = _controller(app)
    viewer = app.panel(FieldPanel.VIEW_3D, FieldViewerPanel)

    viewer.on_visible(True)

    assert controller.visible and viewer.shown

    viewer.on_visible(False)

    assert not controller.visible
