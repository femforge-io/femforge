"""ObjectManagerAPI: the wslink protocol that serves the mirror its object states."""

from collections.abc import Sequence

from vtkmodules.vtkSerializationManager import vtkObjectManager

class ObjectManagerAPI:
    vtk_object_manager: vtkObjectManager
    def __init__(self, addon_serdes_registrars: Sequence[object] | None = ...) -> None: ...
    def get_batch(self, state_ids: Sequence[int], hash_keys: Sequence[str]) -> dict[str, object]: ...
    def get_status(self, obj_id: int) -> dict[str, object]: ...
