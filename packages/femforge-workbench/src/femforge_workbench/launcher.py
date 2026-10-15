"""The built-in launcher dock: the catalogue cards on the left, the scene, the outputs and the log as tabs on the right."""

from typing import TYPE_CHECKING

from femforge_workbench.dock import Panel, Split, Tabs
from femforge_workbench.panels.launcher import LauncherPanel, LauncherPanelId
from femforge_workbench.panels.log import LogPanel
from femforge_workbench.panels.outputs import WorkbenchOutputsPanel
from femforge_workbench.panels.scene import SceneViewerPanel


if TYPE_CHECKING:
    from femforge_workbench.app import Workbench


CARDS_WEIGHT = 1.0
VIEWERS_WEIGHT = 1.6


def workbench() -> Split:
    """The launcher dock."""

    def launcher(app: "Workbench", id: str, title: str) -> LauncherPanel:
        return LauncherPanel(app, id, title)

    def scene(app: "Workbench", id: str, title: str) -> SceneViewerPanel:
        return SceneViewerPanel(app, id, title)

    def outputs(app: "Workbench", id: str, title: str) -> WorkbenchOutputsPanel:
        return WorkbenchOutputsPanel(app, id, title, catalogue=app.catalogue)

    def log(app: "Workbench", id: str, title: str) -> LogPanel:
        return LogPanel(app, id, title)

    viewers = Tabs(
        [
            Panel("Scene", scene, id=LauncherPanelId.SCENE),
            Panel("Outputs", outputs, id=LauncherPanelId.OUTPUTS),
            Panel("Log", log, id=LauncherPanelId.LOG),
        ],
        weight=VIEWERS_WEIGHT,
    )

    return Split([Panel("Launcher", launcher, id=LauncherPanelId.LAUNCHER, weight=CARDS_WEIGHT), viewers])
