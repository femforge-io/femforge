"""Native writer for the Elmer simulation input file (.sif): pyelmer's API shape and output text, always LF-terminated."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import TextIO

import yaml

from femforge.common.files import BundleFile
from femforge.elmer.config import SifValue
from femforge.elmer.sections import SifSection


__all__ = ["Body", "BodyForce", "Boundary", "Component", "Equation", "Material", "Simulation", "Solver"]

type SifData = Mapping[str, SifValue]


def _as_dict(data: SifData | None) -> dict[str, SifValue]:
    """The caller's keyword data as an owned dict (``None`` becomes empty)."""
    return {} if data is None else dict(data)


def _format_block(data: Mapping[str, SifValue], *, separator: str = " = ") -> str:
    """One ``'  key = value\\n'`` line per entry (the header block passes a bare space separator)."""
    return "".join(f"  {key}{separator}{value}\n" for key, value in data.items())


class _Section:
    """Shared shape of one sif block: name, write-time id (0 until [`Simulation.write_sif`][]), keyword data."""

    id: int
    name: str
    data: dict[str, SifValue]

    def get_data(self) -> dict[str, SifValue]:
        """The keyword dict this block emits."""
        return self.data

    def _signature(self) -> list[object]:
        """The constructor arguments the name-clash guard compares."""
        return [self.data]

    def __str__(self) -> str:
        return str(self.id)


def _register[S: _Section](registry: dict[str, S], cls: type[S], name: str, signature: list[object]) -> S | None:
    """The already-registered same-signature object under ``name`` (``None`` means create a fresh one); same name with different arguments raises."""
    existing = registry.get(name)
    if existing is None:
        return None

    if signature != existing._signature():
        raise ValueError(f'{cls.__name__} name clash: "{name}".')

    return existing


# Initialization happens in __new__ (no __init__): when the registry returns an existing object, Python must not
# re-run an initializer that would reset its id and cross-references.
class Solver(_Section):
    """One Solver block."""

    def __new__(cls, simulation: Simulation, name: str, data: SifData | None = None) -> Solver:
        """Register a Solver block under ``name``, or return the one already registered there with the same arguments; other arguments raise.

        Args:
            simulation: The sif document that holds the block.
            name: The block's name, unique among the solvers.
            data: The block's sif keywords; None leaves it empty.
        """
        parsed = _as_dict(data)
        existing = _register(
            simulation.solvers,
            cls,
            name,
            [parsed],
        )
        if existing is not None:
            return existing

        self = super().__new__(cls)
        self.id = 0
        self.name = name
        self.data = parsed
        simulation.solvers[name] = self
        return self


class Equation(_Section):
    """One Equation block listing its active solvers (ids resolve at write time)."""

    solvers: list[Solver]

    def __new__(cls, simulation: Simulation, name: str, solvers: Sequence[Solver], data: SifData | None = None) -> Equation:
        """Register an Equation block under ``name``, or return the one already registered there with the same arguments; other arguments raise.

        Args:
            simulation: The sif document that holds the block.
            name: The block's name, unique among the equations.
            solvers: The solvers the equation activates.
            data: The block's sif keywords; None leaves it empty.
        """
        parsed = _as_dict(data)
        existing = _register(
            simulation.equations,
            cls,
            name,
            [list(solvers), parsed],
        )
        if existing is not None:
            return existing

        self = super().__new__(cls)

        self.id = 0
        self.name = name
        self.solvers = list(solvers)
        self.data = parsed

        simulation.equations[name] = self
        return self

    def get_data(self) -> dict[str, SifValue]:
        """The ``Active Solvers`` list followed by the keyword data."""
        ids = "".join(f"{solver.id} " for solver in self.solvers)
        names = "".join(f"{solver.name}, " for solver in self.solvers)
        d: dict[str, SifValue] = {f"Active Solvers({len(self.solvers)})": f"{ids}  ! {names}"}
        d.update(self.data)
        return d

    def _signature(self) -> list[object]:
        return [self.solvers, self.data]


class Material(_Section):
    """One Material block."""

    def __new__(cls, simulation: Simulation, name: str, data: SifData | None = None) -> Material:
        """Register a Material block under ``name``, or return the one already registered there with the same arguments; other arguments raise.

        Args:
            simulation: The sif document that holds the block.
            name: The block's name, unique among the materials.
            data: The block's sif keywords; None leaves it empty.
        """
        parsed = _as_dict(data)
        existing = _register(
            simulation.materials,
            cls,
            name,
            [parsed],
        )
        if existing is not None:
            return existing

        self = super().__new__(cls)
        self.id = 0
        self.name = name
        self.data = parsed
        simulation.materials[name] = self
        return self


