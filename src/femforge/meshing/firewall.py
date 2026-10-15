"""Keeping gmsh out of a process: an import hook for an entry point that must never load it."""

from __future__ import annotations

import importlib.abc
import sys
from collections.abc import Sequence
from importlib.machinery import ModuleSpec
from types import ModuleType


__all__ = ["block_gmsh"]

GMSH = "gmsh"
"""The module the hook refuses, with its submodules."""


class _GmshBlocker(importlib.abc.MetaPathFinder):
    """Refuses every import of gmsh."""

    def find_spec(self, fullname: str, path: Sequence[str] | None, target: ModuleType | None = None) -> ModuleSpec | None:
        if fullname == GMSH or fullname.startswith(f"{GMSH}."):
            raise ImportError("gmsh is blocked in this process; mesh through a WorkerMesher instead.", name=fullname)

        return None


def block_gmsh() -> None:
    """Make every later ``import gmsh`` in this process raise ImportError, so an entry point can prove it never loads gmsh."""
    if GMSH in sys.modules:
        raise RuntimeError("gmsh is already loaded in this process, so it can no longer be blocked.")

    if not any(isinstance(finder, _GmshBlocker) for finder in sys.meta_path):
        sys.meta_path.insert(0, _GmshBlocker())
