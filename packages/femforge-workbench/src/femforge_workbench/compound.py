"""A build123d Compound as a Renderable: instanced VTK actors with view-dependent silhouettes."""

import build123d as b3d
import pyvista as pv

from femforge_workbench.b3d_pv import Build123Instancer
from femforge_workbench.settings import Setting, enabled


EMPTY_TEXT = "Empty Compound"
EMPTY_LABEL = "Error"
EMPTY_FONT = "Arial"
EMPTY_FONT_SIZE = 5
VIEWER_EDGE_ACCURACY = 0.01
VIEWER_TREE_WIDTH = 350


class CompoundRenderable:
    """A Renderable over a build123d Compound; the push to the OCP CAD Viewer is opt-in through `ocp_vscode` or FEMFORGE_OCP_VSCODE."""

    def __init__(self, compound: b3d.Compound, *, ocp_vscode: bool | None = None) -> None:
        """Instances the compound, or a placeholder label when it is empty, and pushes it to the OCP CAD Viewer when asked."""

        def placeholder() -> b3d.Compound:
            """A red label standing in for a compound with nothing to draw."""
            text = b3d.Text(EMPTY_TEXT, font_size=EMPTY_FONT_SIZE, font=EMPTY_FONT)

            return b3d.Compound(children=[text], label=EMPTY_LABEL, color=b3d.Color(1, 0, 0, 1))

        self.compound = compound if compound.children else placeholder()  # pyright: ignore[reportUnknownMemberType]  # build123d ships no type information

        to_viewer = enabled(Setting.OCP_VSCODE) if ocp_vscode is None else ocp_vscode
        if to_viewer:
            try:
                from ocp_vscode import show  # pyright: ignore[reportUnknownVariableType]  # ocp_vscode ships no type information
            except ModuleNotFoundError as error:
                if error.name != "ocp_vscode":
                    raise

                raise ModuleNotFoundError('Showing compounds in the OCP CAD Viewer needs the ocp extra: pip install "femforge-workbench[ocp]".', name="ocp_vscode") from error

            show(
                self.compound,
                edge_accuracy=VIEWER_EDGE_ACCURACY,
                render_edges=True,
                tree_width=VIEWER_TREE_WIDTH,
                glass=False,
            )

        self.inst = Build123Instancer().parse(self.compound)

    def render(self, plotter: pv.Plotter) -> None:
        """Draw the compound into `plotter` under a top-down parallel projection; the plotter keeps its own background and anti-aliasing."""
        plotter.clear()
        plotter.suppress_rendering = True
        plotter.parallel_projection = True

        _ = self.inst.add_to_plotter(plotter)
        _ = self.inst.add_silhouettes(plotter)

        plotter.view_xy()  # pyright: ignore[reportCallIssue, reportUnknownMemberType]  # pyvista forwards the renderer method onto the plotter at runtime
        plotter.reset_camera()  # pyright: ignore[reportCallIssue, reportUnknownMemberType]

        plotter.suppress_rendering = False
        plotter.render()