class BodyForce(_Section):
    """One Body Force block."""

    def __new__(cls, simulation: Simulation, name: str, data: SifData | None = None) -> BodyForce:
        """Register a Body Force block under ``name``, or return the one already registered there with the same arguments; other arguments raise.

        Args:
            simulation: The sif document that holds the block.
            name: The block's name, unique among the body forces.
            data: The block's sif keywords; None leaves it empty.
        """
        parsed = _as_dict(data)
        existing = _register(
            simulation.body_forces,
            cls,
            name,
            [parsed],
        )
        if existing is not None:
            return existing

        self = super().__new__(cls)
        self.id = 0
        self.name = name
        self.data = parsed
        simulation.body_forces[name] = self
        return self


class Body(_Section):
    """One Body block targeting mesh body ids, with settable ``equation``/``material``/``body_force`` cross-references."""

    body_ids: list[int]
    equation: Equation | None
    material: Material | None
    body_force: BodyForce | None

    def __new__(cls, simulation: Simulation, name: str, body_ids: Sequence[int] | None = None, data: SifData | None = None) -> Body:
        """Register a Body block under ``name``, or return the one already registered there with the same arguments; other arguments raise.

        Args:
            simulation: The sif document that holds the block.
            name: The block's name, unique among the bodies.
            body_ids: The mesh body ids the block targets; None targets none.
            data: The block's sif keywords; None leaves it empty.
        """
        parsed = _as_dict(data)
        ids = [] if body_ids is None else list(body_ids)
        existing = _register(
            simulation.bodies,
            cls,
            name,
            [ids, parsed],
        )
        if existing is not None:
            return existing

        self = super().__new__(cls)

        self.id = 0
        self.name = name
        self.body_ids = ids
        self.data = parsed
        self.equation = None
        self.material = None
        self.body_force = None

        simulation.bodies[name] = self
        return self

    def get_data(self) -> dict[str, SifValue]:
        """The target bodies and the equation, material and body-force references, followed by the keyword data."""
        d: dict[str, SifValue] = {f"Target Bodies({len(self.body_ids)})": " ".join(str(x) for x in self.body_ids)}
        if self.equation is not None:
            d[SifSection.EQUATION] = f"{self.equation.id}  ! {self.equation.name}"
        if self.material is not None:
            d[SifSection.MATERIAL] = f"{self.material.id}  ! {self.material.name}"
        if self.body_force is not None:
            d[SifSection.BODY_FORCE] = f"{self.body_force.id}  ! {self.body_force.name}"
        d.update(self.data)
        return d

    def _signature(self) -> list[object]:
        return [self.body_ids, self.data]


class Boundary(_Section):
    """One Boundary Condition block targeting mesh boundary ids."""

    geo_ids: list[int]

    def __new__(cls, simulation: Simulation, name: str, geo_ids: Sequence[int] | None = None, data: SifData | None = None) -> Boundary:
        """Register a Boundary Condition block under ``name``, or return the one already registered there with the same arguments; other arguments raise.

        Args:
            simulation: The sif document that holds the block.
            name: The block's name, unique among the boundaries.
            geo_ids: The mesh boundary ids the block targets; None targets none.
            data: The block's sif keywords; None leaves it empty.
        """
        parsed = _as_dict(data)
        ids = [] if geo_ids is None else list(geo_ids)
        existing = _register(
            simulation.boundaries,
            cls,
            name,
            [ids, parsed],
        )
        if existing is not None:
            return existing

        self = super().__new__(cls)

        self.id = 0
        self.name = name
        self.geo_ids = ids
        self.data = parsed

        simulation.boundaries[name] = self
        return self

    def get_data(self) -> dict[str, SifValue]:
        """The target boundaries followed by the keyword data."""
        d: dict[str, SifValue] = {f"Target Boundaries({len(self.geo_ids)})": " ".join(str(x) for x in self.geo_ids)}
        d.update(self.data)
        return d

    def _signature(self) -> list[object]:
        return [self.geo_ids, self.data]


