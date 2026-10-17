"""The free variables: a point in parameter space -> a concrete SimulationConfig, through the knobs a study declares."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

from femforge.config import SimulationConfig
from femforge.physical.units import Length, Quantity, quantity


class ParamSpec(BaseModel):
    """The declared closed ``[low, high]`` bounds of one free variable — the schema entry [`Parameterization.space`][] returns, not the sample set."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    low: float
    """Inclusive lower bound of the free variable, in ``unit``."""

    high: float
    """Inclusive upper bound of the free variable, in ``unit``."""

    unit: str | None = None
    """The unit the bounds and every point value are in, such as ``"mm"``; None for a dimensionless knob."""


class BaseKnob(BaseModel):
    """
    Base of every knob: one named free variable that maps a scalar onto a field of a [`SimulationConfig`][femforge.config.SimulationConfig].

    Subclass [`Knob`][] for a dimensionless value or [`LengthKnob`][] for a length; never instantiated directly.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    name: str
    """Parameter-space key this knob reads from a point dict (unique within a Parameterization)."""

    def spec(self) -> ParamSpec:
        """The declared range of this knob, for [`Parameterization.space`][]. The base raises."""
        raise NotImplementedError

    def apply(self, config: SimulationConfig, value: float) -> SimulationConfig:
        """Return a copy of ``config`` with this knob set to ``value``; implementations must not mutate ``config`` (the base config is shared across every grid point). The base raises."""
        raise NotImplementedError


class Knob(BaseKnob):
    """
    Base: a free variable with plain-number bounds, such as a drive scale.

    Subclasses pin a ``kind`` discriminator and implement [`apply`][femforge.optimize.BaseKnob.apply]; never instantiated directly.
    """

    low: float
    """Inclusive lower bound of the knob's value."""

    high: float
    """Inclusive upper bound of the knob's value."""

    def spec(self) -> ParamSpec:
        """The declared range of this knob, without a unit."""
        return ParamSpec(low=self.low, high=self.high)


class LengthKnob(BaseKnob):
    """
    Base: a free variable over a length, whose point values are numbers in the unit ``low`` is written in.

    Subclasses pin a ``kind`` discriminator and implement [`apply`][femforge.optimize.BaseKnob.apply], building each length with [`length`][].
    """

    low: Length
    """Inclusive lower bound of the knob's length; its unit is the unit of every point value."""

    high: Length
    """Inclusive upper bound of the knob's length."""

    @property
    def unit(self) -> str:
        """The unit of ``low``, abbreviated (``"mm"``), that point values are read in."""
        return f"{self.low.units:~}"

    def spec(self) -> ParamSpec:
        """The declared range of this knob as numbers in ``unit``."""
        return ParamSpec(low=self.low.magnitude, high=self.high.m_as(self.unit), unit=self.unit)

    def length(self, value: float) -> Quantity:
        """The point value ``value`` as a length in ``unit``."""
        return quantity(value, self.unit)


class GlobalMeshSize(LengthKnob):
    """Knob over [`MeshConfig.global_mesh_size`][], the domain-wide target element size."""

    kind: Literal["global_mesh_size"] = "global_mesh_size"
    """The discriminator a serialized parameterization reads this knob back by."""

    def apply(self, config: SimulationConfig, value: float) -> SimulationConfig:
        """A copy of ``config`` with ``global_mesh_size`` set to ``value`` in the knob's unit."""
        mesh = config.mesh.model_copy(update={"global_mesh_size": self.length(value)})
        return config.model_copy(update={"mesh": mesh})


class RegionMeshSize(LengthKnob):
    """Knob over one tagged region's [`EntityTag.mesh_size`][], such as a finer air gap than the bulk.

    Targets the region by ``tag`` name and raises when the base config has no such region.
    """

    kind: Literal["region_mesh_size"] = "region_mesh_size"
    """The discriminator a serialized parameterization reads this knob back by."""

    tag: str
    """Derived `EntityTag.name` (e.g. ``"Phase0"``) of the region whose mesh_size this knob sets."""

    def apply(self, config: SimulationConfig, value: float) -> SimulationConfig:
        """A copy of ``config`` with the ``mesh_size`` of the region named ``tag`` set to ``value`` in the knob's unit."""
        if not any(tag.name == self.tag for tag in config.domain.tags):
            raise ValueError(f"no region tagged {self.tag!r} in the base config")

        size = self.length(value)
        # Replace only the matching tag; other tags are shared unchanged — apply must not mutate the base.
        tags = [tag.model_copy(update={"mesh_size": size}) if tag.name == self.tag else tag for tag in config.domain.tags]

        domain = config.domain.model_copy(update={"tags": tags})
        return config.model_copy(update={"domain": domain})


class Parameterization(BaseModel):
    """
    Maps a point in parameter space -> a concrete [`SimulationConfig`][femforge.config.SimulationConfig].

    Holds a fixed ``base`` config plus the knobs that perturb it — the single place that encodes which knobs vary.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, use_attribute_docstrings=True)

    base: SimulationConfig
    """The fixed config the knobs perturb; everything not driven by a knob comes from here."""

    knobs: list[SerializeAsAny[BaseKnob]] = Field(default_factory=list)
    """The free variables, each writing one named scalar onto the config; any [`BaseKnob`][] subclass, from any package."""

    def to_config(self, point: dict[str, float]) -> SimulationConfig:
        """Build the [`SimulationConfig`][femforge.config.SimulationConfig] for one parameter-space ``point`` (knob name -> value), leaving the base untouched.

        The point's keys must be exactly the knob names; a missing or unknown key raises.
        """
        names: set[str] = {knob.name for knob in self.knobs}
        missing: set[str] = names - point.keys()
        unknown: set[str] = point.keys() - names
        if missing or unknown:
            raise ValueError(f"point keys {sorted(point)} do not match knobs {sorted(names)} (missing {sorted(missing)}, unknown {sorted(unknown)})")

        config: SimulationConfig = self.base
        for knob in self.knobs:
            config = knob.apply(config, point[knob.name])

        return config

    def space(self) -> dict[str, ParamSpec]:
        """The free variables keyed by name, each with its declared bounds — the schema a search walks (or a future optimizer samples)."""
        return {knob.name: knob.spec() for knob in self.knobs}
