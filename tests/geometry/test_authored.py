"""The authored-body tessellation a sectioning frame writes beside its solve STEP."""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import build123d as b3d
import pytest

from femforge.common.tags import TagKey
from femforge.common.vector import Direction, Vec3
from femforge.config import SimulationConfig
from femforge.frames import Axis, Cartesian2D, FrameGeometryError
from femforge.frames.base import authored_path
from femforge.geometry import authored
from femforge.geometry.authored import AUTHORED_BODY_ARRAY, tessellate_bodies, write_authored_geometry
from femforge.geometry.tags import TaggedCompound, TaggedPart
from femforge.physical.units import U
from femforge.run.bundle import write_manifest
from femforge.run.inspect import Run
from femforge.run.manifest import RunManifest
from femforge.run.result import RunStatus


FLAT = Cartesian2D(thickness=30 * U.mm)


def _box() -> TaggedCompound:
    part = TaggedPart([b3d.Box(10, 20, 30)], tags={TagKey.MATERIAL: "Steel316"}, name="Box")

    return TaggedCompound(children=[part])


def _cylinder() -> TaggedCompound:
    part = TaggedPart([b3d.Cylinder(10, 30)], tags={TagKey.MATERIAL: "Steel316"}, name="Rod")

    return TaggedCompound(children=[part])


def test_a_box_tessellates_into_twelve_triangles(tmp_path: Path) -> None:
    mesh = tessellate_bodies(_box(), FLAT.plane)
    path = write_authored_geometry(_box(), FLAT.plane, tmp_path / "geometry.authored.vtp")

    assert (len(mesh.triangles), len(mesh.points)) == (12, 24)
    assert mesh.bodies.tolist() == [0] * 12
    assert path is not None
    piece = ElementTree.parse(path).getroot().find("PolyData/Piece")
    assert piece is not None and (piece.get("NumberOfPoints"), piece.get("NumberOfPolys")) == ("24", "12")


def test_the_bodies_land_in_the_solve_frame(tmp_path: Path) -> None:
    tilted = Cartesian2D(normal=Axis(origin=Vec3(0, 0, 0), direction=Direction(1, 0, 0)), thickness=10 * U.mm)
    mesh = tessellate_bodies(_box(), tilted.plane)

    spans = cast("list[float]", (mesh.points.max(axis=0) - mesh.points.min(axis=0)).tolist())

    assert sorted(spans) == pytest.approx([10.0, 20.0, 30.0])
    assert spans[2] == pytest.approx(10.0)


def test_a_tessellation_over_the_limit_retries_coarser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(authored, "AUTHORED_ANGLE", 3.0)
    fine = len(tessellate_bodies(_cylinder(), FLAT.plane).triangles)
    monkeypatch.setattr(authored, "AUTHORED_TRIANGLE_LIMIT", fine - 1)

    path = write_authored_geometry(_cylinder(), FLAT.plane, tmp_path / "rod.vtp")

    assert path is not None
    piece = ElementTree.parse(path).getroot().find("PolyData/Piece")
    assert piece is not None and int(piece.get("NumberOfPolys", "0")) < fine


def test_a_tessellation_that_never_fits_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(authored, "AUTHORED_TRIANGLE_LIMIT", 5)
    target = tmp_path / "box.vtp"
    _ = target.write_bytes(b"stale")

    assert write_authored_geometry(_box(), FLAT.plane, target) is None
    assert not target.exists()


def test_a_failed_frame_check_keeps_the_authored_bodies(tmp_path: Path) -> None:
    sphere = TaggedPart([b3d.Sphere(10)], tags={TagKey.MATERIAL: "Steel316"}, name="Ball")
    authored_step = tmp_path / "authored.step"
    TaggedCompound(children=[sphere]).export_step(str(authored_step))
    step = tmp_path / "geometry.step"

    with pytest.raises(FrameGeometryError):
        _ = Cartesian2D(thickness=20 * U.mm).prepare_geometry(authored_step, step)

    assert authored_path(step) == tmp_path / "geometry.authored.vtp"
    assert authored_path(step).is_file()


def test_the_run_reads_the_authored_bodies_in_mesh_units(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")
    _ = write_authored_geometry(_box(), FLAT.plane, tmp_path / "geometry.authored.vtp")
    manifest = RunManifest.build(SimulationConfig(), status=RunStatus.OK, run_id="box", code_sha="sha", created_at=datetime(2026, 10, 19, tzinfo=timezone.utc))
    write_manifest(tmp_path, manifest)

    ghost = Run(tmp_path).authored_geometry()

    assert ghost is not None
    assert ghost.area == pytest.approx(2.2e-3, rel=1e-6)
    assert ghost.cell_data[AUTHORED_BODY_ARRAY].tolist() == [0] * 12
