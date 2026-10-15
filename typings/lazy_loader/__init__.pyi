"""lazy_loader: the stub-driven lazy attributes the femforge packages expose."""

from collections.abc import Callable

def attach_stub(package_name: str, filename: str) -> tuple[Callable[[str], object], Callable[[], list[str]], list[str]]: ...
