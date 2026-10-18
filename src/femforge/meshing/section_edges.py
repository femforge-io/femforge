"""The named edges of a 2D section, kept in a file beside its STEP because gmsh imports no STEP names on free edges."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from femforge.common.files import write_atomic
from femforge.common.vector import Vec3, Vec3Field
from femforge.meshing.backend import MeshError
from femforge.physical.units import quantity


EDGES_SUFFIX = ".boundaries.json"
"""The suffix that turns a section STEP's stem into its edge file's name, ``geometry.boundaries.json`` beside ``geometry.step``."""

MATCH_TOLERANCE = 1e-6
"""`[mm]`: The largest endpoint or length mismatch at which an imported curve is still the recorded edge."""


def edges_path(step: Path) -> Path:
    """The edge file that belongs to the section STEP ``step``."""
    return step.with_name(f"{step.stem}{EDGES_SUFFIX}")


def millimetres_per_unit(target_units: str | None) -> float:
    """`[mm]`: The length of one gmsh model unit under ``Geometry.OCCTargetUnit = target_units``; None keeps the STEP's own millimetres."""
    if target_units is None:
        return 1.0

    return quantity(1.0, target_units.lower()).m_as("mm")


class CurveSpan(BaseModel):
    """Where an edge or an imported curve lies: its endpoints and its length."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    start: Vec3Field
    """`[mm]`: The first endpoint."""

    end: Vec3Field
    """`[mm]`: The last endpoint, the first again for a closed curve."""

    length: float
    """`L` `[mm]`: The curve length."""

    def matches(self, other: CurveSpan) -> bool:
        """Whether ``other`` runs between the same endpoints, either way round, with the same length."""

        def near(first: Vec3, second: Vec3) -> bool:
            return (first - second).magnitude() <= MATCH_TOLERANCE

        same_ends: bool = (near(self.start, other.start) and near(self.end, other.end)) or (near(self.start, other.end) and near(self.end, other.start))

        return same_ends and abs(self.length - other.length) <= MATCH_TOLERANCE


class SectionEdge(BaseModel):
    """One named edge of a section: the tags its STEP entry carries and where it lies."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    name: str
    """The edge's ``key=value`` tag string, as its STEP entry spells it."""

    span: CurveSpan
    """Where the edge lies in the section."""


class SectionEdges(BaseModel):
    """Every named edge of a section STEP, in export order."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    edges: list[SectionEdge]
    """The named edges."""

    def write(self, step: Path) -> None:
        """Write the edges beside ``step``, or remove a stale edge file when there are none."""
        path: Path = edges_path(step)

        if not self.edges:
            path.unlink(missing_ok=True)
            return

        write_atomic(path, self.model_dump_json())

    @classmethod
    def load(cls, step: Path) -> SectionEdges | None:
        """The edges recorded beside ``step``; None when it has no edge file."""
        path: Path = edges_path(step)

        if not path.is_file():
            return None

        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def match(self, curves: Mapping[int, CurveSpan]) -> dict[int, str]:
        """The name of each imported curve tag that is a recorded edge, each curve claimed once; raises [`MeshError`][femforge.meshing.MeshError] for an edge no curve matches."""
        names: dict[int, str] = {}

        for edge in self.edges:
            tag: int | None = next((tag for tag, span in curves.items() if tag not in names and edge.span.matches(span)), None)

            if tag is None:
                raise MeshError(f"The section edge {edge.name!r} is missing from the imported STEP; regenerate the section.")

            names[tag] = edge.name

        return names
