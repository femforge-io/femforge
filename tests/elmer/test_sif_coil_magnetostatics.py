"""
Coil-magnetostatics (CoilSolver -> WhitneyAV on the elemental coil current) sif generation: the solver stack, the
per-phase Component blocks, the boundary-terminated Coil Start/End electrodes, and the merged boundary blocks.
"""

from __future__ import annotations

import re

import pytest

from femforge.common.tags import CurrentDir, TagKey
from femforge.elmer.config import CoilCurrentSolver, MagnetoDynamicsCalcFieldsSolver, MagnetoDynamicsSolver, ResultOutputSolver, physics_config
from femforge.elmer.physics import Physics, condition_physics
from femforge.elmer.variables import ElmerVariable, elemental
from femforge.elmer.sim import SolverConfigWarning
from femforge.meshing.groups import PhysicalGroup
from femforge.physical.conditions import CoilDrive, CoilTerminal, FixedVoltage
from femforge.physical.domain import EntityTag
from femforge.materials.air import Air
from femforge.materials.copper import Copper
from femforge.materials.pcb import FR4
from femforge.physical.units import U
from tests.elmer.test_sif_smoke import FakeBoundaryGroup, FakePhysicalGroup, _boundary_blocks, _write_sif


def _coil_groups() -> list[PhysicalGroup]:
    """
    One driven phase in a dielectric domain: the phase's winding plus its entry/exit electrode
    bodies all carry the SAME CoilDrive (authored once on the phase tag, matched to every part of the phase), the
    electrode bodies additionally carry their CoilTerminal marker tags.
    """
    phase = EntityTag(selector={TagKey.PHASE: "0"}, conditions=[CoilDrive(current=0.5 * U.A)])
    entry = EntityTag(selector={TagKey.CURRENT_DIR: CurrentDir.ENTRY, TagKey.PHASE: "0"}, conditions=[CoilTerminal(role="start")])
    exit_ = EntityTag(selector={TagKey.CURRENT_DIR: CurrentDir.EXIT, TagKey.PHASE: "0"}, conditions=[CoilTerminal(role="end")])
    return [
        FakePhysicalGroup(1, "COPPER_PHASE0", Copper(), [phase]),
        FakePhysicalGroup(2, "COPPER_PHASE0_ENTRY0", Copper(), [phase, entry]),
        FakePhysicalGroup(3, "COPPER_PHASE0_EXIT0", Copper(), [phase, exit_]),
        FakePhysicalGroup(4, "FR4", FR4(), []),
        FakePhysicalGroup(5, "AIR", Air(), []),
    ]


def _coil_boundary_groups() -> list[FakeBoundaryGroup]:
    """The electrode shells (external: the boundary-terminated stub end faces) plus their winding interfaces and the air shell."""
    return [
        FakeBoundaryGroup(11, "COPPER_PHASE0_ENTRY0__EXT", ("COPPER_PHASE0_ENTRY0",), [101]),
        FakeBoundaryGroup(12, "COPPER_PHASE0__COPPER_PHASE0_ENTRY0", ("COPPER_PHASE0", "COPPER_PHASE0_ENTRY0"), [102]),
        FakeBoundaryGroup(13, "COPPER_PHASE0_EXIT0__EXT", ("COPPER_PHASE0_EXIT0",), [103]),
        FakeBoundaryGroup(14, "COPPER_PHASE0__COPPER_PHASE0_EXIT0", ("COPPER_PHASE0", "COPPER_PHASE0_EXIT0"), [104]),
        FakeBoundaryGroup(15, "AIR__EXT", ("AIR",), [105]),
    ]


def _coil_sif() -> str:
    return _write_sif(physics=Physics.COIL_MAGNETOSTATICS, groups=_coil_groups(), boundary_groups=_coil_boundary_groups())


def test_coil_magnetostatics_chains_coil_solver_into_whitney_av() -> None:
    stack = physics_config(Physics.COIL_MAGNETOSTATICS)
    assert isinstance(stack.solver(CoilCurrentSolver), CoilCurrentSolver)
    assert isinstance(stack.solver(MagnetoDynamicsSolver), MagnetoDynamicsSolver)
    assert isinstance(stack.solver(MagnetoDynamicsCalcFieldsSolver), MagnetoDynamicsCalcFieldsSolver)

    sif = _coil_sif()
    assert 'Procedure = "CoilSolver" "CoilSolver"' in sif
    assert 'Procedure = "MagnetoDynamics" "WhitneyAVSolver"' in sif
    # The AV source is CoilSolver's elemental (DG) current — the nodal bridge dilutes thin conductors ~2x —
    # and Jfix stays on (the DG field is still not exactly solenoidal edge-element-wise).
    assert "Use Elemental CoilCurrent = True" in sif
    assert "Fix Input Current Density = True" in sif
    assert "Permeability of Vacuum" in sif
    # The old StatCurrent bridge must be gone: no nodal Volume Current forwarding, no Potential-flux SaveScalars.
    assert "Volume Current" not in sif
    assert "StatCurrentSolve" not in sif


