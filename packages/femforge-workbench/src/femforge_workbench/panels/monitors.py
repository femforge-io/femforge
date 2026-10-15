"""The live monitors workbench: the run monitor beside the sweep monitor, over the log browser and the terminal as tabs."""

from enum import StrEnum
from typing import TYPE_CHECKING

from femforge.telemetry.tap import LiveLogTap
from femforge_workbench.dock import Panel, Split, Tabs
from femforge_workbench.panels.browsers import telemetry
from femforge_workbench.panels.log_browser import LogBrowserPanel
from femforge_workbench.panels.run_monitor import RunMonitorPanel
from femforge_workbench.panels.sweep_monitor import SweepMonitorPanel
from femforge_workbench.panels.terminal import TerminalPanel
from femforge_workbench.registry import MONITORS


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


SPEC = MONITORS
"""The catalogue card this workbench is opened by."""

RUN_WEIGHT = 1.3


class MonitorPanel(StrEnum):
    """The four panel ids of the monitors dock."""

    RUN_MONITOR = "run_monitor"
    SWEEP_MONITOR = "sweep_monitor"
    LOG_BROWSER = "log_browser"
    TERMINAL = "terminal"


def workbench() -> Split:
    """The dock: the run monitor on the left; the sweep monitor over the log browser and terminal tabs on the right."""
    store = telemetry()
    tap = LiveLogTap()

    def run_monitor(app: "Workbench", id: str, title: str) -> RunMonitorPanel:
        panel = RunMonitorPanel(app, id, title, telemetry=store)
        with app.header:
            panel.build_picker()

        return panel

    def sweep_monitor(app: "Workbench", id: str, title: str) -> SweepMonitorPanel:
        panel = SweepMonitorPanel(app, id, title, telemetry=store)
        with app.header:
            panel.build_picker()

        return panel

    def log_browser(app: "Workbench", id: str, title: str) -> LogBrowserPanel:
        return LogBrowserPanel(app, id, title, telemetry=store, tap=tap)

    def terminal(app: "Workbench", id: str, title: str) -> TerminalPanel:
        return TerminalPanel(app, id, title, tap=tap)

    tabs = Tabs([Panel("Log Browser", log_browser, id=MonitorPanel.LOG_BROWSER), Panel("Terminal", terminal, id=MonitorPanel.TERMINAL)])

    return Split(
        [
            Panel("Run Monitor", run_monitor, id=MonitorPanel.RUN_MONITOR, weight=RUN_WEIGHT),
            Split([Panel("Sweep Monitor", sweep_monitor, id=MonitorPanel.SWEEP_MONITOR), tabs]),
        ]
    )
