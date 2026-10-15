"""Local type stub for the parts of the ``magpylib`` API this project calls.

``magpylib`` ships no type information, so ``magpylib.Collection`` / ``.getB`` and
``magpylib.magnet.Cuboid`` resolve to Unknown under basedpyright. This stub SEALS
only the surface ``validation`` actually uses -- building a ``Collection`` of
``Cuboid`` sources and evaluating ``B`` at observer points -- so a new magpylib
call fails type checks until its signature is added here (the same seal as the
gmsh stubs).
"""

import numpy as np
from numpy.typing import NDArray

class Collection:
    def __init__(self, *children: object) -> None: ...
    def getB(self, observers: object) -> NDArray[np.float64]: ...
