"""RemoteControlledArea re-exported from trame_rca."""

from trame_client.widgets.core import AbstractElement
from trame_rca.view_adapter import RcaViewAdapter
from trame_server import Server

class RemoteControlledArea(AbstractElement):
    def __init__(self, name: str | None = ..., display: str = ..., **kwargs: object) -> None: ...
    def create_view_handler(
        self,
        window: object,
        encoder: str | None = ...,
        target_fps: float = ...,
        interactive_quality: int = ...,
        still_quality: int = ...,
    ) -> RcaViewAdapter: ...
    def add_view_handler(self, view_handler: RcaViewAdapter) -> None: ...

def initialize(server: Server) -> None: ...
