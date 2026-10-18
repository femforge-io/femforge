"""The sif section headers, single-sourced for the writer and the conditions that land in them."""

from enum import StrEnum


class SifSection(StrEnum):
    """One kind of sif section block, valued by its Elmer header spelling."""

    EQUATION = "Equation"
    """The solver set a body runs."""

    SOLVER = "Solver"
    """One solver and its keywords."""

    MATERIAL = "Material"
    """A body's material keywords."""

    BODY = "Body"
    """One body and its equation, material and body-force references."""

    BOUNDARY_CONDITION = "Boundary Condition"
    """The keywords one boundary group carries."""

    BODY_FORCE = "Body Force"
    """A body's volume sources."""

    COMPONENT = "Component"
    """A set of bodies Elmer lumps a quantity over."""
