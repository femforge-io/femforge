"""Typed, provenance-tracked FEA orchestration: CAD -> mesh -> solver -> result."""

import lazy_loader


__version__ = "0.1.0"

__getattr__, __dir__, __all__ = lazy_loader.attach_stub(__name__, __file__)
