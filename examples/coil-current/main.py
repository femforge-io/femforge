"""A solenoid in a steel frame pulls a steel plunger toward the frame's bottom plate: solve two gaps on the axisymmetric section, then browse the field in a workbench."""

from pathlib import Path

import build123d as b3d

import femforge as ff


HERE = Path(__file__).parent
RUNS = HERE / "runs"
TELEMETRY = HERE / "runs-index"

GAPS = (2.0, 4.0)
"""`[mm]`: The air gaps under the plunger to solve."""

CURRENT_DENSITY = 3e6
"""`J` `[A/m²]`: The coil's current density, 3 A/mm², a continuous rating for a small copper coil."""

# The results spell body names in capitals, so these stay uppercase.
COIL = "COIL"
FRAME = "FRAME"
PLUNGER = "PLUNGER"

COPPER = ff.materials.Copper()
STEEL = ff.materials.Steel_1080()
AIR = ff.materials.Air()


class Solenoid(ff.Build123dGeometry):
    """A coil inside a steel pot frame, and a steel plunger through the hole in the frame's top plate; Z is the axis of symmetry."""

    gap: float
    """`[mm]`: Air gap between the bottom of the plunger and the frame's bottom plate."""

    coil_inner_radius: float = 6.0
    """`[mm]`: Inner radius of the coil."""

    coil_outer_radius: float = 16.0
    """`[mm]`: Outer radius of the coil."""

    cavity_height: float = 20.0
    """`[mm]`: Height of the cavity the coil sits in, between the frame's plates."""

    frame_radius: float = 20.0
    """`[mm]`: Outer radius of the frame."""

    frame_wall: float = 3.0
    """`[mm]`: Thickness of the frame's wall and plates."""

    plunger_radius: float = 5.0
    """`[mm]`: Radius of the plunger."""

    plunger_length: float = 40.0
    """`[mm]`: Length of the plunger."""

    clearance: float = 0.5
    """`[mm]`: Radial clearance between the plunger and the hole in the top plate, and between the coil and the plates."""

    padding: float = 60.0
    """`[mm]`: Air added around the bodies, half on each side."""

    def model(self) -> ff.TaggedCompound:
        """The frame from Z = 0 up to the top plate, the coil in its cavity, the plunger ``gap`` above the bottom plate, and the air around them."""

        def cylinder(radius: float, bottom: float, top: float) -> b3d.Part:
            return b3d.Pos(0, 0, (bottom + top) / 2) * b3d.Cylinder(radius, top - bottom)

        height = self.cavity_height
        wall = self.frame_wall

        cavity = cylinder(self.frame_radius - wall, 0, height)
        hole = cylinder(self.plunger_radius + self.clearance, height - 1, height + wall + 1)
        frame = cylinder(self.frame_radius, -wall, height + wall) - cavity - hole

        coil_bottom, coil_top = self.clearance, height - self.clearance
        coil = cylinder(self.coil_outer_radius, coil_bottom, coil_top) - cylinder(self.coil_inner_radius, coil_bottom - 1, coil_top + 1)
        plunger = cylinder(self.plunger_radius, self.gap, self.gap + self.plunger_length)

        bodies = [
            ff.TaggedPart([frame], tags={ff.TagKey.MATERIAL: STEEL.tag}, name=FRAME),
            ff.TaggedPart([coil], tags={ff.TagKey.MATERIAL: COPPER.tag}, name=COIL),
            ff.TaggedPart([plunger], tags={ff.TagKey.MATERIAL: STEEL.tag}, name=PLUNGER),
        ]
        box = ff.boundary_box(ff.TaggedCompound(children=bodies), ff.Vec3(self.padding, self.padding, self.padding))

        return ff.TaggedCompound(children=[*bodies, ff.TaggedPart([box], tags={ff.TagKey.MATERIAL: AIR.tag})])


def config(gap: float = 2.0) -> ff.SimulationConfig:
    """The magnetostatic solve of the plunger ``gap`` mm above the bottom plate, on the axisymmetric section about Z."""
    fine = 0.25 * ff.U.mm

    return ff.SimulationConfig(
        meta=ff.RunMeta(description=f"{gap:g} mm gap"),
        geometry=Solenoid(gap=gap),
        frame=ff.Axisymmetric(),
        mesh=ff.MeshConfig(global_mesh_size=2 * ff.U.mm),
        physics=ff.PhysicsSelection(selected=ff.Physics.MAGNETOSTATICS),
        domain=ff.DomainConfig(
            materials=[COPPER, STEEL, AIR],
            tags=[
                ff.EntityTag(selector={ff.TagKey.NAME: COIL}, conditions=[ff.CurrentDensity(value=CURRENT_DENSITY * ff.U.A / ff.U.m**2)], mesh_size=2 * fine),
                ff.EntityTag(selector={ff.TagKey.NAME: FRAME}, mesh_size=fine),
                ff.EntityTag(selector={ff.TagKey.NAME: PLUNGER}, conditions=[ff.CalculateMagneticForce()], mesh_size=fine),
            ],
        ),
    )


def solve() -> None:
    """Solve every gap in Docker and log the axial force on the plunger, negative toward the bottom plate."""
    driver = ff.Driver(runs_dir=RUNS, telemetry=TELEMETRY, docker=True)

    for gap in GAPS:
        result = driver.evaluate(config(gap))
        newtons = result.axial_force(PLUNGER).m_as("newton")
        ff.log.success(f"{gap:g} mm gap: {newtons:.2f} N on the plunger, run {result.run_id}")


def viewer() -> ff.workbench.Split:
    """The field viewer on the newest run, coloring the flux density with the air hidden."""
    display = ff.DisplaySpec(field="magnetic flux density", mode=ff.DisplayMode.MAGNITUDE, hidden_bodies=("AIR",))
    runs = ff.find_runs(RUNS)

    return ff.workbench.field_viewer(runs[0] if runs else None, display=display, telemetry=TELEMETRY)


if __name__ == "__main__":
    solve()

    # A function, not a built dock, so every save of this file rebuilds the workbench.
    ff.workbench.show(viewer, title="Coil Current", window=ff.workbench.Window.BROWSER)
