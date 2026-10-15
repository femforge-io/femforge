"""Method decorators TrameComponent binds on construction: triggers, state changes, controller slots and life-cycle hooks."""

from collections.abc import Callable
from typing import Protocol

type Handler = Callable[..., object]

class Decorate(Protocol):
    def __call__[F: Handler](self, fn: F, /) -> F: ...

class Named(Protocol):
    def __call__[F: Handler](self, *names: str) -> Callable[[F], F]: ...

def trigger[F: Handler](*names: str) -> Callable[[F], F]: ...
def change[F: Handler](*names: str) -> Callable[[F], F]: ...
def hot_reload[F: Handler](func: F) -> F: ...

class ControllerDecorators:
    once: Named
    add: Named
    add_task: Named
    set: Named

class LifeCycle:
    server_start: Decorate
    server_bind: Decorate
    server_ready: Decorate
    client_connected: Decorate
    client_exited: Decorate
    server_exited: Decorate
    server_reload: Decorate
    error: Decorate
    exception: Decorate
    server_start_task: Decorate
    server_ready_task: Decorate
    client_connected_task: Decorate
    client_exited_task: Decorate
    server_exited_task: Decorate
    server_reload_task: Decorate

controller: ControllerDecorators
life_cycle: LifeCycle
