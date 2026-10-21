"""The launcher panel: the catalogue as cards that open docks and renderables and run and stop scripts."""

import asyncio
import json
from enum import StrEnum
from typing import TYPE_CHECKING

from trame.widgets import html, react

from femforge.common.log import log
from femforge_workbench.app import Hook, StateVar as AppVar, guard
from femforge_workbench.panels import Cls, PanelComponent
from femforge_workbench.panels.log import LogPanel
from femforge_workbench.panels.outputs import WorkbenchOutputsPanel
from femforge_workbench.panels.scene import SceneViewerPanel
from femforge_workbench.registry import WorkbenchKind, WorkbenchSpec
from femforge_workbench.runner import ScriptRun, ScriptRunner, ScriptStatus
from femforge_workbench.widgets.badge import Badge
from femforge_workbench.widgets.button import Button
from femforge_workbench.widgets.card import Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle
from femforge_workbench.widgets.icon import Icon
from femforge_workbench.widgets.tip import Tip


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


ANSI_BOLD = "\x1b[1m"
ANSI_RESET = "\x1b[0m"
UNGROUPED = "Other"
LAST_RUN_FORMAT = "%Y-%m-%d %H:%M"
GROUP = "group"
CARD = "card"
CARD_ARGS = f"[{CARD}.name]"


class LauncherPanelId(StrEnum):
    """The panel ids of the launcher dock."""

    LAUNCHER = "launcher"
    SCENE = "scene"
    OUTPUTS = "outputs"
    LOG = "log"


class StateVar(StrEnum):
    """The per-panel state variables the cards bind."""

    GROUPS = "groups"
    TASKS = "tasks"
    CAPTION = "caption"


class GroupField(StrEnum):
    """The keys of a card group."""

    TITLE = "title"
    CARDS = "cards"


class CardField(StrEnum):
    """The keys of a card."""

    NAME = "name"
    TITLE = "title"
    DESCRIPTION = "description"
    KIND = "kind"
    LAST_RUN = "last_run"
    OUTPUTS = "outputs"
    RUNNING = "running"
    STATUS = "status"
    PID = "pid"


def field(name: CardField) -> str:
    """The JS expression of a card field inside the cards loop."""
    return f"{CARD}.{name}"


