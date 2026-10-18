"""The workbench shell: a React header over a dock whose panels are trame templates, with persisted layouts and a worker-thread marshal."""

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import wraps
from pathlib import Path
from types import FrameType
from typing import Concatenate, Protocol, cast

import platformdirs
from trame.app import TrameApp
from trame.decorators import life_cycle
from trame.ui.html import DivLayout
from trame.widgets import client, html, rca, react, vtklocal
from trame_server import Server

from femforge.common.log import LogLevel, gate_libraries, get_console, log
from femforge_workbench import dock, widgets
from femforge_workbench.dev import DEV_ENV, reload_script
from femforge_workbench.panels import PanelComponent
from femforge_workbench.persist import APP_NAME, LocalJsonStore, StateKey, UiStateStore, default_user
from femforge_workbench.registry import WorkbenchKind, WorkbenchSpec, discover, resolve
from femforge_workbench.reload import Reloader, default_roots, persistent
from femforge_workbench.theme import Palette
from femforge_workbench.widgets.button import Button
from femforge_workbench.widgets.dock import Dock
from femforge_workbench.widgets.dropdown_menu import DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger
from femforge_workbench.widgets.icon import Icon
from femforge_workbench.widgets.module import asset_url
from femforge_workbench.widgets.theme import Theme
from femforge_workbench.widgets.tour import Tour, TourStep


APP_WINDOW_SIZE = "1600,1000"
APP_PROFILE = "app-profile"
APP_FLAG = "--app"
TITLE_VAR = "trame__title"
FAVICON_VAR = "trame__favicon"
"""The trame state variable the client puts in the page's icon link."""

