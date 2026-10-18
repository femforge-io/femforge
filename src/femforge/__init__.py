"""Finite-element analysis as a typed Python API: one config takes a tagged CAD model through meshing and solving to results that carry their units."""

import lazy_loader


__version__ = "0.1.0"

__getattr__, __dir__, __all__ = lazy_loader.attach_stub(__name__, __file__)
