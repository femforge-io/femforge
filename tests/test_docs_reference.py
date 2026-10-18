"""Every public package, root module and workbench module has a ``:::`` directive on a reference page, and every directive resolves."""

import ast
import importlib.util
import pkgutil
import re
from collections import Counter
from pathlib import Path

import pytest

import femforge
import femforge_workbench


SRC = Path(femforge.__file__).parents[1]
"""The source tree under test."""

PACKAGE = SRC / "femforge"
"""The femforge package directory."""

WORKBENCH = Path(femforge_workbench.__file__).parent
"""The femforge_workbench package directory."""

REFERENCE = Path(__file__).parents[1] / "docs" / "reference"
"""The API reference pages."""

DIRECTIVE = re.compile(r"^:::\s+(?P<name>[\w.]+)\s*$", re.MULTILINE)
"""A mkdocstrings directive line and the dotted module path it renders."""

PLAN_EXTRAS = ("femforge.execution.docker.image", "femforge.elmer.runtime.docker_image", "femforge.schemas")
"""Modules outside the ``__all__`` rule that the reference pages render by name."""


def _assigns_all(path: Path) -> bool:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            targets = [element for target in node.targets for element in (target.elts if isinstance(target, ast.Tuple) else [target])]
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            continue

        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            return True

    return False


def _dotted(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def _package(init: Path) -> str:
    return ".".join(init.parent.relative_to(SRC).parts)


DIRECTIVES = [match["name"] for page in sorted(REFERENCE.rglob("*.md")) for match in DIRECTIVE.finditer(page.read_text(encoding="utf-8"))]
"""Every directive across the reference pages, in page order."""

DOCUMENTED = set(DIRECTIVES)
"""The modules the reference pages render."""

PACKAGES = sorted(_package(init) for init in PACKAGE.rglob("__init__.py") if init.parent != PACKAGE and _assigns_all(init))
"""Every femforge subpackage whose import exposes an ``__all__``."""

ROOT_MODULES = sorted(_dotted(module) for module in PACKAGE.glob("*.py") if module.name != "__init__.py" and _assigns_all(module))
"""Every femforge root module that declares an ``__all__``."""

WORKBENCH_MODULES = sorted(f"femforge_workbench.{info.name}" for info in pkgutil.iter_modules([str(WORKBENCH)]) if not info.name.startswith("_"))
"""Every top-level module and package of femforge_workbench."""

LAZY_PACKAGES = sorted(_package(stub) for stub in PACKAGE.rglob("__init__.pyi") if stub.parent != PACKAGE)
"""The lazy subpackages whose ``__all__`` comes from their stub."""


def test_package_scan_sees_the_stub_and_nested_packages() -> None:
    assert set(LAZY_PACKAGES) <= set(PACKAGES)
    assert "femforge.elmer.config" in PACKAGES


def test_workbench_scan_sees_the_subpackages() -> None:
    assert {"femforge_workbench.panels", "femforge_workbench.widgets"} <= set(WORKBENCH_MODULES)


@pytest.mark.parametrize("package", PACKAGES)
def test_package_with_all_has_a_directive(package: str) -> None:
    assert package in DOCUMENTED


@pytest.mark.parametrize("module", ROOT_MODULES)
def test_root_module_with_all_has_a_directive(module: str) -> None:
    assert module in DOCUMENTED


@pytest.mark.parametrize("module", PLAN_EXTRAS)
def test_plan_named_module_has_a_directive(module: str) -> None:
    assert module in DOCUMENTED


@pytest.mark.parametrize("module", WORKBENCH_MODULES)
def test_workbench_module_has_a_directive(module: str) -> None:
    assert module in DOCUMENTED


def test_no_module_is_rendered_twice() -> None:
    assert [name for name, count in Counter(DIRECTIVES).items() if count > 1] == []


@pytest.mark.parametrize("module", sorted(DOCUMENTED))
def test_directive_resolves(module: str) -> None:
    assert importlib.util.find_spec(module) is not None
