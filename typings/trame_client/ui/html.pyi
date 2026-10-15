"""DivLayout: a plain div as a template root."""

from collections.abc import Mapping

from trame_client.ui.core import AbstractLayout
from trame_server import Server

class DivLayout(AbstractLayout):
    def __init__(self, _server: Server, template_name: str = ..., connect_parent: bool = ..., style: str | Mapping[str, str] | None = ..., classes: str | None = ..., **kwargs: object) -> None: ...
