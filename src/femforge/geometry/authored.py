"""The authored bodies of a sectioned run, tessellated in the solve frame and written as ``geometry.authored.vtp``."""

from __future__ import annotations

import base64
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import build123d as b3d
import numpy as np
from numpy.typing import NDArray

from femforge.common.log import log
from femforge.geometry.tags import TaggedCompound, TaggedPart


if TYPE_CHECKING:
    from femforge.frames.base import SolvePlane


__all__ = [
    "AUTHORED_DEFLECTION",
    "AUTHORED_ANGLE",
    "AUTHORED_TRIANGLE_LIMIT",
    "AUTHORED_RETRIES",
    "AUTHORED_BODY_ARRAY",
    "AuthoredMesh",
    "tessellate_bodies",
    "write_vtp",
    "write_authored_geometry",
]

AUTHORED_DEFLECTION = 1e-3
"""`[-]`: The tessellation tolerance per millimetre of the bounding-box diagonal of the model; build123d applies it relative to the size of each edge."""

AUTHORED_ANGLE = 0.3
"""`[rad]`: The largest angle between neighbouring facet normals of the tessellation."""

AUTHORED_TRIANGLE_LIMIT = 200_000
"""The most triangles the file may hold; a finer tessellation is retried coarser."""

AUTHORED_RETRIES = 3
"""How many times a tessellation over the limit is retried at twice the deflection before the file is skipped."""

AUTHORED_BODY_ARRAY = "AuthoredBody"
"""The cell array that carries the index of the authored body each triangle belongs to."""


@dataclass(frozen=True)
class AuthoredMesh:
    """The triangles of the authored bodies, in millimetres in the solve frame."""

    points: NDArray[np.float64]
    """`[mm]`: The ``(n, 3)`` vertices."""

    triangles: NDArray[np.int64]
    """The ``(m, 3)`` vertex indices of each triangle."""

    bodies: NDArray[np.int64]
    """The index of the authored body each triangle belongs to."""


def _clean(part: TaggedPart) -> None:
    """Drop the triangulation OCCT keeps on ``part``, which it reuses instead of meshing coarser."""
    from OCP.BRepTools import BRepTools  # pyright: ignore[reportAttributeAccessIssue, reportUnknownVariableType]  # OCP ships no stubs

    BRepTools.Clean_s(part.wrapped)  # pyright: ignore[reportUnknownMemberType]  # OCP ships no stubs


def tessellate_bodies(compound: TaggedCompound, plane: SolvePlane, scale: float = 1.0) -> AuthoredMesh:
    """Tessellate every authored body of ``compound`` placed in the solve frame of ``plane``.

    Args:
        compound: The authored model, in millimetres.
        plane: The solve plane whose frame the bodies are placed in.
        scale: The multiple of ``AUTHORED_DEFLECTION`` to tessellate at.
    """
    from femforge.geometry.section import solve_location

    parts: list[TaggedPart] = [leaf for leaf in compound.placed_leaves(solve_location(plane)) if isinstance(leaf, TaggedPart)]
    box: b3d.BoundBox = compound.bounding_box()
    deflection: float = scale * AUTHORED_DEFLECTION * box.diagonal
    points: list[NDArray[np.float64]] = [np.zeros((0, 3))]
    triangles: list[NDArray[np.int64]] = [np.zeros((0, 3), dtype=np.int64)]
    bodies: list[NDArray[np.int64]] = [np.zeros(0, dtype=np.int64)]
    offset: int = 0

    for index, part in enumerate(parts):
        _clean(part)
        vertices, faces = part.tessellate(deflection, AUTHORED_ANGLE)
        corners: NDArray[np.float64] = np.array([(vertex.X, vertex.Y, vertex.Z) for vertex in vertices], dtype=np.float64).reshape(-1, 3)
        indices: NDArray[np.int64] = np.array(faces, dtype=np.int64).reshape(-1, 3)
        points.append(corners)
        triangles.append(indices + offset)
        bodies.append(np.full(len(indices), index, dtype=np.int64))
        offset += len(corners)

    return AuthoredMesh(points=np.concatenate(points), triangles=np.concatenate(triangles), bodies=np.concatenate(bodies))


def _encoded(values: NDArray[np.generic]) -> str:
    """``values`` as one zlib-compressed base64 VTK data block with its ``UInt32`` header."""
    raw: bytes = values.tobytes()
    body: bytes = zlib.compress(raw, 6)
    header: bytes = np.array([1, len(raw), len(raw), len(body)], dtype=np.uint32).tobytes()

    return base64.b64encode(header).decode("ascii") + base64.b64encode(body).decode("ascii")


def write_vtp(mesh: AuthoredMesh, path: Path) -> Path:
    """Write ``mesh`` as a zlib-compressed VTK PolyData file with its body index as a cell array, and return ``path``."""
    count: int = len(mesh.triangles)
    connectivity: str = _encoded(mesh.triangles.astype(np.int32).ravel())
    offsets: str = _encoded((np.arange(1, count + 1) * 3).astype(np.int32))
    bodies: str = _encoded(mesh.bodies.astype(np.int32))
    points: str = _encoded(mesh.points.astype(np.float32).ravel())
    xml: str = (
        '<?xml version="1.0"?>\n'
        '<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian" header_type="UInt32" compressor="vtkZLibDataCompressor">\n'
        f'<PolyData><Piece NumberOfPoints="{len(mesh.points)}" NumberOfPolys="{count}">\n'
        f'<CellData><DataArray type="Int32" Name="{AUTHORED_BODY_ARRAY}" format="binary">{bodies}</DataArray></CellData>\n'
        f'<Points><DataArray type="Float32" NumberOfComponents="3" format="binary">{points}</DataArray></Points>\n'
        f'<Polys><DataArray type="Int32" Name="connectivity" format="binary">{connectivity}</DataArray>\n'
        f'<DataArray type="Int32" Name="offsets" format="binary">{offsets}</DataArray></Polys>\n'
        "</Piece></PolyData></VTKFile>\n"
    )
    _ = path.write_bytes(xml.encode("ascii"))

    return path


def write_authored_geometry(compound: TaggedCompound, plane: SolvePlane, path: Path) -> Path | None:
    """Write the authored bodies of ``compound`` in the solve frame of ``plane`` to ``path``, coarsening past the triangle limit; None when no tessellation fits it.

    Args:
        compound: The authored model, in millimetres.
        plane: The solve plane whose frame the bodies are placed in.
        path: The ``.vtp`` file to write.
    """
    scale: float = 1.0

    for _ in range(AUTHORED_RETRIES + 1):
        mesh: AuthoredMesh = tessellate_bodies(compound, plane, scale)

        if len(mesh.triangles) <= AUTHORED_TRIANGLE_LIMIT:
            return write_vtp(mesh, path)

        scale *= 2.0

    log.bind(path=str(path), triangles=len(mesh.triangles), limit=AUTHORED_TRIANGLE_LIMIT).warning("Authored geometry skipped; its tessellation stays over the triangle limit.")
    path.unlink(missing_ok=True)

    return None
