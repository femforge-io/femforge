"""Every name a package stub or a public ``__all__`` lists resolves and is documented, ``dir()`` of each lazy package equals its stub, and the workbench bridge fails helpfully."""

import ast
import dataclasses
import enum
import importlib
import importlib.util
import inspect
import os
import pkgutil
import re
import subprocess
import sys
import typing
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import NamedTuple, cast

import pytest
from pydantic import BaseModel

import femforge
import femforge_workbench
from femforge.common.introspect import attribute_docstrings


SRC = Path(femforge.__file__).parents[1]
"""The source tree under test."""

STUBS = sorted((SRC / "femforge").rglob("__init__.pyi"))
"""Every lazy package stub; each lists the names its package exposes."""

INSTALL_HINT = 'pip install "femforge[workbench]"'
"""The install command the bridge names when the workbench package is missing."""

PUBLIC_ROOTS = ("femforge", "femforge_workbench")
"""The packages whose public modules the docstring checks walk."""

ARGS_HEADER = re.compile(r"^(?P<indent>\s*)Args:\s*$")
"""The header line of a Google ``Args:`` block."""

ARG_ENTRY = re.compile(r"^\*{0,2}(?P<name>\w+):")
"""One entry of an ``Args:`` block: the parameter name without a type, since the annotation carries it."""

CURATED_EXPORTS = ("femforge.materials", "femforge.workbench", "femforge.artifacts", "femforge.geometry", "femforge.meshing", "femforge.optimize", "femforge.validation", "femforge.viz")
"""The modules whose ``__all__`` joins the stubs on the curated surface: the materials, the workbench bridge and the eager packages the root exposes."""


class StubEntry(NamedTuple):
    """One name a package stub lists and the submodule it comes from."""

    package: str
    """The lazy package, such as ``femforge.run``."""

    name: str
    """The name the package exposes."""

    source: str | None
    """The submodule relative to the package that defines ``name``; None when ``name`` is itself a submodule."""


def _package(stub: Path) -> str:
    return ".".join(stub.parent.relative_to(SRC).parts)


def _entries(stub: Path) -> list[StubEntry]:
    imports = [node for node in ast.parse(stub.read_text(encoding="utf-8")).body if isinstance(node, ast.ImportFrom)]

    return [StubEntry(_package(stub), alias.name, node.module) for node in imports for alias in node.names]


ENTRIES = [entry for stub in STUBS for entry in _entries(stub)]


def test_every_lazy_package_has_a_stub() -> None:
    packages = [_package(stub) for stub in STUBS]

    assert packages == ["femforge", "femforge.common", "femforge.elmer", "femforge.physical", "femforge.run", "femforge.telemetry"]


@pytest.mark.parametrize("entry", ENTRIES, ids=[f"{entry.package}.{entry.name}" for entry in ENTRIES])
def test_a_stub_name_resolves_to_its_source(entry: StubEntry) -> None:
    package = importlib.import_module(entry.package)

    exposed = cast("object", getattr(package, entry.name))

    if entry.source is None:
        assert exposed is importlib.import_module(f"{entry.package}.{entry.name}")
    else:
        assert exposed is getattr(importlib.import_module(f"{entry.package}.{entry.source}"), entry.name)


@pytest.mark.parametrize("stub", STUBS, ids=_package)
def test_dir_of_a_lazy_package_equals_its_stub(stub: Path) -> None:
    package = importlib.import_module(_package(stub))
    names = sorted(entry.name for entry in _entries(stub))

    assert dir(package) == names
    assert cast("list[str]", package.__all__) == names


@pytest.mark.parametrize("module_name", ["femforge.materials", "femforge.workbench"])
def test_every_name_in_a_public_all_resolves(module_name: str) -> None:
    module = importlib.import_module(module_name)

    exported = cast("list[str]", module.__all__)

    missing = [name for name in exported if not hasattr(module, name)]

    assert missing == []


