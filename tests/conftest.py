"""Suite-wide isolation: no test reads or writes the developer's own telemetry or artifact store."""

import os

import pytest

from femforge.common.log import TELEMETRY_ENV, _reset_for_tests


def pytest_configure(config: pytest.Config) -> None:
    """Hide the developer's data roots before collection, whose imports can already log and install the telemetry sink."""
    _ = config
    _ = os.environ.pop(TELEMETRY_ENV, None)

    from femforge.cli import ROOT_ENV

    _ = os.environ.pop(ROOT_ENV, None)
    _reset_for_tests()
