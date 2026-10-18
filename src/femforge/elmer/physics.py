"""Discoverable vocabularies for the Elmer stage: the supported physics as an enum, not free strings."""

from __future__ import annotations

from enum import StrEnum


class Physics(StrEnum):
    """Which physics preset the sif writer wires; the string values double as the serialized spelling."""

    MAGNETOSTATICS = "magnetostatics"
    """The static magnetic field; it solves as ``MAGNETOSTATICS_2D`` under ``frame=Axisymmetric()``."""

    MAGNETOSTATICS_2D = "magnetostatics_2d"
    """Axisymmetric magnetostatics on the r–z half-plane with MagnetoDynamics2D, which also takes ``CurrentDensity`` coil drives."""

    THERMAL = "thermal"
    """Steady-state heat conduction."""

    ELECTROSTATICS = "electrostatics"
    """The electrostatic potential and field."""

    CURRENT_CONDUCTION = "current_conduction"
    """Static current conduction: the potential-driven current density through the conductors."""

    JOULE_HEATING = "joule_heating"
    """Current conduction coupled to heat conduction: the steady temperature its Joule losses produce."""

    COIL_MAGNETOSTATICS = "coil_magnetostatics"
    """Current conduction chained into magnetostatics: the field of a coil's own solved current distribution."""

    LINEAR_ELASTICITY = "linear_elasticity"
    """Linear elastic stress and strain."""


def condition_physics(physics: Physics) -> frozenset[Physics]:
    """
    The physics whose authored conditions are *active* under ``physics``: a coupled preset accepts its constituents'
    conditions too, so the same tags drive both the standalone and the coupled solves without re-authoring.
    """
    if physics is Physics.JOULE_HEATING:
        return frozenset({Physics.JOULE_HEATING, Physics.CURRENT_CONDUCTION, Physics.THERMAL})
    if physics is Physics.COIL_MAGNETOSTATICS:
        # CURRENT_CONDUCTION conditions (FixedPotential electrodes) are deliberately NOT activated: this stack has no
        # Potential variable (the source stage is CoilSolver, not StatCurrent), so shared electrode tags stay inert here.
        return frozenset({Physics.COIL_MAGNETOSTATICS, Physics.MAGNETOSTATICS})
    if physics is Physics.MAGNETOSTATICS_2D:
        # The same Magnetization tags drive the 3D and the axisymmetric solves without re-authoring.
        return frozenset({Physics.MAGNETOSTATICS_2D, Physics.MAGNETOSTATICS})
    return frozenset({physics})


def runtime_variables(physics: Physics) -> frozenset[str]:
    """
    The solved-field variable names a material property may depend on at runtime under ``physics``. A property whose
    parameter names are all in this set is emitted as a variable-dependent expression on the local solved field;
    otherwise it falls back to its operating-point value.
    """
    if physics in (Physics.THERMAL, Physics.JOULE_HEATING):
        return frozenset({"temperature"})
    return frozenset()
