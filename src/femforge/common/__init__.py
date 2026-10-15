"""Dependency-free primitives shared by every stage: config areas, tags, vectors, protocols, logging, progress UI, and console formatting."""

import lazy_loader


__getattr__, __dir__, __all__ = lazy_loader.attach_stub(__name__, __file__)
