"""Griffe extension that reads a package's redundant aliases (``import X as X``) as its ``__all__`` when it declares none."""

import ast
from pathlib import Path
from typing import Any, override

import griffe


def _redundant_aliases(path: Path) -> list[str]:
    """Return the names a source file re-exports through ``from m import X as X``, in source order."""
    if not path.is_file():
        return []

    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]

    return [alias.name for node in imports for alias in node.names if alias.asname == alias.name]


class RedundantAliasExports(griffe.Extension):
    """Set the exports of every package without ``__all__`` from the redundant aliases in its ``__init__.pyi`` and ``__init__.py``."""

    @override
    def on_package(self, *, pkg: griffe.Module, loader: griffe.GriffeLoader, **kwargs: Any) -> None:  # pyright: ignore[reportExplicitAny, reportAny]
        """Walk the loaded package and fill the exports of its export-less subpackages."""
        packages = [pkg]
        while packages:
            module = packages.pop()
            packages.extend(member for member in module.modules.values() if not member.is_alias and member.is_init_module)
            if module.exports is not None or not isinstance(module.filepath, Path):
                continue

            init = module.filepath
            names = dict.fromkeys(_redundant_aliases(init.with_suffix(".pyi")) + _redundant_aliases(init))
            if names:
                module.exports = [name for name in names if name in module.members]