class LauncherPanel(PanelComponent):
    """Cards grouped by catalogue group; Open, Close, Run, Stop and Reload act through the shell and the scene panel beside it."""

    def __init__(self, app: "Workbench", id: str, title: str) -> None:
        """Builds the cards and takes the shell's open-workbench hook.

        Args:
            app: The workbench shell the panel is built in.
            id: The panel id, which names its template and namespaces its state.
            title: The panel's tab title.
        """
        self.runner = ScriptRunner(self.on_line, self.on_task)
        self.jobs: dict[str, asyncio.Task[ScriptRun]] = {}
        self.stops: set[asyncio.Task[None]] = set()

        super().__init__(app, id, title)

        app.ctrl[Hook.OPEN_WORKBENCH] = self.open

    @property
    def scene(self) -> SceneViewerPanel:
        """The scene panel beside the cards, which shows a renderable card."""
        return self.app.panel(LauncherPanelId.SCENE, SceneViewerPanel)

    @property
    def outputs(self) -> WorkbenchOutputsPanel:
        """The outputs panel beside the cards."""
        return self.app.panel(LauncherPanelId.OUTPUTS, WorkbenchOutputsPanel)

    @property
    def log(self) -> LogPanel:
        """The log panel beside the cards, which shows the scripts' output."""
        return self.app.panel(LauncherPanelId.LOG, LogPanel)

    def build(self) -> None:
        """Builds the toolbar over the cards, grouped by catalogue group."""
        is_script = f"{field(CardField.KIND)} === {json.dumps(WorkbenchKind.SCRIPT)}"
        is_renderable = f"{field(CardField.KIND)} === {json.dumps(WorkbenchKind.RENDERABLE)}"
        is_open = f"{AppVar.OPENED}.includes({field(CardField.NAME)})"
        failed = f"{field(CardField.STATUS)} === {json.dumps(ScriptStatus.FAILED)}"
        status_variant = f"{field(CardField.RUNNING)} ? 'default' : ({failed} ? 'destructive' : 'secondary')"

        with html.Div(classes=str(Cls.TOOLBAR)):
            html.Span([react.Bind(self.key(StateVar.CAPTION))], classes=str(Cls.CAPTION))
            html.Span(classes=str(Cls.SPACER))
            with (
                Tip(content="Refresh the cards"),
                Button(
                    size="icon-xs",
                    variant="ghost",
                    aria_label="Refresh the cards",
                    on_click=self.callback(self.push),
                ),
            ):
                Icon("refresh-cw")

        with html.Div(classes=f"{Cls.FILL} {Cls.SCROLL}"), react.For(items=self.key(StateVar.GROUPS), name=GROUP):
            html.Div([react.Bind(f"{GROUP}.{GroupField.TITLE}")], classes=str(Cls.GROUP_TITLE))
            with html.Div(classes=str(Cls.CARDS)), react.For(items=f"{GROUP}.{GroupField.CARDS}", name=CARD), Card(classes=str(Cls.CARD)):
                with CardHeader(classes=str(Cls.CARD_SECTION)):
                    with CardTitle(classes=str(Cls.CARD_TITLE)):
                        html.Span([react.Bind(field(CardField.TITLE))])
                        Badge([react.Bind(field(CardField.KIND))], variant="outline")
                        with react.If(field(CardField.STATUS)):
                            Badge([react.Bind(field(CardField.STATUS))], variant=react.Bind(status_variant))

                    CardDescription([react.Bind(field(CardField.DESCRIPTION))])

                with CardContent(classes=str(Cls.CARD_META)):
                    with react.If(field(CardField.LAST_RUN)):
                        html.Span(["last run ", react.Bind(field(CardField.LAST_RUN))])

                    with react.If(f"!{field(CardField.LAST_RUN)} && {is_script}"):
                        html.Span("Never Run")

                    with react.If(field(CardField.OUTPUTS)):
                        html.Span([react.Bind(field(CardField.OUTPUTS)), " outputs"])

                    with react.If(field(CardField.RUNNING)):
                        html.Span(["pid ", react.Bind(field(CardField.PID))])

                with CardFooter(classes=str(Cls.CARD_ACTIONS)):
                    with react.If(f"!({is_script}) && !({is_open})"):
                        Button("Open", size="xs", variant="secondary", on_click=self.callback(self.on_open, args=CARD_ARGS))

                    with react.If(is_open):
                        Button("Close", size="xs", variant="destructive", on_click=self.callback(self.on_close, args=CARD_ARGS))

                    with react.If(f"{is_script} && !{field(CardField.RUNNING)}"):
                        Button("Run", size="xs", variant="secondary", on_click=self.callback(self.on_run, args=CARD_ARGS))

                    with react.If(field(CardField.RUNNING)):
                        Button("Stop", size="xs", variant="destructive", on_click=self.callback(self.on_stop, args=CARD_ARGS))

                    with react.If(field(CardField.OUTPUTS)):
                        Button("Outputs", size="xs", variant="ghost", on_click=self.callback(self.on_outputs, args=CARD_ARGS))

                    with react.If(is_renderable):
                        Button("Reload", size="xs", variant="ghost", on_click=self.callback(self.on_reload, args=CARD_ARGS))

        self.state.update({self.key(StateVar.GROUPS): self.groups(), self.key(StateVar.TASKS): [], self.key(StateVar.CAPTION): self.caption()})

    def specs(self) -> list[WorkbenchSpec]:
        """The catalogue without the launcher itself."""
        return [spec for spec in self.app.catalogue if spec.name != self.app.name]

    def spec(self, name: str) -> WorkbenchSpec:
        """The catalogue card of that name; KeyError for an unknown one."""
        found = next((spec for spec in self.app.catalogue if spec.name == name), None)
        if found is None:
            raise KeyError(f"unknown workbench {name!r}")

        return found

    def card(self, spec: WorkbenchSpec) -> dict[str, object]:
        """A card's state entry: its catalogue fields, last run, outputs and task status."""
        task = self.runner.tasks.get(spec.name)
        last = spec.last_run()

        return {
            CardField.NAME: spec.name,
            CardField.TITLE: spec.title,
            CardField.DESCRIPTION: spec.description,
            CardField.KIND: str(spec.kind),
            CardField.LAST_RUN: last.strftime(LAST_RUN_FORMAT) if last is not None else "",
            CardField.OUTPUTS: len(spec.outputs),
            CardField.RUNNING: task is not None and task.status is ScriptStatus.RUNNING,
            CardField.STATUS: str(task.status) if task is not None else "",
            CardField.PID: task.pid if task is not None else None,
        }

    def groups(self) -> list[dict[str, object]]:
        """The cards by catalogue group, in catalogue order."""
        specs = self.specs()
        titles = list(dict.fromkeys(spec.group for spec in specs))

        return [{GroupField.TITLE: title or UNGROUPED, GroupField.CARDS: [self.card(spec) for spec in specs if spec.group == title]} for title in titles]

    def caption(self) -> str:
        """The toolbar caption: how many workbenches there are and how many scripts run."""
        running = sum(1 for task in self.runner.tasks.values() if task.status is ScriptStatus.RUNNING)
        suffix = f", {running} running" if running else ""

        return f"{len(self.specs())} workbenches{suffix}"

    def push(self) -> None:
        """Rebuilds the cards and the task rows in the state."""
        with self.state:
            self.state[self.key(StateVar.GROUPS)] = self.groups()
            self.state[self.key(StateVar.TASKS)] = self.runner.rows()
            self.state[self.key(StateVar.CAPTION)] = self.caption()

    def activate(self, panel_id: LauncherPanelId) -> None:
        """Brings a launcher panel's tab to the front."""
        if self.app.dock is not None:
            self.app.dock.active_panel(str(panel_id))

    @guard
    def open(self, spec: WorkbenchSpec) -> None:
        """Opens a card: a dock's panels join the dock, a renderable goes to the scene, a script runs."""
        match spec.kind:
            case WorkbenchKind.PANEL:
                self.open_dock(spec)
            case WorkbenchKind.RENDERABLE:
                self.scene.show(spec)
                self.activate(LauncherPanelId.SCENE)
            case WorkbenchKind.SCRIPT:
                self.run(spec)

    def open_dock(self, spec: WorkbenchSpec) -> None:
        """Opens a dock card's panels in the scene panel's group."""
        self.app.open_spec(spec, reference=str(LauncherPanelId.SCENE))
        self.status(f"{spec.title} opened")

    def run(self, spec: WorkbenchSpec) -> None:
        """Starts the script on the server loop; its lines go to the log panel."""
        if spec.kind is not WorkbenchKind.SCRIPT:
            raise ValueError(f"{spec.name} is not a script")

        if self.runner.running(spec.name):
            self.status(f"{spec.title} is running")
            return

        job = asyncio.get_running_loop().create_task(self.runner.run(spec.name, spec.command(), cwd=spec.cwd))
        job.add_done_callback(self.on_job_done)
        self.jobs[spec.name] = job
        self.activate(LauncherPanelId.LOG)

    def on_job_done(self, job: asyncio.Task[ScriptRun]) -> None:
        """Reports a run that failed to start or stream, which no task row would show."""
        error = job.exception()
        if error is None:
            return

        log.opt(exception=error).warning("Script run failed.")
        self.status(f"{type(error).__name__}: {error}")
        self.push()

    @guard
    def on_open(self, name: str) -> None:
        """Opens the card the Open button names."""
        self.open(self.spec(name))

    @guard
    def on_close(self, name: str) -> None:
        """Closes the card the Close button names."""
        self.app.close(name)
        self.status(f"{self.spec(name).title} closed")

    @guard
    def on_run(self, name: str) -> None:
        """Runs the script the Run button names."""
        self.run(self.spec(name))

    @guard
    def on_stop(self, name: str) -> None:
        """Stops the script the Stop button names, without waiting for it."""
        stop = asyncio.get_running_loop().create_task(self.runner.stop(name))
        stop.add_done_callback(self.stops.discard)
        self.stops.add(stop)
        self.status(f"stopping {name}")

    @guard
    def on_outputs(self, name: str) -> None:
        """Shows a card's outputs in the outputs panel."""
        self.outputs.show(self.spec(name))
        self.activate(LauncherPanelId.OUTPUTS)

    @guard
    def on_reload(self, name: str) -> None:
        """Purges and rebuilds a renderable, showing it first when the scene holds another."""
        spec = self.spec(name)
        scene = self.scene
        if scene.spec == spec:
            scene.reload()
        else:
            scene.show(spec)

        self.activate(LauncherPanelId.SCENE)

    def on_line(self, name: str, line: str) -> None:
        """Writes a script's output line to the log panel."""
        self.log.write(line)

    def on_task(self, task: ScriptRun) -> None:
        """A run started or ended: the log gets a marker line, the cards and rows follow, the outputs shown refresh."""
        if task.status is ScriptStatus.RUNNING:
            self.log.write(f"{ANSI_BOLD}>>> {task.name} (pid {task.pid}){ANSI_RESET}")
        else:
            self.log.write(f"{ANSI_BOLD}<<< {task.name} {task.status} (exit {task.returncode}){ANSI_RESET}")
            outputs = self.outputs
            if outputs.spec is not None and outputs.spec.name == task.name:
                outputs.reload()

        self.push()
