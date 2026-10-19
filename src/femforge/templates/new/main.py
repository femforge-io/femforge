# /// script
# requires-python = ">=3.12"
# dependencies = ["femforge[workbench]~=0.1.0"]
# ///
"""A disc magnet pulls on a steel plate across an air gap: solve four gaps in the background while a workbench shows each run as it lands (``--solve-only`` skips the workbench)."""

import sys
from functools import partial
from pathlib import Path

import build123d as b3d

import femforge as ff


HERE = Path(__file__).parent
RUNS = HERE / "runs"
TELEMETRY = HERE / "runs-index"

GAPS = (0.5, 1.0, 2.0, 4.0)
"""`[mm]`: The air gaps to solve."""

# The results spell body names in capitals, so these stay uppercase.
MAGNET = "MAGNET"
PLATE = "PLATE"

N52 = ff.materials.N52()
STEEL = ff.materials.Steel_1080()
AIR = ff.materials.Air()


class MagnetAndPlate(ff.Build123dGeometry):
    """A disc magnet with a steel plate above it, both centered on the Z axis, inside a cylinder of air."""

    gap: float
    """`[mm]`: Air gap between the top of the magnet and the bottom of the plate."""

    magnet_diameter: float = 20.0
    """`[mm]`: Diameter of the magnet disc."""

    magnet_thickness: float = 5.0
    """`[mm]`: Thickness of the magnet disc."""

    plate_diameter: float = 40.0
    """`[mm]`: Diameter of the steel plate."""

    plate_thickness: float = 5.0
    """`[mm]`: Thickness of the steel plate."""

    padding: float = 100.0
    """`[mm]`: Air added around the bodies, half on each side."""

    def model(self) -> ff.TaggedCompound:
        """The magnet from Z = 0 up, the plate ``gap`` above it, and the air around both."""

        def disc(diameter: float, thickness: float, bottom: float) -> b3d.Part:
            return b3d.Pos(0, 0, bottom + thickness / 2) * b3d.Cylinder(diameter / 2, thickness)

        magnet = ff.TaggedPart([disc(self.magnet_diameter, self.magnet_thickness, 0)], tags={ff.TagKey.MATERIAL: N52.tag}, name=MAGNET)
        plate = ff.TaggedPart([disc(self.plate_diameter, self.plate_thickness, self.magnet_thickness + self.gap)], tags={ff.TagKey.MATERIAL: STEEL.tag}, name=PLATE)

        air = ff.TaggedPart([ff.boundary_cylinder(ff.TaggedCompound(children=[magnet, plate]), padding=self.padding)], tags={ff.TagKey.MATERIAL: AIR.tag})

        return ff.TaggedCompound(children=[magnet, plate, air])


def config(gap: float = 1.0) -> ff.SimulationConfig:
    """The magnetostatic solve of the magnet and the plate ``gap`` mm apart, on the axisymmetric section about Z."""
    fine = 0.2 * ff.U.mm

    return ff.SimulationConfig(
        meta=ff.RunMeta(description=f"{gap:g} mm gap"),
        geometry=MagnetAndPlate(gap=gap),
        frame=ff.Axisymmetric(),
        mesh=ff.MeshConfig(global_mesh_size=2 * ff.U.mm),
        physics=ff.PhysicsSelection(selected=ff.Physics.MAGNETOSTATICS),
        domain=ff.DomainConfig(
            materials=[N52, STEEL, AIR],
            tags=[
                ff.EntityTag(selector={ff.TagKey.NAME: MAGNET}, conditions=[ff.Magnetization(direction=ff.Direction(0, 0, 1))], mesh_size=fine),
                ff.EntityTag(selector={ff.TagKey.NAME: PLATE}, conditions=[ff.CalculateMagneticForce()], mesh_size=fine),
            ],
        ),
    )


def solve(gap: float) -> float:
    """`F` `[N]`: The axial force on the plate ``gap`` mm above the magnet, negative toward it; a gap solved before loads from ``runs/``."""
    result = ff.Driver(runs_dir=RUNS, telemetry=TELEMETRY).evaluate(config(gap))
    newtons = result.axial_force(PLATE).m_as("newton")
    ff.log.success(f"{gap:g} mm gap: {newtons:.1f} N on the plate, run {result.run_id}")

    return newtons


def solves() -> list[ff.workbench.Task]:
    """One task per gap, which the workbench solves in the background and lists again on every save of this file."""
    return [ff.workbench.Task(f"Gap {gap:g} mm", partial(solve, gap)) for gap in GAPS]


def viewer() -> ff.workbench.Split:
    """The field viewer coloring the flux density with the air hidden; it opens the newest run as soon as one lands."""
    display = ff.DisplaySpec(field="magnetic flux density", mode=ff.DisplayMode.MAGNITUDE, hidden_bodies=("AIR",))

    return ff.workbench.field_viewer(display=display, telemetry=TELEMETRY)


if __name__ == "__main__":
    if "--solve-only" in sys.argv[1:]:
        for task in solves():
            _ = task.run()
    else:
        # Functions, not a built dock and a list, so every save of this file rebuilds the workbench and solves the edited gaps.
        ff.workbench.show(viewer, tasks=solves, title="Magnet and Plate", window=ff.workbench.Window.BROWSER)
