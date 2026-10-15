"""The window side of trame-rca: the protocol a streamed area implements and the VTK wrapper."""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from numpy.typing import NDArray

@runtime_checkable
class RemoteControlledAreaProtocol(Protocol):
    @property
    def img_cols_rows(self) -> tuple[NDArray[object], int, int]: ...
    def process_resize_event(self, width: int, height: int) -> None: ...
    def process_interaction_event(self, event: Mapping[str, object]) -> None: ...

def window_wrapper(window: object) -> RemoteControlledAreaProtocol: ...
