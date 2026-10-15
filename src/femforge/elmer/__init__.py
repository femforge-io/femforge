"""Elmer backend: the typed solver stack (``PhysicsConfig``), the mesh-name reconciliation a sif needs, and the backends that run Elmer."""

import lazy_loader


__getattr__, __dir__, __all__ = lazy_loader.attach_stub(__name__, __file__)
