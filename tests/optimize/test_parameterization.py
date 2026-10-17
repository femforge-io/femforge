"""A Parameterization maps a parameter-space point to a concrete SimulationConfig and reports its free-variable schema; the contract the grid search walks."""

import pytest

from femforge.common.tags import TagKey
from femforge.config import SimulationConfig
from femforge.meshing.config import MeshConfig
from femforge.optimize.parameterization import GlobalMeshSize, Knob, Parameterization, RegionMeshSize
from femforge.physical.domain import DomainConfig, EntityTag
from femforge.physical.units import U


def _param() -> Parameterization:
    base = SimulationConfig(
        mesh=MeshConfig(step="data/motor.step", global_mesh_size=1.0 * U.m),
        domain=DomainConfig(tags=[EntityTag(selector={TagKey.MATERIAL: "Air"}, mesh_size=500 * U.mm)]),
    )
    return Parameterization(
        base=base,
        knobs=[
            GlobalMeshSize(name="global", low=0.2, high=1.0),
            RegionMeshSize(name="gap", tag="MaterialAir", low=0.05, high=0.5),
        ],
    )


def test_space_keys_match_knob_names() -> None:
    space = _param().space()
    assert set(space) == {"global", "gap"}
    assert space["global"].low == 0.2 and space["global"].high == 1.0
    assert space["gap"].low == 0.05 and space["gap"].high == 0.5


@pytest.mark.parametrize(
    ("point", "expected_global", "expected_gap"),
    [
        ({"global": 0.2, "gap": 0.05}, 0.2, 0.05),  # grid corner (both low)
        ({"global": 1.0, "gap": 0.5}, 1.0, 0.5),  # grid corner (both high)
        ({"global": 0.5, "gap": 0.1}, 0.5, 0.1),  # interior
    ],
)
def test_to_config_applies_knobs_at_grid_corners(point: dict[str, float], expected_global: float, expected_gap: float) -> None:
    config = _param().to_config(point)
    assert config.mesh.global_mesh_size == expected_global * U.m
    gap_tag = next(tag for tag in config.domain.tags if tag.name == "MaterialAir")
    assert gap_tag.mesh_size == expected_gap * U.m
    # The non-knob fields come straight from the base.
    assert config.mesh.step == "data/motor.step"


def test_to_config_does_not_mutate_base() -> None:
    param = _param()
    param.to_config({"global": 0.2, "gap": 0.05})
    assert param.base.mesh.global_mesh_size == 1.0 * U.m
    assert param.base.domain.tags[0].mesh_size == 500 * U.mm


def test_to_config_rejects_mismatched_point_keys() -> None:
    param = _param()
    with pytest.raises(ValueError, match="do not match knobs"):
        param.to_config({"global": 0.5})  # missing "gap"
    with pytest.raises(ValueError, match="do not match knobs"):
        param.to_config({"global": 0.5, "gap": 0.1, "bogus": 1.0})  # unknown key


def test_region_knob_unknown_tag_raises() -> None:
    base = SimulationConfig(domain=DomainConfig(tags=[EntityTag(selector={TagKey.MATERIAL: "Air"})]))
    param = Parameterization(base=base, knobs=[RegionMeshSize(name="gap", tag="Magnet", low=0.0, high=1.0)])
    with pytest.raises(ValueError, match="no region tagged 'Magnet'"):
        param.to_config({"gap": 0.1})


def test_base_knob_apply_is_abstract() -> None:
    with pytest.raises(NotImplementedError):
        _ = Knob(name="k", low=0.0, high=1.0).apply(SimulationConfig(), 0.5)


def test_knobs_from_another_package_are_accepted_and_dumped_with_their_fields() -> None:
    class Description(Knob):
        """A knob defined outside femforge, writing the run description."""

        kind: str = "description"
        prefix: str = "point"

        def apply(self, config: SimulationConfig, value: float) -> SimulationConfig:
            meta = config.meta.model_copy(update={"description": f"{self.prefix}={value}"})
            return config.model_copy(update={"meta": meta})

    param = Parameterization(base=SimulationConfig(), knobs=[Description(name="d", low=0.0, high=1.0, prefix="x")])
    assert param.to_config({"d": 0.5}).meta.description == "x=0.5"
    assert param.model_dump()["knobs"][0]["prefix"] == "x"
