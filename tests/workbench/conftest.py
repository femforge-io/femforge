"""Workbench test fixtures: the VTK.wasm runtime is never downloaded, and the shell sees the built-in cards only."""

import os

import pytest

from femforge_workbench import app
from femforge_workbench.registry import discover


WASM_OVERRIDE = "VTK_WASM_DIR_OVERRIDE"


@pytest.fixture(autouse=True, scope="session")
def no_wasm_download(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Points trame-vtklocal at an empty directory, so enabling it copies nothing and skips the 80 MB download."""
    os.environ[WASM_OVERRIDE] = str(tmp_path_factory.mktemp("wasm"))


@pytest.fixture(autouse=True)
def builtin_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shell lists the built-in cards only, whatever workbench packages the venv has installed."""
    monkeypatch.setattr(app, "discover", lambda: discover([]))
