"""A copper puck dissipates 1 W on an FR4 board that still air cools: solve the steady temperature in the background while a workbench shows it."""

from math import pi
from pathlib import Path

import build123d as b3d

import femforge as ff


HERE = Path(__file__).parent
RUNS = HERE / "runs"
TELEMETRY = HERE / "runs-index"

AMBIENT = 298.15
"""`T_amb` `[K]`: The air temperature, 25 °C."""

FILM = 10.0
"""`h` `[W/(m²·K)]`: The natural convection coefficient of still air."""

# The results spell body names in capitals, so these stay uppercase.
PUCK = "PUCK"
BOARD = "BOARD"

COPPER = ff.materials.Copper()
FR4 = ff.materials.FR4()


class PuckOnBoard(ff.Build123dGeometry):
    """A copper puck centered on a square FR4 board, the bottom of the board at Z = 0."""

    board_width: float = 60.0
    """`[mm]`: Side length of the square board."""

    board_thickness: float = 1.6
    """`[mm]`: Thickness of the board."""

    puck_diameter: float = 20.0
    """`[mm]`: Diameter of the copper puck."""

    puck_thickness: float = 4.0
    """`[mm]`: Thickness of the copper puck."""

    def puck_volume(self) -> float:
        """`V` `[m³]`: The volume the heat source fills."""
        return pi * (self.puck_diameter / 2) ** 2 * self.puck_thickness * 1e-9

    def model(self) -> ff.TaggedCompound:
        """The board with the puck on its top face."""
        board = b3d.Pos(0, 0, self.board_thickness / 2) * b3d.Box(self.board_width, self.board_width, self.board_thickness)
        puck = b3d.Pos(0, 0, self.board_thickness + self.puck_thickness / 2) * b3d.Cylinder(self.puck_diameter / 2, self.puck_thickness)

        return ff.TaggedCompound(
            children=[
                ff.TaggedPart([board], tags={ff.TagKey.MATERIAL: FR4.tag}, name=BOARD),
                ff.TaggedPart([puck], tags={ff.TagKey.MATERIAL: COPPER.tag}, name=PUCK),
            ]
        )


def config(power: float = 1.0) -> ff.SimulationConfig:
    """The steady heat conduction of the puck dissipating ``power`` watts, both bodies cooling into the air through their exposed faces."""
    geometry = PuckOnBoard()
    source = ff.HeatSource(power_density=power / geometry.puck_volume() * ff.U.W / ff.U.m**3)

    # Convection lands on the exposed faces only, so the puck-board interface keeps conducting.
    cooling = ff.Convection(coefficient=FILM * ff.U.W / (ff.U.m**2 * ff.U.K), ext_temperature=AMBIENT * ff.U.K)

    return ff.SimulationConfig(
        meta=ff.RunMeta(description=f"{power:g} W puck"),
        geometry=geometry,
        mesh=ff.MeshConfig(global_mesh_size=1 * ff.U.mm),
        physics=ff.PhysicsSelection(selected=ff.Physics.THERMAL),
        domain=ff.DomainConfig(
            materials=[COPPER, FR4],
            tags=[
                ff.EntityTag(selector={ff.TagKey.NAME: PUCK}, conditions=[source, cooling]),
                ff.EntityTag(selector={ff.TagKey.NAME: BOARD}, conditions=[cooling]),
            ],
        ),
    )


def solve() -> ff.Result:
    """Solve the 1 W puck in Docker and log its peak temperature and its rise over the air."""
    result = ff.Driver(runs_dir=RUNS, telemetry=TELEMETRY, docker=True).evaluate(config())

    if result.temperature_max is None:
        raise RuntimeError(f"run {result.run_id} reported no peak temperature")

    peak = result.temperature_max.m_as("kelvin")
    ff.log.success(f"Peak temperature {peak - 273.15:.1f} °C, {peak - AMBIENT:.1f} K above the air, run {result.run_id}")

    return result


def solves() -> list[ff.workbench.Task]:
    """The 1 W solve as a task, which the workbench runs in the background and again after every save of this file."""
    return [ff.workbench.Task("Puck at 1 W", solve)]


def viewer() -> ff.workbench.Split:
    """The field viewer coloring the temperature; it opens the newest run as soon as one lands."""
    display = ff.DisplaySpec(field="temperature", colormap="inferno")

    return ff.workbench.field_viewer(display=display, telemetry=TELEMETRY)


if __name__ == "__main__":
    # Functions, not a built dock and a list, so every save of this file rebuilds the workbench and solves the edit.
    ff.workbench.show(viewer, tasks=solves, title="Thermal", window=ff.workbench.Window.BROWSER)
