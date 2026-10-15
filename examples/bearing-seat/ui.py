"""The bearing seat workbench: the run list beside the field viewer, with the selected run's details below it."""

import femforge as ff
from geometry import EntityNames
from paths import TELEMETRY_DIR


def workbench_ui() -> ff.workbench.Split:
    """The runs on the left; the field viewer and the run details stacked on the right, both following the selected run."""
    store = ff.Telemetry(TELEMETRY_DIR)
    artifacts = ff.workbench.artifact_store(store)

    display_defaults = ff.DisplaySpec(
        field="magnetic flux density",
        mode=ff.DisplayMode.MAGNITUDE,
        colormap="Linear Green (Gr4L)",
        clim=None,
        opacity_map=(0.05, 0.75),  # Weak field fades out, on the bodies and the field lines alike
        hidden_bodies=("AIR",),
        opacity=0.5,
        body_styles=(
            ff.BodyStyle(body=EntityNames.SEAT, paint=ff.BodyPaint.SOLID, color="#4C00FF"),
            ff.BodyStyle(body=EntityNames.BEARING, paint=ff.BodyPaint.SOLID, color="#C800FF"),
            ff.BodyStyle(body=EntityNames.SHELL, paint=ff.BodyPaint.SOLID, color="#00FFB3"),
        ),
        streamlines=ff.FieldLineOptions(seed_placement=ff.SeedPlacement.VOLUME, line_count=14, max_length=0.4),
    )

    def runs(app: ff.workbench.Workbench, id: str, title: str) -> ff.workbench.RunBrowserPanel:
        return ff.workbench.RunBrowserPanel(app, id, title, telemetry=store)

    def run_detail(app: ff.workbench.Workbench, id: str, title: str) -> ff.workbench.RunDetailPanel:
        return ff.workbench.RunDetailPanel(app, id, title, telemetry=store, store=artifacts)

    # Splits alternate their axis with depth, so the viewer nested two levels down keeps its controls beside the 3D view.
    return ff.workbench.Split(
        [
            ff.workbench.Panel("Runs", runs, id="runs-panel", weight=0.25),
            ff.workbench.Split(
                [
                    ff.workbench.field_viewer(display=display_defaults, telemetry=TELEMETRY_DIR),
                    ff.workbench.Panel("Run", run_detail, id="run-detail-panel", weight=0.5),
                ]
            ),
        ]
    )
