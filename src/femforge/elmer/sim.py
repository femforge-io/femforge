"""Elmer .sif generator whose bodies and boundaries line up one-to-one with the gmsh physical and boundary groups."""

from __future__ import annotations

import os
import warnings
from enum import StrEnum
from typing import TYPE_CHECKING, Mapping, Protocol, cast

from femforge.elmer import sif
from femforge.elmer.config import MagnetoDynamicsCalcFieldsSolver, PhysicsConfig, SaveScalarsSolver, SifValue, solved_stack
from femforge.elmer.physics import Physics, condition_physics, runtime_variables
from femforge.elmer.sections import SifSection
from femforge.elmer.variables import ElmerVariable
from femforge.frames import Frame, Solid
from femforge.materials.properties import MATERIAL_KEY_EQUIVALENTS, ElmerMaterialKeyword, MaterialProperties
from femforge.physical.conditions import BoundarySite, CalculateMagneticForce, Condition, ConditionTarget, FixedDisplacement
from femforge.physical.domain import EntityTag, conditions_for
from femforge.physical.units import Quantity


if TYPE_CHECKING:
    from femforge.common.vector import Vec3
    from femforge.meshing.boundary import BoundaryGroup
    from femforge.meshing.groups import PhysicalGroup


# Physics ill-posed without at least one boundary condition (a pure-Neumann problem has no unique solution).
# Magnetostatics is excluded — its far-field BC is auto-emitted on the external shell.
PHYSICS_NEEDS_BOUNDARY_CONDITION: frozenset[Physics] = frozenset({Physics.THERMAL, Physics.ELECTROSTATICS, Physics.CURRENT_CONDUCTION, Physics.JOULE_HEATING, Physics.LINEAR_ELASTICITY})


class BoundaryKind(StrEnum):
    """The pseudo-``kind`` tokens naming the auto-emitted magnetostatics boundary blocks, in the same ``<group>_<kind>`` form as condition kinds."""

    FAR_FIELD = "far_field"
    SYMMETRY_AXIS = "symmetry_axis"


# The physics whose stack solves a magnetic vector potential: shared magnet wiring (per-magnet Magnetization
# materials) + an auto-emitted Dirichlet far field on external boundaries.
_MAGNETIC_FIELD_PHYSICS = frozenset({Physics.MAGNETOSTATICS, Physics.MAGNETOSTATICS_2D, Physics.COIL_MAGNETOSTATICS})

# The subset whose magnets must resolve a usable Magnetization (the coil stack drives via current, not magnets).
_MAGNET_DRIVEN_PHYSICS = frozenset({Physics.MAGNETOSTATICS, Physics.MAGNETOSTATICS_2D})


class SolverConfigWarning(UserWarning):
    """A non-fatal solver-configuration smell surfaced by [`Sim.validate`][], such as a probably missing boundary condition."""


class _DiscriminatedCondition(Protocol):
    """The ``kind`` discriminator every concrete [`Condition`][femforge.physical.conditions.Condition] subclass carries.

    The boundary loop reads ``kind`` through this, because ``conditions_for`` returns the base type.
    """

    kind: str


class _Oriented(Protocol):
    """The direction a ``Material``-section condition orients its material vector along, None when unset."""

    direction: Vec3 | None


class _Labelled(Protocol):
    """The Component name a force-lumping ``Component``-section condition asks for, None for its tag's name."""

    label: str | None


