"""Bbox/center-of-mass helper: pure dataclass logic, no gmsh."""

from femforge.meshing.geometry_utils import EntityGeometry


def test_entity_geometry_extent():
    g = EntityGeometry(
        dim=3, tag=1,
        bbox_min=(0.0, 0.0, 0.0), bbox_max=(2.0, 4.0, 6.0),
        center_of_mass=(1.0, 2.0, 3.0), volume=48.0,
    )
    assert g.extent == (2.0, 4.0, 6.0)
    assert g.volume == 48.0
    assert g.center_of_mass == (1.0, 2.0, 3.0)
