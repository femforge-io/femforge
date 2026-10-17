"""A magnet holds a steel bearing in its seat: compare the pull with and without a steel shell in 2D, check one case in 3D, and watch the runs land in a workbench."""

import threading

import femforge as ff
from geometry import BearingSeat, EntityNames
from paths import RUNS_DIR, TELEMETRY_DIR
from ui import workbench_ui


def config_for(
    as_2d: bool = False,
    magnet_thickness: float = 2.0,
    airgap: float = 0.25,
    mesh_size: float = 1.0,
    domain_padding: float = 70.0,
    shell_thickness: float | None = None,
    description: str = "Bearing Seat",
) -> ff.SimulationConfig:
    """A magnetostatic solve of the bearing seat, in 3D or as its axisymmetric 2D section.

    Args:
        as_2d: Solve the axisymmetric section about Z instead of the 3D model.
        magnet_thickness: Axial thickness of the magnet disc, in millimetres.
        airgap: Lift of the ball off its seat, in millimetres.
        mesh_size: Element size on the seat, the ball and the shell, in millimetres; the air grows five times coarser.
        domain_padding: Air around the bodies, in millimetres.
        shell_thickness: Wall thickness of the steel shell in millimetres; None for no shell.
        description: The run description the workbench lists.
    """
    geometry = BearingSeat(
        outer_diameter=15.0,
        inner_diameter=3.5,
        thickness=magnet_thickness,
        bearing_diameter=12.0,
        seating_diameter=9.0,
        seating_airgap=airgap,
        domain_padding=domain_padding,
        shell_thickness=shell_thickness,
    )

    # A uniform fine mesh fills the 3D air box with millions of elements; the field that sets the pull lives in and around the bodies.
    body_size = mesh_size * ff.U.mm
    air_size = body_size * 5

    mesh = ff.MeshConfig(
        global_mesh_size=air_size,
        gmsh_options=ff.GmshOptions(mesh_size_max=(air_size * 2).m_as("m")),
    )

    domain = ff.DomainConfig(
        materials=[ff.materials.N52(), ff.materials.Steel_1080(), ff.materials.Air()],
        tags=[
            ff.EntityTag(
                selector={ff.TagKey.NAME: EntityNames.SEAT},
                conditions=[ff.Magnetization(direction=ff.Direction(0, 0, 1))],
                mesh_size=body_size,
            ),
            ff.EntityTag(
                selector={ff.TagKey.NAME: EntityNames.BEARING},
                conditions=[ff.ForceComponent(calculate_magnetic_force=True)],
                mesh_size=body_size,
            ),
            ff.EntityTag(
                selector={ff.TagKey.NAME: EntityNames.SHELL},
                conditions=[ff.ForceComponent(calculate_magnetic_force=True)],
                mesh_size=body_size,
            ),
        ],
    )

    return ff.SimulationConfig(
        meta=ff.RunMeta(description=description),
        geometry=geometry,
        frame=ff.Axisymmetric() if as_2d else ff.Solid(),
        mesh=mesh,
        physics=ff.PhysicsSelection(selected=ff.Physics.MAGNETOSTATICS),
        domain=domain,
    )


def evaluate() -> ff.Driver:
    """Solve 2, 4 and 8 mm magnets with and without the shell in 2D, then the 4 mm magnet with the shell in 3D, logging the bearing's axial pull."""
    driver = ff.Driver(runs_dir=RUNS_DIR, telemetry=TELEMETRY_DIR, docker=True)

    def bearing_pull(magnet_thickness: float, shell_thickness: float | None) -> float:
        shell = "with" if shell_thickness is not None else "without"

        config = config_for(
            as_2d=True,
            magnet_thickness=magnet_thickness,
            mesh_size=0.5,
            shell_thickness=shell_thickness,
            description=f"{magnet_thickness:g} mm magnet {shell} shell",
        )

        result = driver.evaluate(config)

        return result.axial_force(EntityNames.BEARING).m_as("newton")

    lines = [f"{'magnet [mm]':>12} {'with shell [N]':>15} {'without [N]':>12} {'change':>8}"]

    for magnet_thickness in (2.0, 4.0, 8.0):
        with_shell = bearing_pull(magnet_thickness, shell_thickness=2.0)
        without_shell = bearing_pull(magnet_thickness, shell_thickness=None)

        change = with_shell / without_shell - 1

        lines.append(f"{magnet_thickness:>12g} {with_shell:>15.3f} {without_shell:>12.3f} {change:>8.1%}")

    ff.log.success("Evaluation complete!")
    ff.log.info("Bearing axial pull, with vs without the shell:\n" + "\n".join(lines))

    # A 3D run has no symmetry axis, so the axial pull projects onto Z.
    config_3d = config_for(
        as_2d=False,
        magnet_thickness=4.0,
        mesh_size=1.0,
        shell_thickness=2.0,
        description="4 mm magnet with shell (3D)",
    )
    result_3d = driver.evaluate(config_3d)
    pull_3d = result_3d.axial_force(EntityNames.BEARING, ff.Axis.Z).m_as("newton")
    ff.log.info(f"3D evaluation axial pull: {pull_3d:.3f} N")

    return driver


def main() -> None:
    """Solve the shell comparison in the background while the workbench shows the runs as they land."""
    threading.Thread(target=evaluate, daemon=True).start()

    # A factory, not a built Split, so the workbench rebuilds the UI on every save of ui.py.
    ff.workbench.show(workbench_ui, title="Bearing Seat Workbench", window=ff.workbench.Window.BROWSER)


if __name__ == "__main__":
    main()