def test_coil_solver_keeps_the_massive_distribution() -> None:
    # Normalize Coil Current rescales |J| to a uniform magnitude ("as for stranded coil") — on a varying
    # cross-section (traces vs stubs vs vias) that authors a non-conservative source Jfix then redistributes
    # (measured 0.66x drive current in the winding). The Desired Coil Current scaling is a separate step.
    assert "Normalize Coil Current = False" in _coil_sif()
    assert CoilCurrentSolver().to_elmer()["Calculate Elemental Fields"] is True
    assert CoilCurrentSolver().to_elmer()["Coil Closed"] is False


def test_vtu_saves_only_the_consumed_field() -> None:
    # The pipeline reads only B back (log energy + VTU cross-check/sampling); an unrestricted VTU on the
    # big L meshes carries ~710 MB of unread fields, and H recovery is redundant outright (μr ≡ 1, H = B/μ0).
    sif = _coil_sif()
    assert f'Vector Field 1 = String "{elemental(ElmerVariable.MAGNETIC_FLUX_DENSITY)}"' in sif
    assert "Calculate Magnetic Field Strength = False" in sif
    assert "Save Geometry Ids = True" in sif


def test_saved_field_lists_expand_to_numbered_keywords() -> None:
    emitted = ResultOutputSolver(saved_vector_fields=['String "a"', 'String "b"'], saved_scalar_fields=['String "c"']).to_elmer()
    assert emitted["Vector Field 1"] == 'String "a"'
    assert emitted["Vector Field 2"] == 'String "b"'
    assert emitted["Scalar Field 1"] == 'String "c"'
    assert "Vector Field" not in emitted


def test_each_drive_becomes_one_component_over_all_its_bodies() -> None:
    sif = _coil_sif()
    components: list[str] = re.findall(r"Component \d+\n(?:.*\n)*?End", sif)
    assert len(components) == 1
    block = components[0]
    # Master Bodies = every body carrying the drive: the winding and both electrodes (sif body ids 1..3).
    assert re.search(r"Master Bodies\(3\) = 1 2 3", block)
    assert 'Coil Type = "massive"' in block
    m = re.search(r"Desired Coil Current = ([0-9.eE+-]+)", block)
    assert m and float(m.group(1)) == pytest.approx(0.5)


def test_coil_drive_is_not_a_body_force() -> None:
    assert "Body Force" not in _coil_sif()


def test_undriven_bodies_get_the_field_only_equation() -> None:
    # CoilSolver hard-errors when a body in its equation has no Component, so its equation covers exactly the
    # driven bodies; WhitneyAV + CalcFields stay in BOTH equations (splitting those is the CalcFields SIGSEGV).
    sif = _coil_sif()
    assert "Field Equation" in sif

    body_blocks = {m.group(1): m.group(0) for m in re.finditer(r"Body \d+\n(?:.*\n)*?  Name = \"(\w+)\"\n(?:.*\n)*?End", sif)}
    equations = {name: re.search(r"Equation = (\d+)", block).group(1) for name, block in body_blocks.items()}  # pyright: ignore[reportOptionalMemberAccess]
    assert equations["COPPER_PHASE0"] == equations["COPPER_PHASE0_ENTRY0"] == equations["COPPER_PHASE0_EXIT0"]
    assert equations["FR4"] == equations["AIR"]
    assert equations["FR4"] != equations["COPPER_PHASE0"], "undriven bodies must not carry the CoilSolver equation"


def test_all_driven_bodies_keep_one_equation_when_nothing_else_exists() -> None:
    sif = _write_sif(physics=Physics.COIL_MAGNETOSTATICS, groups=_coil_groups()[:3], boundary_groups=_coil_boundary_groups()[:4])
    assert "Field Equation" not in sif