class Sim:
    """
    Generate an Elmer Simulation (and write the .sif) from the gmsh physical/boundary groups, via a two-phase
    lifecycle: edit the typed [`config`][] after the cheap constructor, then [`generate`][] + [`write`][].
    """

    def __init__(
        self,
        physical_groups: list["PhysicalGroup"] | None = None,
        physics: Physics | str = Physics.MAGNETOSTATICS,
        boundary_groups: list["BoundaryGroup"] | None = None,
        config: PhysicsConfig | None = None,
        frame: Frame = Solid(),
    ) -> None:
        """Hold the groups and resolve the solver stack; [`generate`][] builds the sif.

        Args:
            physical_groups: The gmsh body groups the Elmer bodies mirror.
            physics: The physics to solve, as the enum or its string value.
            boundary_groups: The surface groups boundary conditions attach to; None wires none.
            config: An authored solver stack; None takes the preset of ``physics`` in ``frame``.
            frame: The solve frame the model maps onto.
        """
        self.physical_groups: list["PhysicalGroup"] = physical_groups or []
        # The dim-2 (surface) groups from body adjacency; boundary conditions attach to these. If omitted, none are wired.
        self.boundary_groups: list["BoundaryGroup"] = boundary_groups or []
        self.frame: Frame = frame
        # Accept the enum or its string value; normalize so a bad value fails here with a clear error.
        try:
            selected: Physics = Physics(physics)
        except ValueError:
            raise ValueError(f"Unknown physics {physics!r}; choose from {[p.value for p in Physics]}.") from None

        self.physics: Physics = frame.solves(selected)
        # The typed, editable solver stack: the caller's authored config when given, else the physics' preset in the frame.
        # Cheap (pure pydantic); the sif object graph is built in generate().
        self.config: PhysicsConfig = config if config is not None else solved_stack(selected, frame)
        self.config.check_frame(frame)

        # The backstop for conditions edited in place after the config validated them.
        body_tags: list[EntityTag] = [tag for group in self.physical_groups for tag in group.tags]
        face_tags: list[EntityTag] = [tag for group in self.boundary_groups for tag in group.tags]

        for tag in body_tags + face_tags:
            for condition in tag.conditions:
                condition.check_frame(frame)

        # The sif Simulation, built by generate(); None until then so write()/validate() fail loudly if skipped.
        self.sim: sif.Simulation | None = None

    # -- generation ----------------------------------------------------------

    def generate(self, validate: bool = True) -> "Sim":
        """
        Build the sif ``Simulation`` object graph from the (possibly edited) [`config`][] and the groups, then
        run [`validate`][]; returns ``self``. Pass ``validate=False`` to build a deliberately incomplete config
        (the wiring falls back to its in-sif markers).
        """
        sim: sif.Simulation = sif.Simulation()
        sim.settings = self.config.simulation.to_elmer()
        # Each typed model reflects its sif keyword block on demand; the constants block carries only the physics' activated constants.
        sim.constants.update(self.config.constants.to_elmer())

        # Build the sif sections in dependency order.
        solvers: list[sif.Solver] = self._build_solvers(sim)
        equation, dielectric_equation = self._build_equations(sim, solvers)
        materials: dict[str, sif.Material] = self._build_materials(sim)
        self._build_bodies(sim, materials, equation, dielectric_equation)
        self._build_boundaries(sim)

        self.sim = sim
        if validate:
            self.validate()
        return self

    # -- validation ----------------------------------------------------------

    def validate(self) -> None:
        """Cross-object material and solver checks Pydantic cannot see, collected into one ``ValueError``; requires [`generate`][] first.

        Missing material keys, a leaked pint ``Quantity`` or a magnet without a usable ``Magnetization`` raise; missing boundary or drive coverage warns with a [`SolverConfigWarning`][].
        """
        sim: sif.Simulation | None = self.sim
        if sim is None:
            raise RuntimeError("validate() requires generate() to have built the simulation first.")

        problems: list[str] = []
        # conductor_only solvers act only on conductor bodies (see _build_equations), so their requirements apply only there.
        required_everywhere: set[ElmerMaterialKeyword] = {key for solver in self.config.solvers if not solver.conductor_only for key in solver.required_material_keys}
        required_on_conductors: set[ElmerMaterialKeyword] = {key for solver in self.config.solvers if solver.conductor_only for key in solver.required_material_keys}
        at: Mapping[str, Quantity] = self.config.operating_point.as_mapping()

        for group in self.physical_groups:
            mat: MaterialProperties = group.material
            emitted: dict[str, float | str] = mat.to_elmer(
                at=self.config.operating_point.as_mapping(),
                runtime=runtime_variables(self.physics),
            )

            # Check 1: required material properties present (ElmerMaterialKeyword is a StrEnum, so members compare against the string-keyed dict).
            required: set[ElmerMaterialKeyword] = required_everywhere | (required_on_conductors if mat.is_conductor(at) else set())
            # A key is also satisfied by an equivalent: a nonlinear material presents an H-B Curve instead of Relative Permeability.
            missing: set[ElmerMaterialKeyword] = {key for key in required - emitted.keys() if not (MATERIAL_KEY_EQUIVALENTS.get(key, frozenset()) & emitted.keys())}
            if missing:
                problems.append(f"Body {group.name}: material {mat.name} missing {sorted(k.value for k in missing)} for physics '{self.physics.value}'")

            # Check 2: numeric sanity / unit stripping.
            for key, value in emitted.items():
                if not isinstance(value, (int, float, str)):
                    problems.append(
                        f"Body {group.name}: material {mat.name} property '{key}' is a {type(value).__name__}, expected a stripped number/str (a to_elmer() left a pint Quantity unconverted)"
                    )

            # Check 3: magnets must resolve a usable magnetization direction.
            if self.physics in _MAGNET_DRIVEN_PHYSICS and mat.is_magnet:
                magnetizations: list[Condition] = self._material_conditions(group.tags)
                usable: Condition | None = next((m for m in magnetizations if cast("_Oriented", m).direction is not None), None)
                if usable is None:
                    problems.append(f"Body {group.name}: magnet material {mat.name} has no usable Magnetization condition (a direction is required for physics '{self.physics.value}')")

        # Check 4b: a coil-magnetostatics solve with bodies but no CoilDrive is source-free (the AV solve would return a zero field) — almost always a forgotten drive.
        if self.physics is Physics.COIL_MAGNETOSTATICS and self.physical_groups and not any(self._coil_drives(group.tags) for group in self.physical_groups):
            warnings.warn(
                f"Physics '{self.physics.value}' resolved no CoilDrive condition on any of {len(self.physical_groups)} bodies; "
                "the coil solve has no source (author a CoilDrive on each driven phase's tag, plus its CoilTerminal pair).",
                SolverConfigWarning,
                stacklevel=2,
            )

        # Check 4: a physics that needs a boundary condition to be well-posed but resolved none — almost always a forgotten BC.
        if self.physics in PHYSICS_NEEDS_BOUNDARY_CONDITION and not sim.boundaries:
            warnings.warn(
                f"Physics '{self.physics.value}' resolved no boundary conditions from {len(self.boundary_groups)} boundary group(s); "
                f"the solve is likely ill-posed (does a region need a FixedTemperature / HeatFlux / Convection / FixedPotential / FixedDisplacement / SurfaceForce?).",
                SolverConfigWarning,
                stacklevel=2,
            )
        elif self.physics is Physics.LINEAR_ELASTICITY and not self._has_fixed_displacement():
            warnings.warn(
                f"Physics '{self.physics.value}' resolved no FixedDisplacement on any of {len(self.boundary_groups)} boundary group(s); "
                "nothing holds the model against rigid-body motion, so the solve is singular (fix a face with FixedDisplacement).",
                SolverConfigWarning,
                stacklevel=2,
            )

        # Check 5: a force Component (authored CalculateMagneticForce or the auto per-magnet one) needs its measurement chain —
        # CalcFields computes the Component lumping, SaveScalars exports it to scalars.dat.
        has_authored_force: bool = any(self._force_conditions(group.tags) for group in self.physical_groups)
        has_auto_force: bool = self.physics in _MAGNETIC_FIELD_PHYSICS and any(group.material.is_magnet for group in self.physical_groups)

        if has_authored_force or has_auto_force:
            calc_fields_solvers: list[MagnetoDynamicsCalcFieldsSolver] = [solver for solver in self.config.solvers if isinstance(solver, MagnetoDynamicsCalcFieldsSolver)]

            if not calc_fields_solvers:
                warnings.warn(
                    "Force Component(s) resolved but the solver stack has no MagnetoDynamicsCalcFieldsSolver; Component force lumping lives in CalcFields, so no force would be computed.",
                    SolverConfigWarning,
                    stacklevel=2,
                )
            elif not any(solver.calculate_nodal_forces for solver in calc_fields_solvers):
                warnings.warn(
                    "Force Component(s) resolved but CalcFields has Calculate Nodal Forces off; the Component lumping reads the nodal force field, so the exported force would be zero.",
                    SolverConfigWarning,
                    stacklevel=2,
                )

            if not any(isinstance(solver, SaveScalarsSolver) for solver in self.config.solvers):
                warnings.warn(
                    "Force Component(s) resolved but the solver stack has no SaveScalarsSolver; the lumped Component force would be computed but never exported to scalars.dat.",
                    SolverConfigWarning,
                    stacklevel=2,
                )

        if problems:
            raise ValueError("Solver configuration invalid:\n  - " + "\n  - ".join(problems))

    # -- sif section builders ------------------------------------------------

    def _build_solvers(self, sim: sif.Simulation) -> list[sif.Solver]:
        """Instantiate the config's typed solvers as sif Solver objects (each solver's ``to_elmer()`` supplies its sif keyword dict)."""
        solvers: list[sif.Solver] = []
        for spec in self.config.solvers:
            solvers.append(sif.Solver(sim, spec.kind, spec.to_elmer()))
        return solvers

    def _build_equations(self, sim: sif.Simulation, solvers: list[sif.Solver]) -> tuple[sif.Equation, sif.Equation | None]:
        """
        The main equation (all active solvers) plus, only when the stack has a ``conductor_only`` solver AND a
        non-conductor body exists, a second equation without those solvers. Non-conductor bodies must not carry a
        conduction solve: the ~21-decade σ contrast makes the matrix singular for a direct factorization and a stall
        for preconditioned CG.
        """
        keywords: dict[str, SifValue] = {}

        for spec in self.config.solvers:
            keywords.update(spec.equation_keywords())

        equation = sif.Equation(sim, "Main Equation", solvers, {"Name": '"Main Equation"', **keywords})
        everywhere: list[sif.Solver] = [solver for spec, solver in zip(self.config.solvers, solvers) if not spec.conductor_only]

        if self.physics is Physics.COIL_MAGNETOSTATICS:
            # CoilSolver hard-errors when a body in its equation belongs to no Component, so its equation covers only the DRIVEN (CoilDrive-carrying) bodies.
            # WhitneyAV and CalcFields must stay in BOTH equations: splitting THOSE across bodies corrupts CalcFields' DOF mapping (SIGSEGV + silently wrong energy).
            if all(self._coil_drives(group.tags) for group in self.physical_groups):
                return equation, None
            return equation, sif.Equation(sim, "Field Equation", everywhere, {"Name": '"Field Equation"', **keywords})

        at: Mapping[str, Quantity] = self.config.operating_point.as_mapping()
        if len(everywhere) == len(solvers) or all(group.material.is_conductor(at) for group in self.physical_groups):
            return equation, None
        return equation, sif.Equation(sim, "Dielectric Equation", everywhere, {"Name": '"Dielectric Equation"', **keywords})

    @staticmethod
    def _coil_drives(tags: list[EntityTag]) -> list[Condition]:
        """The coil physics' Component conditions a region carries — what marks a body as part of a driven coil Component."""
        coil_conditions: list[Condition] = conditions_for(tags, Physics.COIL_MAGNETOSTATICS, ConditionTarget.BODY)

        return [c for c in coil_conditions if c.section is SifSection.COMPONENT]

    def _force_conditions(self, tags: list[EntityTag]) -> list[Condition]:
        """The active Component conditions a region carries besides its coil drives: the force lumps."""
        body_conditions: list[Condition] = conditions_for(tags, condition_physics(self.physics), ConditionTarget.BODY)
        drives: list[Condition] = self._coil_drives(tags)

        return [c for c in body_conditions if c.section is SifSection.COMPONENT and not any(c is drive for drive in drives)]

    @staticmethod
    def _material_conditions(tags: list[EntityTag]) -> list[Condition]:
        """The magnetostatic conditions a region writes into its own Material block."""
        magnetic_conditions: list[Condition] = conditions_for(tags, Physics.MAGNETOSTATICS, ConditionTarget.BODY)

        return [c for c in magnetic_conditions if c.section is SifSection.MATERIAL]

    def _build_materials(self, sim: sif.Simulation) -> dict[str, sif.Material]:
        """
        One Elmer Material per distinct project material, keyed by material name.
        Deduplicated so two bodies of the same material share one Material block.
        """
        at: Mapping[str, object] = self.config.operating_point.as_mapping()

        materials: dict[str, sif.Material] = {}
        seen: set[str] = set()

        for group in self.physical_groups:
            mat = group.material

            # Magnet bodies get their OWN per-region Material (carrying the Magnetization vector, see _wire_magnet_body);
            # skipping them here keeps the shared block from being emitted as an unreferenced orphan.
            if self.physics in _MAGNETIC_FIELD_PHYSICS and mat.is_magnet:
                continue
            if mat.name in seen:
                continue

            seen.add(mat.name)
            data = mat.to_elmer(at=at, runtime=runtime_variables(self.physics))
            materials[mat.name] = sif.Material(sim, mat.name, data=data)

        return materials

    def _build_bodies(self, sim: sif.Simulation, materials: dict[str, sif.Material], equation: sif.Equation, dielectric_equation: sif.Equation | None) -> None:
        """
        One Elmer Body per gmsh physical group, targeting the same gmsh id, wired to its material + the equation
        matching its conductor classification, plus any physics-specific per-body extras.
        """
        at: Mapping[str, Quantity] = self.config.operating_point.as_mapping()

        # One Component per distinct CoilDrive instance (keyed by identity: the SAME drive authored on a phase's tag reaches every body of that phase).
        coil_components: dict[int, tuple[str, Condition, list[sif.Body]]] = {}

        # Same identity-keyed grouping for CalculateMagneticForce: one authored instance gathers every matched body into one Component.
        force_components: dict[int, tuple[str, Condition, list[sif.Body]]] = {}

        # Magnet groups that get an auto force Component (no authoring needed); an authored CalculateMagneticForce on the group suppresses it.
        auto_force_bodies: list[tuple[str, sif.Body]] = []

        for group in self.physical_groups:
            body: sif.Body = sif.Body(sim, group.name, [group.id], {"Name": f'"{group.name}"'})
            drives: list[Condition] = self._coil_drives(group.tags) if self.physics is Physics.COIL_MAGNETOSTATICS else []

            # The main equation is for the DRIVEN bodies (COIL_MAGNETOSTATICS) or the conductors (conductor_only physics); everything else takes the second equation when one exists.
            if self.physics is Physics.COIL_MAGNETOSTATICS:
                on_main: bool = bool(drives)
            else:
                on_main = group.material.is_conductor(at)
            body.equation = equation if on_main or dielectric_equation is None else dielectric_equation

            for drive in drives:
                fresh_entry: tuple[str, Condition, list[sif.Body]] = (self._condition_tag_name(group.tags, drive), drive, [])
                _, _, bodies = coil_components.setdefault(id(drive), fresh_entry)
                bodies.append(body)

            if self.physics in _MAGNETIC_FIELD_PHYSICS and group.material.is_magnet:
                # A magnet body's Material is per-region (it carries the Magnetization vector), not the shared deduplicated block.
                self._wire_magnet_body(sim, body, group)
            else:
                body.material = materials[group.material.name]

            forces: list[Condition] = self._force_conditions(group.tags)

            for cond in forces:
                fresh_force_entry: tuple[str, Condition, list[sif.Body]] = (self._condition_tag_name(group.tags, cond), cond, [])
                _, _, force_bodies = force_components.setdefault(id(cond), fresh_force_entry)
                force_bodies.append(body)

            if self.physics in _MAGNETIC_FIELD_PHYSICS and group.material.is_magnet and not forces:
                auto_force_bodies.append((group.name, body))

            body_conditions: list[Condition] = conditions_for(
                tags=group.tags,
                physics=condition_physics(self.physics),
                target=ConditionTarget.BODY,
            )
            data: dict[str, SifValue] = {}

            for cond in body_conditions:
                if cond.section is SifSection.BODY_FORCE:
                    data.update(cond.to_elmer())

            if data:
                body.body_force = sif.BodyForce(sim, f"{group.name}_body_force", data)

        # The Component blocks CoilSolver reads; the Body references resolve to their sif ids at write time.
        for name, drive, bodies in coil_components.values():
            _ = sif.Component(sim, f"{name}_coil", master_bodies=bodies, data=drive.to_elmer())

        # The force/torque-lumping Components CalcFields fills and SaveScalars exports.
        for tag_name, cond, bodies in force_components.values():
            label: str | None = cast("_Labelled", cond).label
            _ = sif.Component(sim, label or tag_name, master_bodies=bodies, data=cond.to_elmer())

        # Every magnet body gets force scalars without authoring; named by group so run.magnetic's name join works.
        for group_name, magnet_body in auto_force_bodies:
            _ = sif.Component(sim, group_name, master_bodies=[magnet_body], data=CalculateMagneticForce().to_elmer())

    @staticmethod
    def _condition_tag_name(tags: list[EntityTag], condition: Condition) -> str:
        """The name of the EntityTag that authored ``condition`` — the stable, human-readable Component name stem."""
        for tag in tags:
            if any(c is condition for c in tag.conditions):
                return tag.name
        raise ValueError(f"{type(condition).__name__} condition not found on any of the region's tags (conditions_for returned it, so a tag must carry it).")

    # -- boundary conditions -------------------------------------------------

    def _build_boundaries(self, sim: sif.Simulation) -> None:
        """
        Attach boundary conditions to the adjacency-derived surface groups: each bordering body's boundary-target
        conditions project onto the group, plus the magnetostatics far field on external shells. A surface resolving
        several conditions gets ONE merged block — Elmer assigns each boundary element a single BC section, so a
        second block targeting the same boundary id would be silently ignored.
        """
        tags_by_body: dict[str, list[EntityTag]] = {group.name: group.tags for group in self.physical_groups}
        for bg in self.boundary_groups:
            specs = self._boundary_specs(bg, tags_by_body)

            if not specs:
                continue

            merged: dict[str, SifValue] = {}

            for _, data in specs:
                merged.update(data)

            name = f"{bg.name}_{'_'.join(kind for kind, _ in specs)}"
            _ = sif.Boundary(sim, name, [bg.id], data=merged)

    def _boundary_conditions(self, bg: "BoundaryGroup", tags_by_body: Mapping[str, list[EntityTag]]) -> list[Condition]:
        """The boundary conditions one group resolves under the active physics: its bordering bodies' tags first, then its own face tags, each tag once.

        An ``external_only`` condition lands only on an external group.
        """
        body_tags: list[EntityTag] = [tag for body in bg.bodies for tag in tags_by_body.get(body, [])]
        unique_tags: dict[int, EntityTag] = {id(tag): tag for tag in body_tags + bg.tags}

        resolved_conditions: list[Condition] = conditions_for(
            tags=unique_tags.values(),
            physics=condition_physics(self.physics),
            target=ConditionTarget.BOUNDARY,
        )

        return [cond for cond in resolved_conditions if bg.is_external or not cond.external_only]

    def _has_fixed_displacement(self) -> bool:
        """Whether any boundary group resolves a ``FixedDisplacement``."""
        tags_by_body: dict[str, list[EntityTag]] = {group.name: group.tags for group in self.physical_groups}
        resolved: list[Condition] = [cond for bg in self.boundary_groups for cond in self._boundary_conditions(bg, tags_by_body)]

        return any(isinstance(cond, FixedDisplacement) for cond in resolved)

    def _boundary_specs(self, bg: "BoundaryGroup", tags_by_body: Mapping[str, list[EntityTag]]) -> list[tuple[str, dict[str, SifValue]]]:
        """
        The ``(kind, sif-data)`` specs one surface group resolves: its conditions in ``_boundary_conditions`` order, so a face
        tag's keyword wins a clash with a body's, then the auto-emitted magnetic blocks. Kept free of sif objects so the
        projection rule is unit-testable.

        Returns:
            A list of ``(kind, sif-data)`` tuples, one per resolved condition. The ``kind`` is the condition's
            discriminator (the same string the config's solver stack uses to select the solver for that condition).
        """
        specs: list[tuple[str, dict[str, SifValue]]] = []
        site: BoundarySite = BoundarySite(name=bg.name, measure=bg.measure, frame=self.frame)

        for cond in self._boundary_conditions(bg, tags_by_body):
            kind: str = cast("_DiscriminatedCondition", cond).kind
            specs.append((kind, dict(cond.on_boundary(site))))

        if self.physics in _MAGNETIC_FIELD_PHYSICS and bg.is_external:
            # Far field: Dirichlet A=0 on the outer shell. The WhitneyAV stacks pin the edge DOFs (`AV {e}`, the
            # solver's default-named variable); kept over `Infinity BC = Logical True`, whose Robin far-field
            # approximation is fragile and gave nonsensical fields here. The axisymmetric scalar stack pins its
            # nodal Potential instead.
            if self.physics is Physics.MAGNETOSTATICS_2D:
                specs.append((BoundaryKind.FAR_FIELD, {str(ElmerVariable.POTENTIAL): "Real 0"}))
            else:
                specs.append((BoundaryKind.FAR_FIELD, {"AV {e}": "Real 0"}))

        if self.physics is Physics.MAGNETOSTATICS_2D and bg.is_axis:
            # The same Dirichlet as the far field, but on r=0 it is the EXACT axis condition A_phi = 0, not an approximation.
            specs.append((BoundaryKind.SYMMETRY_AXIS, {str(ElmerVariable.POTENTIAL): "Real 0"}))

        return specs

    # -- physics-specific per-body wiring -----------------------------------

    def _wire_magnet_body(self, sim: sif.Simulation, body: sif.Body, group: "PhysicalGroup") -> None:
        """
        Give a magnet body its own Material carrying the ``Magnetization`` vector — the WhitneyAVSolver reads
        ``Magnetization 1/2/3`` from the *Material*, not a Body Force; |M| = Br/mu0 comes from the material and the
        direction from the per-region ``Magnetization`` condition. A magnet with no usable remanence/direction gets a
        commented ``! Magnetization`` marker rather than a silent zero field (``validate()`` promotes it to an error).
        """
        at: Mapping[str, object] = self.config.operating_point.as_mapping()
        data: dict[str, SifValue] = dict(group.material.to_elmer(at=at, runtime=runtime_variables(self.physics)))

        magnitude = group.material.magnetic.magnetization_magnitude(at=at)  # A/m

        conditions: list[Condition] = self._material_conditions(group.tags)
        magnetization: Condition | None = conditions[0] if conditions else None

        if magnitude is None:
            # Flagged as a magnet but no resolvable remanence; mark rather than emit a zero field.
            data["! Magnetization"] = "MISSING REMANENCE"
        elif magnetization is None or cast("_Oriented", magnetization).direction is None:
            # Material is a magnet but no (usable) orientation was found; mark rather than emit a zero field.
            data["! Magnetization"] = "MISSING DIRECTION TAG"
        else:
            data.update(magnetization.to_elmer(magnitude, self.frame))

        # Named per body (group.name is unique), so each Halbach slot is its own Material; the sif registry guards name clashes.
        body.material = sif.Material(sim, group.name, data=data)

    # -- output --------------------------------------------------------------

    def write(self, sim_dir: str) -> None:
        """Write the ELMERSOLVER_STARTINFO, the .sif, and the boundary/component id maps. Requires [`generate`][] to have built the simulation."""
        sim: sif.Simulation | None = self.sim
        if sim is None:
            raise RuntimeError("call generate() before write(); the simulation has not been built yet.")

        # Check that the dir exist and isn't a file
        if not os.path.exists(sim_dir):
            raise ValueError(f"Output directory {sim_dir} does not exist.")
        elif os.path.isfile(sim_dir):
            raise ValueError(f"Output directory {sim_dir} is a file.")

        sim.write_startinfo(sim_dir)
        sim.write_sif(sim_dir)
        if sim.boundaries:
            sim.write_boundary_ids(sim_dir)
        if sim.components:
            sim.write_component_ids(sim_dir)