class Component(_Section):
    """One Component block over master bodies/boundaries (ids resolve at write time)."""

    master_bodies: list[Body]
    master_boundaries: list[Boundary]

    def __new__(
        cls,
        simulation: Simulation,
        name: str,
        master_bodies: Sequence[Body] | None = None,
        master_boundaries: Sequence[Boundary] | None = None,
        data: SifData | None = None,
    ) -> Component:
        """Register a Component block under ``name``, or return the one already registered there with the same arguments; other arguments raise.

        Args:
            simulation: The sif document that holds the block.
            name: The block's name, unique among the components.
            master_bodies: The bodies the component lumps over; None names none.
            master_boundaries: The boundaries the component lumps over; None names none.
            data: The block's sif keywords; None leaves it empty.
        """
        parsed = _as_dict(data)
        bodies = [] if master_bodies is None else list(master_bodies)
        boundaries = [] if master_boundaries is None else list(master_boundaries)
        existing = _register(
            simulation.components,
            cls,
            name,
            [bodies, boundaries, parsed],
        )
        if existing is not None:
            return existing

        self = super().__new__(cls)

        self.id = 0
        self.name = name
        self.master_bodies = bodies
        self.master_boundaries = boundaries
        self.data = parsed

        simulation.components[name] = self
        return self

    def get_data(self) -> dict[str, SifValue]:
        """The component name and its master bodies and boundaries, followed by the keyword data."""
        d: dict[str, SifValue] = {"Name": self.name}

        if self.master_bodies:
            ids = " ".join(str(x) for x in self.master_bodies)
            names = "".join(f"{body.name}, " for body in self.master_bodies).strip()
            d[f"Master Bodies({len(self.master_bodies)})"] = f"{ids}  ! {names}"

        if self.master_boundaries:
            ids = " ".join(str(x) for x in self.master_boundaries)
            names = "".join(f"{boundary.name}, " for boundary in self.master_boundaries).strip()
            d[f"Master Boundaries({len(self.master_boundaries)})"] = f"{ids}  ! {names}"

        d.update(self.data)
        return d

    def _signature(self) -> list[object]:
        return [self.master_bodies, self.master_boundaries, self.data]


def _write_section(f: TextIO, keyword: SifSection, registry: Mapping[str, _Section]) -> None:
    """One ``! name`` + ``<Keyword> <id>`` block per registered object, then the reference's trailing blank line."""
    for name, obj in registry.items():
        f.write(f"! {name}\n{keyword} {obj.id}\n")
        f.write(_format_block(obj.get_data()))
        f.write("End\n\n")
    f.write("\n")


class Simulation:
    """The whole sif document: header/settings/constants plus insertion-ordered registries of every section block."""

    def __init__(self) -> None:
        """Start an empty document with the default header and constants."""
        self.header: dict[str, str] = {"CHECK KEYWORDS": '"Warn"', "Mesh DB": '"." "."'}
        self.settings: dict[str, SifValue] = {}
        self.constants: dict[str, SifValue] = {"Stefan Boltzmann": 5.6704e-08}

        self.solvers: dict[str, Solver] = {}
        self.equations: dict[str, Equation] = {}
        self.materials: dict[str, Material] = {}
        self.bodies: dict[str, Body] = {}
        self.boundaries: dict[str, Boundary] = {}
        self.body_forces: dict[str, BodyForce] = {}
        self.components: dict[str, Component] = {}

        self.sif_filename: str = BundleFile.SIF

    def write_sif(self, simulation_dir: str) -> None:
        """Assign section ids 1..N in insertion order, then write the sif in the reference's exact layout."""
        self._assign_ids()
        with open(os.path.join(simulation_dir, self.sif_filename), "w", newline="\n") as f:
            f.write("Header\n")
            f.write(_format_block(self.header, separator=" "))
            f.write("End\n\n")
            f.write("Simulation\n")
            f.write(_format_block(self.settings))
            f.write("End\n\n")
            f.write("Constants\n")
            f.write(_format_block(self.constants))
            f.write("End\n\n")
            _write_section(f, SifSection.EQUATION, self.equations)
            _write_section(f, SifSection.SOLVER, self.solvers)
            _write_section(f, SifSection.MATERIAL, self.materials)
            _write_section(f, SifSection.BODY, self.bodies)
            _write_section(f, SifSection.BOUNDARY_CONDITION, self.boundaries)
            _write_section(f, SifSection.BODY_FORCE, self.body_forces)
            _write_section(f, SifSection.COMPONENT, self.components)

    def write_startinfo(self, simulation_dir: str) -> None:
        """Write the ELMERSOLVER_STARTINFO file naming the sif."""
        with open(os.path.join(simulation_dir, BundleFile.STARTINFO), "w", newline="\n") as f:
            f.write(self.sif_filename)
            f.write("\n")

    def write_boundary_ids(self, simulation_dir: str) -> None:
        """Write ``boundaries.yml`` mapping each assigned boundary id to its block name."""
        data = {boundary.id: name for name, boundary in self.boundaries.items()}
        with open(os.path.join(simulation_dir, BundleFile.BOUNDARIES), "w", newline="\n") as f:
            yaml.dump(data, f, sort_keys=False)

    def write_component_ids(self, simulation_dir: str) -> None:
        """Write ``components.yml`` mapping each assigned component id to its block name (the twin of [`write_boundary_ids`][])."""
        data = {component.id: name for name, component in self.components.items()}
        with open(os.path.join(simulation_dir, BundleFile.COMPONENTS), "w", newline="\n") as f:
            yaml.dump(data, f, sort_keys=False)

    def _assign_ids(self) -> None:
        """Number each section's blocks 1..N in insertion order."""
        for registry in (self.solvers, self.equations, self.materials, self.bodies, self.boundaries, self.body_forces, self.components):
            for i, obj in enumerate(registry.values(), start=1):
                obj.id = i