def test_coil_terminals_land_only_on_the_external_electrode_faces() -> None:
    blocks = _boundary_blocks(_coil_sif())
    # The terminal marker reaches the stub's exposed end face (merged with the far field there), NOT its
    # interior interface with the winding.
    start_blocks = [name for name, block in blocks.items() if "Coil Start = Logical True" in block]
    end_blocks = [name for name, block in blocks.items() if "Coil End = Logical True" in block]
    assert start_blocks == ["COPPER_PHASE0_ENTRY0__EXT_coil_terminal_far_field"]
    assert end_blocks == ["COPPER_PHASE0_EXIT0__EXT_coil_terminal_far_field"]


def test_overlapping_conditions_merge_into_one_boundary_block() -> None:
    # Elmer honors ONE BC section per boundary element: the electrode end face needs its Coil Start AND the
    # far-field AV pin in the SAME block, or one of them is silently dropped.
    blocks = _boundary_blocks(_coil_sif())
    entry = blocks["COPPER_PHASE0_ENTRY0__EXT_coil_terminal_far_field"]
    assert "Coil Start = Logical True" in entry
    assert "AV {e} = Real 0" in entry
    assert "Target Boundaries(1) = 11" in entry
    # And no second block targets the same boundary id.
    targets = re.findall(r"Target Boundaries\(1\) = (\d+)", "\n\n".join(blocks.values()))
    assert len(targets) == len(set(targets))


def test_external_only_conditions_skip_axis_groups() -> None:
    # is_external now excludes on_axis groups, so an external_only condition (the terminal marker) can never land
    # on a symmetry-axis boundary — and a non-2D physics attaches nothing else to it either.
    groups = _coil_boundary_groups()
    groups[0] = FakeBoundaryGroup(11, "COPPER_PHASE0_ENTRY0__AXIS", ("COPPER_PHASE0_ENTRY0",), [101], on_axis=True)
    blocks = _boundary_blocks(_write_sif(physics=Physics.COIL_MAGNETOSTATICS, groups=_coil_groups(), boundary_groups=groups))

    assert not any("Coil Start = Logical True" in block for block in blocks.values())
    assert not any(name.startswith("COPPER_PHASE0_ENTRY0__AXIS") for name in blocks)  # the axis group emits no block at all
    assert any("Coil End = Logical True" in block for block in blocks.values())  # the untouched exit terminal still lands


def test_far_field_pins_av_on_every_external_shell() -> None:
    blocks = _boundary_blocks(_coil_sif())
    external = {name for name in blocks if name.endswith("_far_field")}
    assert external == {
        "COPPER_PHASE0_ENTRY0__EXT_coil_terminal_far_field",
        "COPPER_PHASE0_EXIT0__EXT_coil_terminal_far_field",
        "AIR__EXT_far_field",
    }
    assert all("AV {e} = Real 0" in blocks[name] for name in external)


def test_fixed_voltage_electrodes_are_inert_under_the_coil_solve() -> None:
    # No Potential variable exists in this stack (the source stage is CoilSolver, not StatCurrent), so a shared
    # config's CURRENT_CONDUCTION electrode tags must not emit.
    assert condition_physics(Physics.COIL_MAGNETOSTATICS) == frozenset({Physics.COIL_MAGNETOSTATICS, Physics.MAGNETOSTATICS})
    groups = _coil_groups()
    groups[1].tags.append(EntityTag(selector={TagKey.CURRENT_DIR: CurrentDir.ENTRY}, conditions=[FixedVoltage(value=1 * U.V)]))
    sif = _write_sif(physics=Physics.COIL_MAGNETOSTATICS, groups=groups, boundary_groups=_coil_boundary_groups())
    assert "fixed_voltage" not in sif
    assert "Save Scalars" not in sif


def test_the_authoring_tag_lookup_requires_a_tag_carrying_the_condition() -> None:
    from femforge.elmer.sim import Sim

    orphan = CoilDrive(current=1 * U.A)
    with pytest.raises(ValueError, match="CoilDrive condition not found on any"):
        _ = Sim._tag_of([EntityTag(selector={TagKey.PHASE: "0"}, conditions=[])], orphan)


def test_driveless_coil_solve_warns() -> None:
    groups: list[PhysicalGroup] = [FakePhysicalGroup(1, "COPPER_PHASE0", Copper(), []), FakePhysicalGroup(2, "AIR", Air(), [])]
    with pytest.warns(SolverConfigWarning, match="no CoilDrive"):
        _ = _write_sif(physics=Physics.COIL_MAGNETOSTATICS, groups=groups, boundary_groups=[])
