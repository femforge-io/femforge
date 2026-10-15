"""Scheduling helpers: a task whose exceptions are reported instead of swallowed."""

from asyncio import AbstractEventLoop, Task
from collections.abc import Coroutine

def create_task[T](coroutine: Coroutine[object, object, T], loop: AbstractEventLoop | None = ...) -> Task[T]: ...
def decorate_task[T](task: Task[T]) -> Task[T]: ...
