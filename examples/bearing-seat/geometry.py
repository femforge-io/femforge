"""The bearing seat: a magnet disc with a spherical seat, the steel ball it holds, and an optional steel shell."""

from math import sqrt

import build123d as b3d

import femforge as ff


class EntityNames:
    """Body names shared by the solve setup and the workbench."""

    SEAT = "SEAT"
    BEARING = "BEARING"
    SHELL = "SHELL"


SHELL_GAP = 0.1
"""`[mm]`: Clearance between the shell and the seat."""


class BearingSeat(ff.Build123dGeometry):
    """A magnet disc with a spherical seat holding a steel bearing ball, optionally cupped in a steel shell; Z is the axis of symmetry."""

    outer_diameter: float
    """`[mm]`: Outer diameter of the magnet disc."""

    inner_diameter: float
    """`[mm]`: Bore diameter of the magnet disc."""

    thickness: float
    """`[mm]`: Axial thickness of the magnet disc."""

    bearing_diameter: float
    """`[mm]`: Diameter of the bearing ball."""

    seating_diameter: float
    """`[mm]`: Diameter where the ball meets the disc top, measured like a countersink."""

    seating_airgap: float
    """`[mm]`: Extra lift of the ball off its seat, emulating misalignment or surface roughness."""

    domain_padding: float
    """`[mm]`: Air added around the bodies to bound the simulation domain."""

    shell_thickness: float | None = None
    """`[mm]`: Wall thickness of the steel shell around the disc; None for no shell."""

    def disc_face(self) -> b3d.Face:
        """The annular disc profile."""
        return b3d.Face(
            outer_wire=b3d.Wire.make_circle(self.outer_diameter / 2),
            inner_wires=[b3d.Wire.make_circle(self.inner_diameter / 2)],
        )

    def seat_part(self) -> b3d.Part:
        """The magnet disc, from Z = 0 up, with the ball cut out of its top."""
        disc = b3d.extrude(self.disc_face(), self.thickness)
        part = disc - self.bearing_part()

        assert isinstance(part, b3d.Part), "Failed to create the bearing seat part"

        return part

    def bearing_part(self) -> b3d.Part:
        """The ball, resting with its surface on the disc top at the seating diameter."""
        sphere = b3d.Sphere(self.bearing_diameter / 2)
        center_height = self.thickness + sqrt((self.bearing_diameter / 2) ** 2 - (self.seating_diameter / 2) ** 2)

        return sphere.located(b3d.Location((0, 0, center_height)))

    def shell_part(self, thickness: float) -> b3d.Part:
        """A cup of wall ``thickness`` around the disc's side and bottom, ``SHELL_GAP`` away from it."""
        shell_outer_diameter = self.outer_diameter + 2 * (thickness + SHELL_GAP)
        shell_inner_diameter = self.outer_diameter + 2 * SHELL_GAP

        ring = b3d.Face(
            outer_wire=b3d.Wire.make_circle(shell_outer_diameter / 2),
            inner_wires=[b3d.Wire.make_circle(shell_inner_diameter / 2)],
        )
        sides = b3d.extrude(ring, self.thickness + SHELL_GAP)

        assert isinstance(sides, b3d.Part), "Failed to create the shell part"

        bottom_face = b3d.Face(outer_wire=b3d.Wire.make_circle(shell_outer_diameter / 2))
        bottom = b3d.extrude(bottom_face, -thickness)
        cup = sides + bottom

        return b3d.Part([cup]).located(b3d.Location((0, 0, -SHELL_GAP)))

    def bodies(self) -> list[ff.TaggedPart]:
        """The seat, the ball lifted by ``seating_airgap`` and, when ``shell_thickness`` is set, the shell, each tagged with its material and name."""
        seat = ff.TaggedPart([self.seat_part()], tags={ff.TagKey.MATERIAL: ff.materials.N52().tag}, name=EntityNames.SEAT)

        lifted_ball = self.bearing_part().translate((0, 0, self.seating_airgap))
        bearing = ff.TaggedPart([lifted_ball], tags={ff.TagKey.MATERIAL: ff.materials.Steel_1080().tag}, name=EntityNames.BEARING)

        bodies = [seat, bearing]

        if self.shell_thickness is not None:
            shell = ff.TaggedPart([self.shell_part(self.shell_thickness)], tags={ff.TagKey.MATERIAL: ff.materials.Steel_1080().tag}, name=EntityNames.SHELL)
            bodies.append(shell)

        return bodies

    def model(self) -> ff.TaggedCompound:
        """The bodies inside a padded cylinder of air."""
        bodies = self.bodies()
        cylinder = ff.boundary_cylinder(ff.TaggedCompound(children=bodies), padding=self.domain_padding)
        air = ff.TaggedPart([cylinder], tags={ff.TagKey.MATERIAL: ff.materials.Air().tag})

        return ff.TaggedCompound(children=[*bodies, air])