BRAND = "femforge"
LOGO = "ff-logo.svg"
FAVICON = "favicon.png"
"""The state variable trame's client shows as the page title."""
BROWSERS = (Path("C:/Program Files/Google/Chrome/Application/chrome.exe"), Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"))
POPOUT_PAGE = "popout.html"
PANEL_PARAM = "panel"
"""The render-prop parameter carrying a dock panel's params to its server template."""
TOUR_DELAY = 1.0
"""Seconds after the first scene shows before the tour opens on its own."""
HELP_ID = "tour-help"
"""The id of the app bar's help button, the tour's last stop."""
TOUR_LABEL = "Take the Tour"
REPLAY_STEP: TourStep = {"target": f"#{HELP_ID}", "title": "Replay This Tour", "body": "Open this tour again from here at any time.", "side": "bottom"}


class WidgetLibrary(Protocol):
    """A trame widget package whose initialize() enables its client module on a server."""

    def initialize(self, server: Server) -> None:
        """Enables the library's client module on `server`."""
        ...


WIDGET_LIBRARIES: tuple[WidgetLibrary, ...] = (widgets, rca, vtklocal)
"""Every widget library a panel may use: a client loads only the modules enabled before it connected, and a card can open such panels later."""

QUIET_LIBRARIES = (
    "trame_server",
    "trame_client",
    "trame_common",
    "trame_rca",
    "trame_vtklocal",
    "wslink",
    "aiohttp",
    "aiohttp.access",
    "watchdog",
    "asyncio",
)
"""Stdlib loggers gated to WARNING by every shell, whichever entry point built it: the access log alone writes thousands of lines per page load."""


class Cls(StrEnum):
    """The shell's Tailwind compositions; the strings appear literally so the stylesheet build scans them."""

    ROOT = "flex h-full flex-col"
    HEADER = "flex shrink-0 items-center gap-3 border-b border-border bg-background px-3 h-(--ff-bar-height)"
    HEADER_SLOT = "flex items-center gap-3"
    BRAND = "flex shrink-0 items-center gap-1.5 font-semibold tracking-tight"
    BRAND_MARK = "size-5"
    DIVIDER = "h-5 w-px shrink-0 bg-border"
    TITLE = "gap-1 font-semibold"
    MENU_ITEM = "flex-col items-start gap-0"
    MENU_DESCRIPTION = "text-label text-muted-foreground"
    STATUS = "ml-auto truncate text-xs text-muted-foreground"
    MAIN = "min-h-0 flex-1"


class Window(StrEnum):
    """Where a served workbench opens."""

    BROWSER = "browser"
    """A tab in the default browser."""

    APP = "app"
    """A Chrome/Edge app window; closing it stops the server."""

    WEBVIEW = "webview"
    """pywebview, from the app extra."""


class Template(StrEnum):
    """Templates rendered outside the panels."""

    HEADER = "app-header"


class StateVar(StrEnum):
    """The shell's own state variables."""

    STATUS = "wb_status"
    TITLE = "wb_title"
    DARK = "wb_dark"
    WORKBENCHES = "wb_workbenches"
    OPENED = "wb_opened"
    TOUR_OPEN = "wb_tour_open"
    TOUR_STEPS = "wb_tour_steps"


class TourKey(StrEnum):
    """The fields of the persisted tour document."""

    SEEN = "seen"


class Hook(StrEnum):
    """Controller slots panels fill: the launcher takes OPEN_WORKBENCH, the field viewer FIELD_OPEN."""

    OPEN_WORKBENCH = "open_workbench"
    FIELD_OPEN = "field_open"


class OpenedKey(StrEnum):
    """The fields of the persisted opened document: the cards open in the dock, in opening order."""

    ENTRIES = "entries"
    NAME = "name"
    REFERENCE = "reference"


class CardKey(StrEnum):
    """The fields of a catalogue card in the state."""

    NAME = "name"
    TITLE = "title"
    DESCRIPTION = "description"
    GROUP = "group"


@dataclass(frozen=True)
class Opened:
    """A Split in the running dock: the card it serves (None for a bare Split), the panel it was anchored to and the header widgets it added."""

    root: dock.Split
    reference: str | None
    name: str | None = None
    header: tuple[object, ...] = ()


@dataclass(frozen=True)
class Watch:
    """A card rebuilt on save and the reloader watching its target's roots."""

    name: str
    reloader: Reloader[dock.Split]


def check_unique(ids: Sequence[str]) -> None:
    """Rejects a panel id used twice in one Split."""
    repeated = sorted({panel_id for panel_id in ids if ids.count(panel_id) > 1})
    if repeated:
        raise ValueError(f"panel ids repeat: {repeated}")


class Reporting(Protocol):
    """Anything with a status line: the shell and its panels."""

    def status(self, message: str) -> None:
        """Writes `message` to the status line."""
        ...


def guard[S: Reporting, **P](handler: Callable[Concatenate[S, P], None]) -> Callable[Concatenate[S, P], None]:
    """Prints a handler's exception with rich and reports it in the status line instead of letting it escape into wslink."""

    @wraps(handler)
    def guarded(self: S, *args: P.args, **kwargs: P.kwargs) -> None:
        try:
            handler(self, *args, **kwargs)
        except Exception as error:
            get_console().print_exception()
            self.status(f"{type(error).__name__}: {error}")

    return guarded


class Workbench(TrameApp):
    """One trame server with the header, the dock and the panels of a named workbench."""

    def __init__(
        self,
        name: str,
        *,
        palette: Palette | None = None,
        title: str = "femforge workbench",
        server: Server | None = None,
        persist: UiStateStore | None = None,
        user: str | None = None,
        catalogue: Sequence[WorkbenchSpec] | None = None,
    ) -> None:
        """Builds the shell: the header, the dock and the shared state, before any panel is added.

        Args:
            name: The workbench name, which names the trame server and scopes the persisted state.
            palette: The colours the shell and its panels paint in; None loads the dist's dark palette.
            title: The title the header and the browser tab show.
            server: The trame server to run on; None makes one named after `name`.
            persist: Where layouts and opened cards persist; None keeps them in JSON files under the per-user config directory.
            user: The user the persisted state is scoped to; None takes the OS user.
            catalogue: The cards the title menu and a launcher panel list; None lists the discovered ones.
        """
        gate_libraries(dict.fromkeys(QUIET_LIBRARIES, LogLevel.WARNING))
        super().__init__(server=server if server is not None else name, client_type="react")

        self.name = name
        self.title = title
        self.palette = palette if palette is not None else Palette.load()
        self.palette.install()
        self.persist: UiStateStore = persist if persist is not None else LocalJsonStore()
        self.scope = (user if user is not None else default_user(), name)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.window = Window.BROWSER
        self.authored: Opened | None = None
        """The Split add() built, named after the spec it serves when it is to be rebuilt on save."""
        self.panels: list[PanelComponent] = []
        self.opened: dict[str, Opened] = {}
        """Splits opened into the running dock by their first panel id."""
        self.watches: list[Watch] = []
        self.pending: set[str] = set()
        """Panel ids handed to the dock since a layout last showed them all; a layout still missing one is a placement in flight, not a close."""
        self.on_screen: set[str] = set()
        """Panel ids the dock last reported visible; the dock reports a panel only when it mounts, so a panel rebuilt in place learns it from here."""
        self.catalogue = list(catalogue) if catalogue is not None else self.load_catalogue()
        """The cards the title menu and a launcher panel list: the discovered ones unless the constructor was given its own."""
        self.tour_steps: list[TourStep] = []
        """The tour's stops in order, before the shell's own closing one; panels add theirs through `add_tour_steps`."""
        self.dock: Dock | None = None
        self.header = DivLayout(self.server, template_name=str(Template.HEADER), classes=str(Cls.HEADER_SLOT))
        """The header's slot for a workbench's own controls: build widgets inside `with app.header:` and flush_content() after a later change."""

        cards = [{CardKey.NAME: spec.name, CardKey.TITLE: spec.title, CardKey.DESCRIPTION: spec.description, CardKey.GROUP: spec.group} for spec in self.catalogue]
        self.state.update(
            {
                StateVar.STATUS: "ready",
                StateVar.TITLE: title,
                StateVar.DARK: self.palette.is_dark,
                StateVar.WORKBENCHES: cards,
                StateVar.OPENED: [],
                StateVar.TOUR_OPEN: False,
                StateVar.TOUR_STEPS: [REPLAY_STEP],
                TITLE_VAR: title,
                FAVICON_VAR: asset_url(FAVICON),
            }
        )

        for library in WIDGET_LIBRARIES:
            library.initialize(self.server)

        self.ui = self.build_ui()

    def load_catalogue(self) -> list[WorkbenchSpec]:
        """The discovered specs, empty with a warning when an entry point is broken."""
        try:
            return discover()
        except (TypeError, ValueError, ImportError):
            log.opt(exception=True).warning("Workbench catalogue unavailable.")
            return []

    def build_ui(self) -> DivLayout:
        """The theme switch, the header and the dock, whose panel bodies are the server templates the placements name."""
        with DivLayout(self.server, classes=str(Cls.ROOT)) as layout:
            _ = self.theme()
            if os.environ.get(DEV_ENV):
                client.Script(reload_script())

            with html.Header(classes=str(Cls.HEADER)):
                with html.Div(classes=str(Cls.BRAND)):
                    html.Img(src=asset_url(LOGO), alt="", classes=str(Cls.BRAND_MARK))
                    html.Span(BRAND)

                html.Div(classes=str(Cls.DIVIDER), aria_hidden=True)
                if self.catalogue:
                    self.build_switcher()
                else:
                    html.Span([react.Bind(StateVar.TITLE)], classes=str(Cls.TITLE))

                client.ServerTemplate(name=str(Template.HEADER))
                with react.If(f"{StateVar.OPENED}.length"):
                    Button("Close All", variant="destructive", size="sm", title="Close every opened card", on_click=react.Callback(self.close_all))

                with Button(
                    size="icon-sm",
                    variant="ghost",
                    title="Reset the layout",
                    aria_label="Reset the layout",
                    on_click=react.Callback(self.reset_layout),
                ):
                    Icon("layout-grid")

                html.Span([react.Bind(StateVar.STATUS)], classes=str(Cls.STATUS))
                with Button(size="icon-sm", variant="ghost", id=HELP_ID, title=TOUR_LABEL, aria_label=TOUR_LABEL, on_click=react.Callback(self.start_tour)):
                    Icon("circle-help")

            Tour(model=StateVar.TOUR_OPEN, steps=react.Bind(StateVar.TOUR_STEPS), on_finish=react.Callback(self.on_tour_finish, args="[$event]"))

            with html.Main(classes=str(Cls.MAIN)):
                with react.Slot(params=[PANEL_PARAM]) as render_panel:
                    client.ServerTemplate(name=react.Bind(f"{PANEL_PARAM}.{dock.Key.TEMPLATE_NAME}"))

                self.dock = Dock(
                    render_panel=render_panel,
                    dark=react.Bind(StateVar.DARK),
                    popout_url=asset_url(POPOUT_PAGE),
                    floating_group_bounds="boundedWithinViewport",
                    on_ready=react.Callback(self.on_dock_ready),
                    on_layout_change=react.Callback(self.on_layout_changed, args="[$event]"),
                    on_visibility=react.Callback(self.on_panel_visibility, args="[$event]"),
                    on_panel_removed=react.Callback(self.on_panel_removed, args="[$event]"),
                )

        return layout

    def theme(self) -> Theme:
        """The scheme switch bound to the dark state; mounted in the root and in every panel, so a standalone ?ui= page and a popout paint it too."""
        return Theme(dark=react.Bind(StateVar.DARK))

    def build_switcher(self) -> None:
        """The title as a menu over the catalogue."""
        with DropdownMenu():
            with DropdownMenuTrigger(as_child=True), Button(variant="ghost", size="sm", classes=str(Cls.TITLE)):
                html.Span([react.Bind(StateVar.TITLE)])
                Icon("chevron-down")

            with DropdownMenuContent(align="start"), react.For(items=StateVar.WORKBENCHES, name="card"):
                with DropdownMenuItem(on_select=react.Callback(self.open_workbench, args=f"[card.{CardKey.NAME}]"), classes=str(Cls.MENU_ITEM)):
                    html.Span([react.Bind(f"card.{CardKey.TITLE}")])
                    html.Span([react.Bind(f"card.{CardKey.DESCRIPTION}")], classes=str(Cls.MENU_DESCRIPTION))

    @property
    def root(self) -> dock.Split | None:
        """The authored Split, None before `add`."""
        return self.authored.root if self.authored is not None else None

    def add_tour_steps(self, steps: Sequence[TourStep]) -> None:
        """Adds stops to the tour before the shell's closing one; a stop at a target already listed replaces it, so a rebuilt panel adds nothing twice."""
        for step in steps:
            self.tour_steps = [kept for kept in self.tour_steps if kept["target"] != step["target"]]
            self.tour_steps.append(step)

        with self.state:
            self.state[StateVar.TOUR_STEPS] = [*self.tour_steps, REPLAY_STEP]

    @guard
    def start_tour(self) -> None:
        """Opens the tour at its first stop."""
        with self.state:
            self.state[StateVar.TOUR_OPEN] = True

    def offer_tour(self) -> None:
        """Opens the tour on its own a moment after the first scene shows, until the user has finished or skipped it once."""
        saved = self.persist.load(self.scope, StateKey.TOUR) or {}
        if saved.get(TourKey.SEEN) or self.state[StateVar.TOUR_OPEN]:
            return

        if self.loop is None:
            self.start_tour()
            return

        _ = self.loop.call_later(TOUR_DELAY, self.start_tour)

    @guard
    def on_tour_finish(self, reason: str) -> None:
        """Records that the user has seen the tour, finished or skipped; the status line keeps whatever the panels last reported."""
        self.persist.save(self.scope, StateKey.TOUR, {str(TourKey.SEEN): True})
        log.bind(workbench=self.name, reason=reason).info("Tour ended.")

    def add(self, root: dock.Split, *, spec: WorkbenchSpec | None = None, watch: Sequence[Path] = ()) -> None:
        """Builds every panel's component and template; the dock arranges them once the client reports ready.

        Args:
            root: The Split to serve.
            spec: The card the Split was built from; given, the Split is rebuilt on every save.
            watch: More directories whose .py saves rebuild the Split, beside the spec's own roots.
        """
        leaves = dock.panels(root)
        ids = [leaf.panel_id for leaf in leaves]
        check_unique(ids)

        header_before = len(self.header.root.children)
        self.panels = []
        _ = self.register(leaves)
        self.authored = Opened(root, None, spec.name if spec is not None else None, tuple(self.header.root.children[header_before:]))

        log.bind(workbench=self.name, panels=ids).info("Panels built.")

        self.restore_opened()
        if spec is not None:
            self.watch(spec, extra=watch)

    def open(self, root: dock.Split, *, reference: str | None = None, name: str | None = None) -> None:
        """Adds a Split's panels to the running dock, building those not built yet, and activates the first.

        Args:
            root: The Split whose panels to add.
            reference: The panel whose group the first panel joins; None lets the dock place it.
            name: The card the Split comes from, remembered so the next start reopens it; None for a bare Split.
        """
        leaves = dock.panels(root)
        ids = [leaf.panel_id for leaf in leaves]
        check_unique(ids)

        known = self.panel_ids
        fresh = [leaf for leaf in leaves if leaf.panel_id not in known]
        header_before = len(self.header.root.children)
        _ = self.register(fresh)
        self.header.flush_content()
        self.opened[ids[0]] = Opened(root, reference, name, tuple(self.header.root.children[header_before:]))

        self.place(root, reference)
        if self.dock is not None:
            self.dock.active_panel(ids[0])

        if name is not None:
            self.remember_opened()

        log.bind(workbench=self.name, panels=ids, built=[leaf.panel_id for leaf in fresh]).info("Panels opened.")

    def open_spec(self, spec: WorkbenchSpec, *, reference: str | None = None) -> None:
        """Opens a dock card: its target builds the Split, remembered under the card's name so the next start rebuilds it."""
        if spec.kind is not WorkbenchKind.PANEL:
            raise ValueError(f"{spec.name} is not a dock")

        factory = resolve(spec.target)
        if not callable(factory):
            raise TypeError(f"{spec.target} is not callable")

        root = factory()
        if not isinstance(root, dock.Split):
            raise TypeError(f"{spec.target}() returned {type(root).__name__}, not a Split")

        self.open(root, reference=reference, name=spec.name)
        self.watch(spec, reference=reference)

    def opened_first(self, name: str) -> str | None:
        """The first panel id of the open card of that name, None when it is not open."""
        return next((first for first, opened in self.opened.items() if opened.name == name), None)

    def close(self, name: str) -> None:
        """Takes an opened card out of the dock and the shell and stops rebuilding it on save."""
        self.discard(name)
        self.unwatch(name)

    @guard
    def close_all(self) -> None:
        """Closes every opened card."""
        names = [opened.name for opened in list(self.opened.values()) if opened.name is not None]
        for name in names:
            self.close(name)

        self.status(f"{len(names)} cards closed")

    def discard(self, name: str) -> None:
        """Takes an opened card out of the dock and the shell: its panels are removed and disposed, its header widgets dropped, the card forgotten."""
        first = self.opened_first(name)
        if first is None:
            raise KeyError(f"{name} is not open")

        opened = self.opened.pop(first)
        ids = dock.panel_ids(opened.root)
        self.pending.difference_update(ids)
        self.on_screen.difference_update(ids)
        if self.dock is not None:
            for panel_id in ids:
                self.dock.remove_panel(panel_id)

        self.retire(ids)
        self.drop_header(opened.header)
        self.header.flush_content()
        self.remember_opened()
        log.bind(workbench=self.name, card=name, panels=ids).info("Card closed.")

    def register(self, leaves: Sequence[dock.Panel]) -> list[PanelComponent]:
        """Builds each leaf and registers it before the next is built, so a panel can see the ones already standing (a second scene panel must know a mirror is taken)."""
        built: list[PanelComponent] = []
        try:
            for leaf in leaves:
                panel = leaf.build(self, leaf.panel_id, leaf.name)
                built.append(panel)
                self.panels.append(panel)
        except Exception:
            for panel in built:
                panel.dispose()
                self.panels.remove(panel)

            raise

        return built

    def retire(self, ids: Sequence[str]) -> None:
        """Disposes the built panels of those ids and drops them from the shell."""
        for panel in [panel for panel in self.panels if panel.id in ids]:
            panel.dispose()
            self.panels.remove(panel)

    def drop_header(self, widgets: Sequence[object]) -> None:
        """Removes a Split's widgets from the header, those still there."""
        for widget in widgets:
            if widget in self.header.root.children:
                self.header.root.children.remove(widget)

    def watch(self, spec: WorkbenchSpec, *, reference: str | None = None, extra: Sequence[Path] = ()) -> None:
        """Rebuilds the spec's Split after every .py save under its roots once the server runs; a spec of the shell core is left to the dev supervisor.

        Args:
            spec: The card whose target builds the Split.
            reference: The panel whose group the rebuilt card joins when it is no longer open.
            extra: More directories to watch, beside the femforge core, the workbench package and the spec's own source root.
        """
        self.unwatch(spec.name)
        if persistent(spec.module):
            return

        roots = [*default_roots(spec.module), *extra]

        def apply(root: dock.Split, seconds: float) -> None:
            self.rebuild(spec.name, root, reference=reference, seconds=seconds)

        def failed(error: Exception) -> None:
            self.status(f"{type(error).__name__}: {error}")

        reloader = Reloader(spec.target, roots, apply, kind=dock.Split, marshal=self.marshal, failed=failed)
        watch = Watch(spec.name, reloader)
        self.watches.append(watch)
        if self.loop is not None:
            reloader.start()
            return

        def start(**_: object) -> None:
            if watch in self.watches:
                reloader.start()

        _ = self.ctrl.on_server_ready.add(start)

    def unwatch(self, name: str) -> None:
        """Stops rebuilding a card on save."""
        for watch in [watch for watch in self.watches if watch.name == name]:
            watch.reloader.stop()
            self.watches.remove(watch)

    @guard
    def rebuild(self, name: str, root: dock.Split, *, reference: str | None = None, seconds: float = 0.0) -> None:
        """Swaps a card's panels for a fresh build under the same ids, the dock untouched; other ids close and reopen the card, and a card no longer open reopens.

        Args:
            name: The card to rebuild.
            root: The fresh build of its Split.
            reference: The panel whose group the card joins when it is no longer open.
            seconds: The seconds the build took, added to the rebuild time the status line reports.
        """
        started = time.perf_counter()
        # Delivered through call_soon_threadsafe, outside any trame handler, so the state transaction is explicit for the flush.
        with self.state:
            if self.authored is not None and self.authored.name == name:
                self.replace_root(root)
            else:
                self.replace_card(name, root, reference)

        elapsed = seconds + time.perf_counter() - started
        log.bind(workbench=self.name, card=name, seconds=round(elapsed, 2)).info("Card rebuilt.")
        self.status(f"rebuilt {name} in {elapsed:.1f} s")

    def replace_root(self, root: dock.Split) -> None:
        """Rebuilds the authored Split in place; changed ids re-lay the dock out from the authored arrangement."""
        authored = self.authored
        if authored is None:
            raise RuntimeError("add() a Split before rebuilding it")

        retired = dock.panel_ids(authored.root)
        header = self.swap(root, retired, authored.header)
        self.authored = replace(authored, root=root, header=header)
        if dock.panel_ids(root) != retired:
            self.reset_layout()

    def replace_card(self, name: str, root: dock.Split, reference: str | None) -> None:
        """Rebuilds an open card in place; changed ids close and reopen it, and a card not open opens.

        Args:
            name: The card to rebuild.
            root: The fresh build of its Split.
            reference: The panel whose group the card joins when it is not open.
        """
        first = self.opened_first(name)
        if first is None:
            self.open(root, reference=reference, name=name)
            return

        opened = self.opened[first]
        retired = dock.panel_ids(opened.root)
        if dock.panel_ids(root) != retired:
            self.discard(name)
            self.open(root, reference=opened.reference, name=name)
            return

        header = self.swap(root, retired, opened.header)
        self.opened[first] = replace(opened, root=root, header=header)

    def swap(self, root: dock.Split, retired: Sequence[str], header: Sequence[object]) -> tuple[object, ...]:
        """Disposes the retired panels, then builds the Split's leaves, whose templates overwrite those of the same ids; returns the header widgets the build added.

        Args:
            root: The Split whose leaves to build.
            retired: The ids of the panels to dispose first.
            header: The header widgets the retired panels added, dropped with them.
        """
        leaves = dock.panels(root)
        check_unique([leaf.panel_id for leaf in leaves])
        previous = [panel for panel in self.panels if panel.id in retired]
        self.retire(retired)
        self.drop_header(header)
        header_before = len(self.header.root.children)

        try:
            built = self.register(leaves)
        except Exception:
            del self.header.root.children[header_before:]
            self.header.flush_content()
            raise

        for panel in built:
            predecessor = next((old for old in previous if old.id == panel.id), None)
            if predecessor is not None:
                panel.succeed(predecessor)

            if panel.id in self.on_screen:
                panel.on_visible(True)

        self.header.flush_content()

        return tuple(self.header.root.children[header_before:])

    def find_spec(self, name: str) -> WorkbenchSpec | None:
        """The catalogue card of that name, None when the catalogue has none."""
        return next((spec for spec in self.catalogue if spec.name == name), None)

    def restore_opened(self) -> None:
        """Reopens the cards the last session left in the dock, dropping any whose card is gone or whose target no longer builds a Split."""
        saved = self.persist.load(self.scope, StateKey.OPENED) or {}
        raw = saved.get(OpenedKey.ENTRIES)
        entries = cast("list[object]", raw) if isinstance(raw, list) else []

        for entry in entries:
            record = cast("dict[str, object]", entry) if isinstance(entry, dict) else {}
            name = str(record.get(OpenedKey.NAME))
            reference = record.get(OpenedKey.REFERENCE)
            spec = self.find_spec(name)
            if spec is None or spec.kind is not WorkbenchKind.PANEL:
                log.bind(workbench=self.name, card=name).warning("Opened card is no longer a dock in the catalogue.")
                continue

            anchor = reference if isinstance(reference, str) and reference in self.panel_ids else None
            try:
                self.open_spec(spec, reference=anchor)
            except Exception:  # noqa: BLE001 - a card that fails to build costs its panels, never the start
                log.bind(workbench=self.name, card=name).opt(exception=True).warning("Opened card failed to rebuild.")

        self.remember_opened()

    def remember_opened(self) -> None:
        """Persists the cards open in the dock, in opening order, and names them in the state for the launcher's cards."""
        cards = [opened for opened in self.opened.values() if opened.name is not None]
        entries = [{str(OpenedKey.NAME): opened.name, str(OpenedKey.REFERENCE): opened.reference} for opened in cards]

        self.persist.save(self.scope, StateKey.OPENED, {str(OpenedKey.ENTRIES): entries})
        with self.state:
            self.state[StateVar.OPENED] = [opened.name for opened in cards]

    def prune_opened(self, layout: Mapping[str, object]) -> None:
        """Forgets an opened Split whose first panel is missing from a settled layout, one that shows every panel handed to the dock since the last."""
        panels = layout.get(dock.Key.PANELS)
        present = set(cast("Mapping[str, object]", panels).keys()) if isinstance(panels, Mapping) else set[str]()
        if self.pending:
            if not self.pending <= present:
                return

            self.pending.clear()

        closed = [first for first in self.opened if first not in present]
        if not closed:
            return

        for first in closed:
            del self.opened[first]

        self.remember_opened()
        log.bind(workbench=self.name, panels=closed).info("Opened panels closed.")

    def place(self, root: dock.Split, reference: str | None = None) -> None:
        """add_panel for every placement of a Split, its first panel within `reference`'s group when given; ids already in the dock are left alone."""
        if self.dock is None:
            return

        for placement in dock.placements(root):
            anchored = replace(placement, reference=reference, direction=dock.PlacementDirection.WITHIN) if placement.reference is None and reference is not None else placement
            self.dock.add_panel(anchored.panel.panel_id, anchored.panel.name, dock.template_name(anchored.panel.panel_id), **anchored.add_on())
            self.pending.add(anchored.panel.panel_id)

    @property
    def panel_ids(self) -> list[str]:
        """The ids of the built panels, in build order."""
        return [panel.id for panel in self.panels]

    def panel[P: PanelComponent](self, panel_id: str, kind: type[P]) -> P:
        """The built component of a panel, checked to be of `kind`."""
        found = next((panel for panel in self.panels if panel.id == panel_id), None)
        if not isinstance(found, kind):
            raise KeyError(f"no {kind.__name__} with id {panel_id!r}")

        return found

    def status(self, message: str) -> None:
        """Writes the status line, flushing so it also lands from outside a handler."""
        with self.state:
            self.state[StateVar.STATUS] = message

    def marshal(self, fn: Callable[..., object], *args: object) -> None:
        """Runs `fn(*args)` on the server loop from any thread."""
        if self.loop is None:
            raise RuntimeError("the server is not running")

        _ = self.loop.call_soon_threadsafe(fn, *args)

    def when_ready(self, fn: Callable[[], None]) -> None:
        """Calls `fn` on the server loop: now when the server runs, else once it is ready."""
        if self.loop is not None:
            fn()
            return

        def ready(**_: object) -> None:
            fn()

        _ = self.ctrl.on_server_ready.add(ready)

    def authored_layout(self) -> dock.Layout:
        """The dockview layout of the authored Split; RuntimeError before `add`."""
        if self.root is None:
            raise RuntimeError("add() a Split before the dock is ready")

        return dock.layout_json(self.root)

    @guard
    def on_dock_ready(self) -> None:
        """Restores the saved arrangement when it names the authored panels, else applies the authored one; add_panel then fills any gap."""
        if self.dock is None or self.root is None:
            return

        saved = dock.restorable(self.persist.load(self.scope, StateKey.LAYOUT), self.panel_ids)
        self.dock.restore_layout(saved if saved is not None else self.authored_layout())

        self.place(self.root)
        for opened in self.opened.values():
            self.place(opened.root, opened.reference)

        log.bind(workbench=self.name, restored=saved is not None).info("Dock ready.")

    @guard
    def on_layout_changed(self, layout: Mapping[str, object]) -> None:
        """Persists the layout the dock reports, forgets the cards it no longer shows and tells every panel."""
        self.persist.save(self.scope, StateKey.LAYOUT, layout)
        self.prune_opened(layout)

        for panel in self.panels:
            panel.on_layout_changed()

    @guard
    def on_panel_removed(self, event: Mapping[str, object]) -> None:
        """Disposes the panel whose tab the user closed; the dock reports only a user close here, never its own teardown, and Reset layout builds a closed panel again."""
        panel_id = str(event.get("id"))
        if panel_id not in self.panel_ids:
            return

        self.pending.discard(panel_id)
        self.on_screen.discard(panel_id)
        self.retire([panel_id])
        log.bind(workbench=self.name, panel=panel_id).info("Panel closed.")

    def rebuild_closed(self) -> None:
        """Rebuilds every card a closed tab left incomplete, header widgets included, so an arrangement naming its panels finds them live."""
        cards: list[tuple[str | None, Opened]] = [(first, opened) for first, opened in self.opened.items()]
        if self.authored is not None:
            cards.insert(0, (None, self.authored))

        for first, card in cards:
            ids = dock.panel_ids(card.root)
            if all(panel_id in self.panel_ids for panel_id in ids):
                continue

            rebuilt = replace(card, header=self.swap(card.root, ids, card.header))
            if first is None:
                self.authored = rebuilt
            else:
                self.opened[first] = rebuilt

            log.bind(workbench=self.name, panels=ids).info("Closed panels rebuilt.")

    @guard
    def on_panel_visibility(self, change: Mapping[str, object]) -> None:
        """Records that the dock showed or hid a panel and tells that panel."""
        panel_id = str(change.get("id"))
        visible = bool(change.get("visible"))
        if visible:
            self.on_screen.add(panel_id)
        else:
            self.on_screen.discard(panel_id)

        for panel in self.panels:
            if panel.id == panel_id:
                panel.on_visible(visible)

    @guard
    def reset_layout(self) -> None:
        """Rebuilds the authored arrangement, which also recovers a group orphaned by a killed popout window."""
        if self.dock is None:
            return

        self.rebuild_closed()
        self.dock.restore_layout(self.authored_layout())
        for opened in self.opened.values():
            self.place(opened.root, opened.reference)

        self.status("layout reset")

    @guard
    def open_workbench(self, name: str) -> None:
        """Hands a catalogue selection to the open_workbench hook, or names the command that opens it."""
        spec = self.find_spec(name)
        if spec is None:
            self.status(f"unknown workbench {name}")
            return

        if spec.name == self.name:
            self.status(f"{spec.title} is open")
            return

        hook = self.ctrl[Hook.OPEN_WORKBENCH]
        if hook.exists():
            _ = hook(spec)
            return

        self.status(f"open with: {' '.join(spec.command())}")
        log.bind(workbench=spec.name, command=spec.command()).info("Workbench selected.")

    @life_cycle.server_ready
    def on_server_ready(self, **_: object) -> None:
        """Takes the server loop and opens the app window when one was asked for."""
        self.loop = asyncio.get_running_loop()
        if self.window is Window.APP:
            self.open_app_window()

    @life_cycle.server_exited
    def on_server_exited(self, **_: object) -> None:
        """Stops every reloader and flushes the persisted state."""
        for watch in self.watches:
            watch.reloader.stop()

        self.persist.flush()

    def open_app_window(self) -> None:
        """Chrome (Edge fallback) as an app window on its own profile; the server stops when the window process exits. Without either, a system browser tab."""
        url = f"http://localhost:{self.server.port}/"
        browser = next((path for path in BROWSERS if path.exists()), None)
        if browser is None:
            log.bind(url=url).warning("No Chrome or Edge found; opening the system browser.")
            _ = webbrowser.open(url)
            return

        profile = Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False)) / APP_PROFILE
        profile.mkdir(parents=True, exist_ok=True)
        command = [
            str(browser),
            f"--app={url}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={APP_WINDOW_SIZE}",
        ]
        process = subprocess.Popen(command)
        log.bind(browser=browser.name, pid=process.pid).info("App window opened.")

        def stop_with_window() -> None:
            code = process.wait()
            log.bind(returncode=code).info("App window closed; stopping the server.")
            if self.loop is not None:
                _ = asyncio.run_coroutine_threadsafe(self.server.stop(), self.loop)

        threading.Thread(target=stop_with_window, name="app-window", daemon=True).start()

    def install_break_handler(self) -> None:
        """Makes a break or termination signal a clean stop, so the exit hooks flush the persisted state; the --dev supervisor stops its child this way."""
        _ = signal.signal(signal.SIGTERM, self.on_break)
        if sys.platform == "win32":
            _ = signal.signal(signal.SIGBREAK, self.on_break)

    def on_break(self, signum: int, frame: FrameType | None) -> None:
        """Schedules the server's stop on its loop, which ends the main loop and runs the exit hooks."""
        loop = self.loop
        if loop is None:
            return

        def stop() -> None:
            _ = asyncio.ensure_future(self.server.stop())

        loop.call_soon_threadsafe(stop)

    def run(self, *, port: int = 8080, window: Window = Window.BROWSER) -> None:
        """Serves until stopped, or until an app window closes."""
        self.window = window
        # trame's own --app flag means pywebview; the app window is ours.
        sys.argv = [arg for arg in sys.argv if arg != APP_FLAG]
        self.install_break_handler()

        log.bind(workbench=self.name, port=port, window=str(window)).info("Starting the workbench.")
        _ = self.server.start(port=port, exec_mode="desktop" if window is Window.WEBVIEW else "main", open_browser=window is Window.BROWSER, timeout=0)
