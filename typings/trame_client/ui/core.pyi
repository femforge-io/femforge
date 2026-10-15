"""AbstractLayout: a root element whose rendered HTML is pushed to the client as a named template."""

from collections.abc import Awaitable
from typing import Self

from trame_client.widgets.core import AbstractElement
from trame_server import Controller, Server, State

class AbstractLayout:
    def __init__(
        self,
        _server: Server,
        _root_elem: AbstractElement,
        template_name: str = ...,
        width: str | int = ...,
        height: str | int = ...,
        base_url: str = ...,
        iframe_builder: object = ...,
        **kwargs: object,
    ) -> None: ...
    @property
    def template_name(self) -> str: ...
    @property
    def root(self) -> AbstractElement: ...
    @property
    def html(self) -> object: ...
    @property
    def server(self) -> Server: ...
    @property
    def controller(self) -> Controller: ...
    @property
    def state(self) -> State: ...
    @property
    def ready(self) -> Awaitable[bool]: ...
    def flush_content(self) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, *args: object) -> None: ...
