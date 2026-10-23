"""Boundary conditions on a named face group merge with the bordering bodies' conditions, the face's last."""

from __future__ import annotations

import re

import pytest

from femforge.common.tags import TagKey
from femforge.common.vector import Direction
from femforge.elmer.physics import Physics
from femforge.elmer.sim import Sim
from femforge.frames import Axisymmetric, Solid
from femforge.materials.pcb import FR4
from femforge.meshing.boundary import BoundaryGroup
from femforge.meshing.groups import PhysicalGroup
from femforge.physical.conditions import BoundarySite, ConditionUnion, Convection, FixedTemperature, HeatFlux, Magnetization
from femforge.physical.domain import EntityTag
from femforge.physical.units import U
from tests.elmer.test_sif_smoke import FakeBoundaryGroup, FakePhysicalGroup, _boundary_blocks, _write_sif


def _convection() -> Convection:
    return Convection(coefficient=12 * U.W / (U.m**2 * U.K), ext_temperature=300 * U.K)


def _box(*conditions: ConditionUnion) -> list[PhysicalGroup]:
    body_tag = EntityTag(selector={TagKey.NAME: "Box"}, conditions=list(conditions))

    return [FakePhysicalGroup(1, "BOX", FR4(), [body_tag])]


def _shell_and_tip(*tip_tags: EntityTag) -> list[FakeBoundaryGroup]:
    return [
        FakeBoundaryGroup(11, "BOX__EXT", ("BOX",), [101]),
        FakeBoundaryGroup(12, "TIP", ("BOX",), [102], tags=list(tip_tags), measure=1e-4, named=True),
    ]


def _tip_tag(*conditions: ConditionUnion) -> EntityTag:
    return EntityTag(selector={TagKey.NAME: "Tip"}, conditions=list(conditions))


def _temperature(block: str) -> float:
    match = re.search(r"^\s*Temperature = ([-0-9.eE+]+)", block, re.MULTILINE)
    assert match is not None

    return float(match.group(1))


def test_a_face_condition_lands_only_on_its_named_group() -> None:
    sif = _write_sif(physics=Physics.THERMAL, groups=_box(), boundary_groups=_shell_and_tip(_tip_tag(FixedTemperature(value=350 * U.K))))
    blocks = _boundary_blocks(sif)

    assert set(blocks) == {"TIP_fixed_temperature"}
    assert "Target Boundaries(1) = 12" in blocks["TIP_fixed_temperature"]


def test_an_external_only_body_condition_also_reaches_the_named_group() -> None:
    sif = _write_sif(physics=Physics.THERMAL, groups=_box(_convection()), boundary_groups=_shell_and_tip(_tip_tag(HeatFlux(value=40 * U.W / U.m**2))))
    blocks = _boundary_blocks(sif)

    assert set(blocks) == {"BOX__EXT_convection", "TIP_convection_heat_flux"}
    assert "Heat Transfer Coefficient" in blocks["TIP_convection_heat_flux"]


def test_a_body_value_and_a_different_face_value_on_one_face_fail_naming_both_tags() -> None:
    groups = _box(FixedTemperature(value=300 * U.K))
    boundary_groups = _shell_and_tip(_tip_tag(FixedTemperature(value=350 * U.K)))

    with pytest.raises(ValueError, match=r"TIP: Temperature is 300 from Fixed Temperature \(tag BOX\) but 350 from Fixed Temperature \(tag TIP\)"):
        _ = _write_sif(physics=Physics.THERMAL, groups=groups, boundary_groups=boundary_groups)


def test_a_body_value_the_face_repeats_merges() -> None:
    blocks = _boundary_blocks(_write_sif(physics=Physics.THERMAL, groups=_box(FixedTemperature(value=300 * U.K)), boundary_groups=_shell_and_tip(_tip_tag(FixedTemperature(value=300 * U.K)))))

    assert _temperature(blocks["TIP_fixed_temperature_fixed_temperature"]) == pytest.approx(300.0)


def test_a_film_on_a_face_held_at_a_fixed_temperature_fails() -> None:
    with pytest.raises(ValueError, match=r"TIP: Convection \(tag BOX\) has no effect under the fixed Temperature of Fixed Temperature \(tag TIP\); remove one$"):
        _ = _write_sif(physics=Physics.THERMAL, groups=_box(_convection()), boundary_groups=_shell_and_tip(_tip_tag(FixedTemperature(value=350 * U.K))))


def test_a_tag_matching_the_body_and_the_face_lands_once() -> None:
    everywhere = EntityTag(conditions=[FixedTemperature(value=320 * U.K)])
    groups: list[PhysicalGroup] = [FakePhysicalGroup(1, "BOX", FR4(), [everywhere])]
    blocks = _boundary_blocks(_write_sif(physics=Physics.THERMAL, groups=groups, boundary_groups=_shell_and_tip(everywhere)))

    assert set(blocks) == {"BOX__EXT_fixed_temperature", "TIP_fixed_temperature"}


def test_a_boundary_condition_emits_through_the_site_of_its_group(monkeypatch: pytest.MonkeyPatch) -> None:
    sites: list[BoundarySite] = []

    def recording(self: FixedTemperature, site: BoundarySite) -> dict[str, float | str]:
        sites.append(site)

        return self.to_elmer()

    monkeypatch.setattr(FixedTemperature, "on_boundary", recording)
    _ = _write_sif(physics=Physics.THERMAL, groups=_box(), boundary_groups=_shell_and_tip(_tip_tag(FixedTemperature(value=350 * U.K))))

    assert sites == [BoundarySite(name="TIP", measure=1e-4, frame=Solid())]


def test_the_frame_check_covers_the_face_tags() -> None:
    off_axis = _tip_tag(Magnetization(direction=Direction(1, 0, 0)))

    with pytest.raises(ValueError, match="no form in the axisymmetric"):
        _ = Sim(_box(), physics=Physics.THERMAL, boundary_groups=_shell_and_tip(off_axis), frame=Axisymmetric())  # pyright: ignore[reportArgumentType]  # structural fake


def test_an_adjacency_group_has_no_tags_measure_or_name() -> None:
    group = BoundaryGroup(gmsh_id=1, name="BOX__EXT", bodies=("BOX",), face_tags=[1])

    assert (group.tags, group.measure, group.named) == ([], None, False)