def test_the_bridge_names_the_install_command_without_the_workbench(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "femforge_workbench", None)
    for name in [name for name in sys.modules if name.startswith("femforge_workbench.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.delitem(sys.modules, "femforge.workbench", raising=False)

    with pytest.raises(ImportError) as raised:
        _ = importlib.import_module("femforge.workbench")

    assert INSTALL_HINT in str(raised.value)


def test_the_bridge_re_raises_a_module_missing_inside_the_workbench(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "femforge_workbench.app", None)
    monkeypatch.delitem(sys.modules, "femforge.workbench", raising=False)

    with pytest.raises(ModuleNotFoundError) as raised:
        _ = importlib.import_module("femforge.workbench")

    assert raised.value.name == "femforge_workbench.app"
    assert INSTALL_HINT not in str(raised.value)


def test_the_bridge_lets_a_missing_workbench_dependency_through_unchanged() -> None:
    workbench_src = Path(femforge_workbench.__file__).parents[1]
    env = {name: value for name, value in os.environ.items() if not name.startswith(("COV_CORE_", "COVERAGE_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), str(workbench_src)])

    done = subprocess.run(
        [sys.executable, "-c", "import sys; sys.modules['trame'] = None; import femforge.workbench"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    error = done.stderr.strip().splitlines()[-1]

    assert done.returncode != 0
    assert error.startswith("ModuleNotFoundError") and "trame" in error
    assert INSTALL_HINT not in done.stderr


class Target(NamedTuple):
    """One documented thing: a module, class, function or bound value, named by where it is defined."""

    reference: str
    """The defining module and qualified name; a test id that ``-k`` selects by package."""

    value: object
    """The module, class, function or value itself."""

    module: str
    """The module whose source binds the name."""

    name: str
    """The name that module binds."""


class CallableTarget(NamedTuple):
    """A public function, method or hand-written constructor."""

    reference: str
    """The defining module and qualified name."""

    function: Callable[..., object]
    """The function as its class or module holds it."""

    bound: bool
    """Whether its first parameter is the instance or class it binds to."""


def _ours(value: object) -> bool:
    module = getattr(value, "__module__", None)

    return isinstance(module, str) and module.split(".")[0] in PUBLIC_ROOTS


def _defined(value: type[object] | Callable[..., object]) -> Target:
    module = cast("str", value.__module__)

    return Target(f"{module}.{value.__qualname__}", value, module, value.__qualname__)


def _statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Module-level statements, those inside ``if`` and ``try`` blocks included."""
    for statement in body:
        yield statement

        if isinstance(statement, ast.If):
            yield from _statements(statement.body)
            yield from _statements(statement.orelse)

        if isinstance(statement, ast.Try):
            yield from _statements(statement.body)


def _binding(module_name: str, name: str) -> tuple[str, str]:
    """The module whose source binds ``name`` and the name it binds there, following from-imports."""
    module = importlib.import_module(module_name)
    tree = ast.parse(inspect.getsource(module))

    for statement in _statements(tree.body):
        if not isinstance(statement, ast.ImportFrom):
            continue

        for alias in statement.names:
            if (alias.asname or alias.name) != name:
                continue

            relative = "." * statement.level + (statement.module or "")
            source = importlib.util.resolve_name(relative, module.__package__) if statement.level else relative

            return _binding(source, alias.name)

    return module_name, name


def _target(owner: str, name: str, value: object) -> Target:
    """The documentation target of ``name``, which module ``owner`` imports or defines."""
    if inspect.ismodule(value):
        return Target(value.__name__, value, value.__name__, name)

    if (inspect.isclass(value) or inspect.isfunction(value)) and _ours(value):
        return _defined(value)

    module, bound = _binding(owner, name)

    return Target(f"{module}.{bound}", value, module, bound)


def _curated() -> list[Target]:
    """Every name the package stubs and the ``CURATED_EXPORTS`` modules expose."""
    targets: list[Target] = []
    for entry in ENTRIES:
        value = cast("object", getattr(importlib.import_module(entry.package), entry.name))
        owner = entry.package if entry.source is None else f"{entry.package}.{entry.source}"
        targets.append(_target(owner, entry.name, value))

    for module_name in CURATED_EXPORTS:
        module = importlib.import_module(module_name)
        for name in cast("list[str]", module.__all__):
            targets.append(_target(module_name, name, cast("object", getattr(module, name))))

    return targets


def _public_modules() -> list[ModuleType]:
    modules: list[ModuleType] = []
    for root in PUBLIC_ROOTS:
        package = importlib.import_module(root)
        modules.append(package)

        for info in pkgutil.walk_packages(package.__path__, prefix=f"{root}."):
            if any(part.startswith("_") for part in info.name.split(".")):
                continue

            modules.append(importlib.import_module(info.name))

    return modules


def _definitions(module: ModuleType) -> list[type[object] | Callable[..., object]]:
    """The public classes and functions a module defines itself."""
    namespace = cast("dict[str, object]", vars(module))

    return [value for name, value in namespace.items() if not name.startswith("_") and (inspect.isclass(value) or inspect.isfunction(value)) and getattr(value, "__module__", None) == module.__name__]


def _record(cls: type[object]) -> bool:
    return issubclass(cls, (BaseModel, enum.Enum)) or dataclasses.is_dataclass(cls) or (tuple in cls.__mro__ and hasattr(cls, "_fields")) or typing.is_typeddict(cls)


def _declared_fields(owner: type[object]) -> list[str]:
    """The public fields or members a record class declares itself."""
    own = inspect.get_annotations(owner)

    if issubclass(owner, BaseModel):
        declared = [name for name in owner.model_fields if name in own]
    elif issubclass(owner, enum.Enum):
        declared = [name for name, member in owner.__members__.items() if member.name == name]
    elif dataclasses.is_dataclass(owner):
        declared = [field.name for field in dataclasses.fields(owner) if field.name in own]
    else:
        declared = list(own)

    return [name for name in declared if not name.startswith("_")]


def _undocumented_fields(owner: type[object]) -> list[str]:
    declared = _declared_fields(owner)

    if issubclass(owner, BaseModel):
        return [name for name in declared if not owner.model_fields[name].description]

    documented = attribute_docstrings(owner)

    return [name for name in declared if name not in documented]


def _callables(cls: type[object]) -> Iterator[CallableTarget]:
    """The hand-written constructors and the public methods a class defines itself."""
    namespace = cast("dict[str, object]", vars(cls))
    for name, member in namespace.items():
        if name.startswith("_") and name not in ("__init__", "__new__"):
            continue

        if (name == "__init__" and dataclasses.is_dataclass(cls)) or (name == "__new__" and tuple in cls.__mro__):
            continue

        function = cast("object", member.__func__) if isinstance(member, (classmethod, staticmethod)) else member
        bound = name == "__new__" or not isinstance(member, staticmethod)
        if inspect.isfunction(function) and _ours(function):
            yield CallableTarget(f"{function.__module__}.{function.__qualname__}", function, bound)


def _parameters(target: CallableTarget) -> list[str]:
    names = list(inspect.signature(target.function).parameters)

    return names[1:] if target.bound else names


def _documented_arguments(docstring: str | None) -> list[str] | None:
    """The parameter names a Google ``Args:`` block lists, or None when the docstring has none.

    A line that is not one typeless ``name:`` entry, such as a typed entry or a wrapped one, comes back verbatim so the comparison fails on it.
    """
    lines = inspect.cleandoc(docstring or "").splitlines()
    for index, line in enumerate(lines):
        header = ARGS_HEADER.match(line)
        if header is None:
            continue

        indent = len(header.group("indent"))
        names: list[str] = []
        entry_indent: int | None = None
        for following in lines[index + 1 :]:
            depth = len(following) - len(following.lstrip())
            if not following.strip() or depth <= indent:
                break

            entry_indent = depth if entry_indent is None else entry_indent
            entry = ARG_ENTRY.match(following.strip())
            names.append(entry.group("name") if depth == entry_indent and entry is not None else following.strip())

        return names

    return None


def _binding_documented(module_name: str, name: str) -> bool:
    """Whether ``name`` carries an attribute docstring, or a docstring on its class or function definition, in that module."""
    module = importlib.import_module(module_name)
    if name in attribute_docstrings(module):
        return True

    tree = ast.parse(inspect.getsource(module))
    definitions = [statement for statement in _statements(tree.body) if isinstance(statement, (ast.ClassDef, ast.FunctionDef)) and statement.name == name]

    return any(ast.get_docstring(definition) for definition in definitions)


def _docstring_targets() -> list[Target]:
    targets = {target.reference: target for target in CURATED}
    for module in PUBLIC_MODULES:
        for value in _definitions(module):
            target = _defined(value)
            targets.setdefault(target.reference, target)

    return [targets[reference] for reference in sorted(targets)]


def _field_owners() -> list[type[object]]:
    """Every class that declares a field or member of a curated record, bases included."""
    owners: dict[str, type[object]] = {}
    for target in CURATED:
        if not inspect.isclass(target.value) or not _ours(target.value):
            continue

        for base in target.value.__mro__:
            if _ours(base) and _record(base):
                owners[f"{base.__module__}.{base.__qualname__}"] = base

    return [owners[reference] for reference in sorted(owners)]


def _callable_targets() -> list[CallableTarget]:
    """Every public callable with three or more parameters, the curated classes' bases included."""
    curated_classes = [target.value for target in CURATED if inspect.isclass(target.value) and _ours(target.value)]
    classes: list[type[object]] = [base for cls in curated_classes for base in cls.__mro__ if _ours(base)]
    functions: list[Callable[..., object]] = [target.value for target in CURATED if inspect.isfunction(target.value) and _ours(target.value)]
    for module in PUBLIC_MODULES:
        for value in _definitions(module):
            if inspect.isclass(value):
                classes.append(value)
            else:
                functions.append(value)

    targets: dict[str, CallableTarget] = {}
    for function in functions:
        reference = f"{function.__module__}.{function.__qualname__}"
        targets.setdefault(reference, CallableTarget(reference, function, bound=False))

    for cls in classes:
        for target in _callables(cls):
            targets.setdefault(target.reference, target)

    return [targets[reference] for reference in sorted(targets) if len(_parameters(targets[reference])) >= 3]


class StaticCallable(NamedTuple):
    """A public function or method defined only for the type checker, read from source."""

    reference: str
    """The module and qualified name."""

    node: ast.FunctionDef
    """The definition in the module's source."""

    bound: bool
    """Whether its first parameter is the instance or class it binds to."""


def _decorated(node: ast.FunctionDef, name: str) -> bool:
    return any((isinstance(decorator, ast.Name) and decorator.id == name) or (isinstance(decorator, ast.Attribute) and decorator.attr == name) for decorator in node.decorator_list)


def _static_parameters(target: StaticCallable) -> list[str]:
    arguments = target.node.args
    names = [argument.arg for argument in [*arguments.posonlyargs, *arguments.args]]
    names += [arguments.vararg.arg] if arguments.vararg is not None else []
    names += [argument.arg for argument in arguments.kwonlyargs]
    names += [arguments.kwarg.arg] if arguments.kwarg is not None else []

    return names[1:] if target.bound else names


def _type_checking_callables() -> list[StaticCallable]:
    """Every public function and method defined under ``if TYPE_CHECKING:`` with three or more parameters, overloads aside."""
    found: list[StaticCallable] = []
    for module in PUBLIC_MODULES:
        tree = ast.parse(inspect.getsource(module))
        guards = [statement for statement in _statements(tree.body) if isinstance(statement, ast.If) and isinstance(statement.test, (ast.Name, ast.Attribute))]
        blocks = [guard.body for guard in guards if ast.unparse(guard.test).endswith("TYPE_CHECKING")]
        for statement in (inner for block in blocks for inner in block):
            if isinstance(statement, ast.FunctionDef) and not statement.name.startswith("_"):
                found.append(StaticCallable(f"{module.__name__}.{statement.name}", statement, bound=False))

            if not isinstance(statement, ast.ClassDef) or statement.name.startswith("_"):
                continue

            for member in statement.body:
                public = isinstance(member, ast.FunctionDef) and (not member.name.startswith("_") or member.name in ("__init__", "__new__"))
                if isinstance(member, ast.FunctionDef) and public and not _decorated(member, "overload"):
                    found.append(StaticCallable(f"{module.__name__}.{statement.name}.{member.name}", member, bound=not _decorated(member, "staticmethod")))

    return [target for target in found if len(_static_parameters(target)) >= 3]


CURATED = _curated()
PUBLIC_MODULES = _public_modules()
DOCSTRING_TARGETS = _docstring_targets()
FIELD_OWNERS = _field_owners()
CALLABLE_TARGETS = _callable_targets()
STATIC_CALLABLE_TARGETS = _type_checking_callables()

@pytest.mark.parametrize("target", DOCSTRING_TARGETS, ids=[target.reference for target in DOCSTRING_TARGETS])
def test_a_public_name_carries_its_own_docstring(target: Target) -> None:
    value = target.value
    own_docstring = inspect.ismodule(value) or ((inspect.isclass(value) or inspect.isfunction(value)) and _ours(value))

    if inspect.isclass(value) and own_docstring:
        documented = bool(cast("dict[str, object]", vars(value)).get("__doc__"))
    elif own_docstring:
        documented = bool(getattr(value, "__doc__", None))
    else:
        documented = _binding_documented(target.module, target.name)

    assert documented, f"{target.reference} has no docstring of its own"


@pytest.mark.parametrize("owner", FIELD_OWNERS, ids=[f"{owner.__module__}.{owner.__qualname__}" for owner in FIELD_OWNERS])
def test_a_curated_record_documents_every_field(owner: type[object]) -> None:
    assert _undocumented_fields(owner) == []


@pytest.mark.parametrize("target", CALLABLE_TARGETS, ids=[target.reference for target in CALLABLE_TARGETS])
def test_a_public_callable_documents_its_arguments(target: CallableTarget) -> None:
    assert _documented_arguments(target.function.__doc__) == _parameters(target)


@pytest.mark.parametrize("target", STATIC_CALLABLE_TARGETS, ids=[target.reference for target in STATIC_CALLABLE_TARGETS])
def test_a_type_checking_callable_documents_its_arguments(target: StaticCallable) -> None:
    assert _documented_arguments(ast.get_docstring(target.node)) == _static_parameters(target)
