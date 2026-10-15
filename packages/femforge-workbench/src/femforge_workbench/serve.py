"""Serves a workbench from a script or the command line: a dock, a catalogue card, or the launcher."""

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from femforge.common.log import TELEMETRY_ENV, log
from femforge.viz.scene import Renderable
from femforge_workbench.app import Window, Workbench
from femforge_workbench.dock import Panel, Split
from femforge_workbench.panels.scene import SceneViewerPanel
from femforge_workbench.persist import LocalJsonStore, UiStateStore
from femforge_workbench.registry import LAUNCHER, WorkbenchKind, WorkbenchSpec, discover, panel, resolve


DEFAULT_PORT = 8080
"""The port a workbench serves on unless told otherwise."""

DEFAULT_TITLE = "femforge workbench"
VIEWER_PANEL = "viewer"

type Factory = Callable[[], Split | Renderable]
"""A module-level function building a dock Split or a Renderable."""


class NotAWorkbench(TypeError):
    """A target that builds neither a dock Split nor a Renderable."""


def show(
    workbench: Split | Factory | WorkbenchSpec | None = None,
    *,
    title: str | None = None,
    workbenches: Sequence[WorkbenchSpec] = (),
    window: Window = Window.APP,
    port: int = DEFAULT_PORT,
    telemetry: str | Path | None = None,
    persist: str | Path | None = None,
    watch: Sequence[str | Path] = (),
) -> None:
    """Serves a workbench until its window closes: a Split as built, a factory or card rebuilt on every save, or the launcher when none is given.

    Args:
        workbench: The Split, factory or card to serve; None serves the launcher.
        title: The header title; None keeps the card's own, or femforge workbench for a Split.
        workbenches: Cards the launcher lists beside the discovered ones, and the only cards a single workbench's title menu lists.
        window: Where the workbench opens.
        port: The port the server listens on.
        telemetry: The telemetry root the panels read, exported as FEMFORGE_TELEMETRY; None keeps the environment's.
        persist: The directory layouts and opened cards persist under; None uses the per-user config directory.
        watch: More directories whose .py saves rebuild a factory or card, beside its own roots.
    """
    if telemetry is not None:
        os.environ[TELEMETRY_ENV] = str(telemetry)

    store = LocalJsonStore(Path(persist).resolve()) if persist is not None else None
    if isinstance(workbench, Split):
        shown = title or DEFAULT_TITLE
        app = Workbench(shown.lower().replace(" ", "-"), title=shown, persist=store, catalogue=workbenches)
        app.add(workbench)
    else:
        spec = card(workbench)
        if spec.kind is WorkbenchKind.SCRIPT:
            log.bind(command=spec.command()).info("Running the script.")
            _ = subprocess.run(spec.command(), cwd=spec.cwd, check=False)
            return

        catalogue = [*discover(), *workbenches] if spec.name == LAUNCHER.name else workbenches
        app = build(replace(spec, title=title) if title else spec, catalogue=catalogue, watch=[Path(root).resolve() for root in watch], persist=store)

    app.run(port=port, window=window)


def card(workbench: Factory | WorkbenchSpec | None) -> WorkbenchSpec:
    """The spec to open: the launcher for None, a dock card named after a factory."""
    if workbench is None:
        return LAUNCHER

    if isinstance(workbench, WorkbenchSpec):
        return workbench

    return panel(workbench.__name__, workbench.__name__.replace("_", " "), target=workbench)


def build(spec: WorkbenchSpec, *, catalogue: Sequence[WorkbenchSpec] | None = None, watch: Sequence[Path] = (), persist: UiStateStore | None = None) -> Workbench:
    """A shell holding the spec's dock, rebuilt on every save under its roots, or a viewer of its Renderable.

    Args:
        spec: The card to serve.
        catalogue: The cards the title menu lists; None lists the discovered ones.
        watch: More directories whose .py saves rebuild the card, beside its own roots.
        persist: Where layouts and opened cards persist; None uses JSON files under the per-user config directory.
    """
    app = Workbench(spec.name, title=spec.title, persist=persist, catalogue=catalogue)
    if spec.kind is WorkbenchKind.RENDERABLE:
        _ = viewer(app, spec, watch)
        return app

    factory = resolve(spec.target)
    if not callable(factory):
        raise NotAWorkbench(f"{spec.target} is not callable")

    root = factory()
    if isinstance(root, Split):
        app.add(root, spec=spec, watch=watch)
        for scene in (built for built in app.panels if isinstance(built, SceneViewerPanel)):
            scene.watch.extend(watch)

        return app

    if isinstance(root, Renderable):
        _ = viewer(app, replace(spec, kind=WorkbenchKind.RENDERABLE), watch)
        return app

    raise NotAWorkbench(f"{spec.target}() returned {type(root).__name__}; a Split or a Renderable is needed")


def viewer(app: Workbench, spec: WorkbenchSpec, watch: Sequence[Path]) -> SceneViewerPanel:
    """One scene panel that shows the spec's Renderable once the server runs, rebuilding it on every save under its roots.

    Args:
        app: The shell to add the scene panel to.
        spec: The card whose target builds the Renderable.
        watch: More directories whose .py saves rebuild the Renderable, beside the spec's own roots.
    """

    def scene_panel(app: Workbench, id: str, title: str) -> SceneViewerPanel:
        return SceneViewerPanel(app, id, title, watch=watch)

    app.add(Split([Panel(spec.title, scene_panel, id=VIEWER_PANEL)]))
    scene = app.panel(VIEWER_PANEL, SceneViewerPanel)

    def stop(**_: object) -> None:
        scene.stop()

    app.when_ready(lambda: scene.show(spec))
    _ = app.ctrl.on_server_exited.add(stop)

    return scene
