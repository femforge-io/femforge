"""The workbench API from the femforge-workbench package, which the workbench extra installs.

Without the extra, importing this module, and so reading ``femforge.workbench``, raises an ImportError that names the install command.
"""

from importlib.util import find_spec


try:
    from femforge_workbench.app import Window, Workbench
    from femforge_workbench.dock import Panel, Split, Tabs
    from femforge_workbench.panels import PanelComponent
    from femforge_workbench.panels.browsers import artifact_store
    from femforge_workbench.panels.field_viewer import FieldControlsPanel, FieldViewerPanel, workbench as field_viewer
    from femforge_workbench.panels.figure import FigurePanel
    from femforge_workbench.panels.runs import RunBrowserPanel, RunDetailPanel
    from femforge_workbench.panels.table import TablePanel
    from femforge_workbench.serve import show
except ModuleNotFoundError as error:
    if find_spec("femforge_workbench") is not None:
        raise

    raise ImportError('femforge.workbench needs the workbench extra: pip install "femforge[workbench]"') from error


__all__ = [
    "FieldControlsPanel",
    "FieldViewerPanel",
    "FigurePanel",
    "Panel",
    "PanelComponent",
    "RunBrowserPanel",
    "RunDetailPanel",
    "Split",
    "TablePanel",
    "Tabs",
    "Window",
    "Workbench",
    "artifact_store",
    "field_viewer",
    "show",
]
